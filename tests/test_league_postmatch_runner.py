from __future__ import annotations

import fcntl
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import worldcup.sources.league_fotmob_lineups as fotmob_source
from worldcup.league_postmatch_notifications import (
    LeaguePostmatchNotificationOutbox,
    build_threshold_events,
)
from worldcup.league_closing import select_league_closings
from worldcup.league_live_probe import evaluate_league_probe_bundle
from worldcup.league_postmatch import build_league_postmatch
from worldcup.league_postmatch_runner import _saved_sample_matches, main, run_league_postmatch
from worldcup.competitions import get_competition
from worldcup.league_result_evidence import build_result_contract_evidence
from worldcup.league_statistics import build_league_statistics
from worldcup.league_team_identity import accepted_league_team_identity_registry


EPL = "epl_2026_27"
LALIGA = "laliga_2026_27"
BRAZIL = "serie_a_brazil_2026"
NOW = datetime(2026, 8, 29, 0, 0, tzinfo=timezone.utc)


def _forbidden(*_args: object, **_kwargs: object) -> dict:
    raise AssertionError("external dependency must not be called")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fotmob_evidence_path(root: Path, competition_id: str) -> Path:
    return (
        root
        / "data/local/leagues"
        / competition_id
        / "providers/fotmob/result_contract_evidence.json"
    )


def _evidence(root: Path, competition_id: str) -> dict:
    sample_path = Path("data/probe/leagues/results") / f"{competition_id}.json"
    sample_bytes = f"{competition_id}:saved-sample".encode()
    absolute_sample = root / sample_path
    absolute_sample.parent.mkdir(parents=True, exist_ok=True)
    absolute_sample.write_bytes(sample_bytes)
    evidence = build_result_contract_evidence(
        competition_id=competition_id,
        sport_key=get_competition(competition_id).theoddsapi_sport_key,
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference=hashlib.sha256(sample_bytes).hexdigest(),
        provider="fotmob",
        sample_path=sample_path.as_posix(),
    )
    return evidence


def _acceptance(evidence_by_competition: dict[str, dict]) -> dict:
    return _producer_acceptance(evidence_by_competition)


def _producer_acceptance(evidence_by_competition: dict[str, dict]) -> dict:
    registry = accepted_league_team_identity_registry()
    rows = {}
    for competition_id, evidence in evidence_by_competition.items():
        home, away, _home_canonical, _away_canonical = _teams(competition_id)
        rows[competition_id] = evaluate_league_probe_bundle(
            {
                "schema_version": 1,
                "competition_id": competition_id,
                "sport_key": get_competition(competition_id).theoddsapi_sport_key,
                "odds": [{
                    "id": f"{competition_id}-sample-event",
                    "sport_key": get_competition(competition_id).theoddsapi_sport_key,
                    "home_team": home,
                    "away_team": away,
                    "bookmakers": [{
                        "key": "sample-book",
                        "markets": [{"key": "h2h", "outcomes": [
                            {"name": home, "price": 2.0},
                            {"name": "Draw", "price": 3.2},
                            {"name": away, "price": 3.8},
                        ]}],
                    }],
                }],
            },
            identity_registry=registry,
            result_contract_evidence=evidence,
        )
    return {"schema_version": 1, "competitions": rows}


def _teams(competition_id: str) -> tuple[str, str, str, str]:
    if competition_id == EPL:
        return "Arsenal", "Chelsea", "arsenal", "chelsea"
    if competition_id == BRAZIL:
        return "Bahia", "Botafogo", "bahia", "botafogo"
    return "Barcelona", "Real Madrid", "barcelona", "real_madrid"


def _snapshot(competition_id: str, event_id: str) -> dict:
    home, away, home_canonical, away_canonical = _teams(competition_id)
    return {
        "snapshot_id": f"{competition_id}-snapshot-1",
        "snapshot_at": "2026-08-28T18:59:00+00:00",
        "competition": {"id": competition_id},
        "matches": [{
            "source_event_id": event_id,
            "kickoff_at_utc": "2026-08-28T19:00:00+00:00",
            "home_team": home,
            "away_team": away,
            "home_canonical": home_canonical,
            "away_canonical": away_canonical,
            "fixture_status": "SCHEDULED",
            "match_decision": {
                "schema_version": 2,
                "label": "MATCH_PICK",
                "market": "1X2",
                "selection": "home",
            },
        }],
    }


def _setup(root: Path, competitions: tuple[str, ...] = (EPL,)) -> None:
    evidence_by_competition = {competition_id: _evidence(root, competition_id) for competition_id in competitions}
    _write_json(root / "data/local/leagues/acceptance.json", _acceptance(evidence_by_competition))
    for index, competition_id in enumerate(competitions, start=1):
        partition = root / "data/local/leagues" / competition_id
        _write_json(
            _fotmob_evidence_path(root, competition_id),
            evidence_by_competition[competition_id],
        )
        _write_json(partition / "history/snapshot-1.json", _snapshot(competition_id, str(1000 + index)))


def _status(*, finished: bool, score: str = "2 - 1") -> dict:
    return {
        "utcTime": "2026-08-28T19:00:00Z",
        "started": finished,
        "cancelled": False,
        "finished": finished,
        "scoreStr": score,
        "reason": {"short": "FT" if finished else "NS", "long": "Full-Time"},
        **({
            "halfs": {
                "firstExtraHalfStarted": "",
                "secondExtraHalfStarted": "",
            },
            "whoLostOnPenalties": None,
            "whoLostOnAggregated": "",
        } if finished else {}),
    }


def _calendar(competition_id: str, event_id: str, *, finished: bool = True, score: str = "2 - 1") -> dict:
    home, away, _home_canonical, _away_canonical = _teams(competition_id)
    league_ids = {EPL: 47, LALIGA: 87, BRAZIL: 268}
    return {"leagues": [{
        "id": league_ids[competition_id],
        "name": "safe",
        "matches": [{
            "id": int(event_id),
            "home": {"name": home},
            "away": {"name": away},
            "status": _status(finished=finished, score=score),
        }],
    }], "token": "provider-secret-must-not-escape"}


def _details(competition_id: str, event_id: str, *, finished: bool = True, score: str = "2 - 1") -> dict:
    home, away, _home_canonical, _away_canonical = _teams(competition_id)
    league_ids = {EPL: 47, LALIGA: 87, BRAZIL: 268}
    return {
        "general": {
            "matchId": int(event_id),
            "leagueId": league_ids[competition_id],
            "matchTimeUTC": "Fri, Aug 28, 2026, 19:00 UTC",
            "matchTimeUTCDate": "2026-08-28T19:00:00Z",
            "homeTeam": {"name": home},
            "awayTeam": {"name": away},
        },
        "header": {"status": _status(finished=finished, score=score)},
        "raw_response": "provider-secret-must-not-escape",
    }


def _fetchers(events: list[str], *, failures: set[str] | None = None):
    failures = failures or set()

    def calendar_fetcher(competition_id: str, _date: str) -> dict:
        events.append(f"calendar:{competition_id}")
        if competition_id in failures:
            raise RuntimeError("provider-token=do-not-leak")
        event_id = "1001" if competition_id == EPL else "1002"
        return _calendar(competition_id, event_id)

    def detail_fetcher(competition_id: str, event_id: str) -> dict:
        events.append(f"details:{competition_id}")
        return _details(competition_id, event_id)

    return calendar_fetcher, detail_fetcher


def test_default_dry_run_has_zero_external_or_write_side_effects():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = run_league_postmatch(root, calendar_fetcher=_forbidden, detail_fetcher=_forbidden)

        assert result["mode"] == "dry_run"
        assert result["safety"] == {
            "read_env": False,
            "called_fotmob": False,
            "wrote": False,
            "notified": False,
        }
        assert list(root.rglob("*")) == []


def test_partial_live_flag_matrix_is_blocked_before_every_dependency_or_lock_write():
    for live, write, notify in (
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, False, True),
        (False, True, True),
    ):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_league_postmatch(
                root,
                live=live,
                write=write,
                notify=notify,
                calendar_fetcher=_forbidden,
                detail_fetcher=_forbidden,
                notifier=_forbidden,
            )
            assert result["status"] == "blocked"
            assert result["reason"] == "live_write_flags_required"
            assert result["safety"] == {
                "read_env": False,
                "called_fotmob": False,
                "wrote": False,
                "notified": False,
            }
            assert list(root.rglob("*")) == []


def test_live_requires_acceptance_fingerprint_bound_fotmob_evidence():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        evidence_path = _fotmob_evidence_path(root, EPL)
        evidence = json.loads(evidence_path.read_text())
        evidence["source_reference"] = "0" * 64
        _write_json(evidence_path, evidence)

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=_forbidden,
            detail_fetcher=_forbidden,
        )

        assert result["competitions"][EPL] == {
            "status": "blocked",
            "reason": "result_contract_evidence_invalid",
        }
        assert result["safety"]["called_fotmob"] is False
        assert not (root / f"data/local/leagues/{EPL}/results.json").exists()


def test_live_rejects_wrong_and_malformed_expected_sample_sha_before_provider():
    """A valid-looking evidence fingerprint must not bypass the actual saved-byte digest comparison."""
    for source_reference in ("0" * 64, "not-a-sha256"):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _setup(root)
            evidence = build_result_contract_evidence(
                competition_id=EPL,
                sport_key="soccer_epl",
                provider_schema="fotmob_league_results_v1",
                score_scope="football_90min",
                source_reference=source_reference,
                provider="fotmob",
                sample_path=f"data/probe/leagues/results/{EPL}.json",
            )
            if source_reference == "not-a-sha256":
                evidence["verified"] = True
            _write_json(_fotmob_evidence_path(root, EPL), evidence)
            acceptance = json.loads(
                (root / "data/local/leagues/acceptance.json").read_text(encoding="utf-8")
            )
            acceptance["competitions"][EPL]["fingerprints"]["result_contract"] = evidence[
                "fingerprint"
            ]
            _write_json(root / "data/local/leagues/acceptance.json", acceptance)

            result = run_league_postmatch(
                root,
                live=True,
                write=True,
                now=NOW,
                calendar_fetcher=_forbidden,
                detail_fetcher=_forbidden,
            )

            assert result["competitions"][EPL] == {
                "status": "blocked",
                "reason": "result_contract_evidence_invalid",
            }
            assert result["safety"]["called_fotmob"] is False


def test_runner_saved_sample_check_fails_closed_for_root_symlink_loop():
    """A root resolution loop must remain a boolean evidence failure at the runner boundary."""
    with TemporaryDirectory() as tmp:
        loop = Path(tmp) / "loop"
        loop.symlink_to("loop", target_is_directory=True)
        evidence = build_result_contract_evidence(
            competition_id=EPL,
            sport_key="soccer_epl",
            provider_schema="fotmob_league_results_v1",
            score_scope="football_90min",
            source_reference="a" * 64,
            provider="fotmob",
            sample_path="data/probe/leagues/results/epl/sample.json",
        )

        assert _saved_sample_matches(loop, evidence) is False


def test_fotmob_runner_ignores_generic_and_legacy_provider_evidence_paths():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        evidence = _evidence(root, EPL)
        _write_json(root / "data/local/leagues/acceptance.json", _acceptance({EPL: evidence}))
        _write_json(
            root / f"data/local/leagues/{EPL}/history/snapshot-1.json",
            _snapshot(EPL, "1001"),
        )
        _write_json(
            root / f"data/local/leagues/{EPL}/result_contract_evidence.json",
            evidence,
        )
        _write_json(
            root
            / f"data/local/leagues/legacy_theoddsapi/{EPL}/result_contract_evidence.json",
            evidence,
        )
        provider_events: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(provider_events)

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
        )

        assert result["competitions"][EPL] == {
            "status": "blocked",
            "reason": "result_contract_evidence_invalid",
        }
        assert provider_events == []
        assert result["safety"]["called_fotmob"] is False


def test_production_acceptance_evidence_contract_activates_runner_without_manual_hashes():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        sample_path = Path("data/probe/leagues/results/epl-finished.json")
        sample_bytes = b"sanitized-fotmob-finished-sample"
        absolute_sample = root / sample_path
        absolute_sample.parent.mkdir(parents=True)
        absolute_sample.write_bytes(sample_bytes)
        evidence = build_result_contract_evidence(
            competition_id=EPL,
            sport_key="soccer_epl",
            provider_schema="fotmob_league_results_v1",
            score_scope="football_90min",
            source_reference=hashlib.sha256(sample_bytes).hexdigest(),
            provider="fotmob",
            sample_path=sample_path.as_posix(),
        )
        acceptance = _producer_acceptance({EPL: evidence})
        assert acceptance["competitions"][EPL]["state"] == "active"
        assert acceptance["competitions"][EPL]["fingerprints"]["result_contract"] == evidence["fingerprint"]
        partition = root / f"data/local/leagues/{EPL}"
        _write_json(root / "data/local/leagues/acceptance.json", acceptance)
        _write_json(_fotmob_evidence_path(root, EPL), evidence)
        _write_json(partition / "history/snapshot-1.json", _snapshot(EPL, "1001"))
        events: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(events)

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
        )

        assert result["status"] == "settled"
        assert events == [f"calendar:{EPL}", f"details:{EPL}"]


def test_live_binds_acceptance_to_saved_sample_bytes_and_current_identity_registry():
    mutations = (
        "tampered_sample",
        "missing_sample",
        "path_escape",
        "traversal",
        "cache_path",
        "symlinked_probe_directory",
        "symlinked_component",
        "symlinked_sample_file",
        "identity_mismatch",
    )
    for mutation in mutations:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _setup(root)
            evidence_path = _fotmob_evidence_path(root, EPL)
            evidence = json.loads(evidence_path.read_text())
            if mutation == "tampered_sample":
                (root / evidence["sample_path"]).write_bytes(b"tampered-provider-bytes")
            elif mutation == "missing_sample":
                (root / evidence["sample_path"]).unlink()
            elif mutation == "path_escape":
                outside = root / "outside-allowed-probe-boundary.json"
                outside.write_bytes(f"{EPL}:saved-sample".encode())
                evidence["sample_path"] = outside.name
                _write_json(evidence_path, evidence)
            elif mutation == "traversal":
                evidence["sample_path"] = "data/probe/../outside.json"
                _write_json(evidence_path, evidence)
            elif mutation == "cache_path":
                cache_sample = root / "data/cache/leagues/results/epl.json"
                cache_sample.parent.mkdir(parents=True)
                cache_sample.write_bytes(f"{EPL}:saved-sample".encode())
                evidence["sample_path"] = "data/cache/leagues/results/epl.json"
                _write_json(evidence_path, evidence)
            elif mutation == "symlinked_probe_directory":
                probe = root / "data/probe"
                real_probe = root / "data/probe-real"
                probe.rename(real_probe)
                probe.symlink_to(real_probe, target_is_directory=True)
            elif mutation == "symlinked_component":
                results = root / "data/probe/leagues/results"
                real_results = root / "data/probe/leagues/results-real"
                results.rename(real_results)
                results.symlink_to(real_results, target_is_directory=True)
            elif mutation == "symlinked_sample_file":
                sample = root / evidence["sample_path"]
                target = sample.with_name("accepted-bytes.json")
                sample.rename(target)
                sample.symlink_to(target.name)
            else:
                acceptance_path = root / "data/local/leagues/acceptance.json"
                acceptance = json.loads(acceptance_path.read_text())
                acceptance["competitions"][EPL]["fingerprints"]["team_identity"] = "0" * 64
                _write_json(acceptance_path, acceptance)

            result = run_league_postmatch(
                root,
                live=True,
                write=True,
                now=NOW,
                calendar_fetcher=_forbidden,
                detail_fetcher=_forbidden,
            )

            assert result["competitions"][EPL] == {
                "status": "blocked",
                "reason": "result_contract_evidence_invalid"
                if mutation != "identity_mismatch"
                else "team_identity_evidence_invalid",
            }
            assert result["safety"]["called_fotmob"] is False
            assert not (root / f"data/local/leagues/{EPL}/results.json").exists()


def test_dry_run_surfaces_malformed_acceptance_and_history_without_side_effects():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        acceptance_path = root / "data/local/leagues/acceptance.json"
        _write_json(acceptance_path, {"malformed": True})
        before = acceptance_path.read_bytes()

        result = run_league_postmatch(root, now=NOW)

        assert result["status"] == "blocked"
        assert result["reason"] == "local_inputs_invalid"
        assert result["errors"] == [{"scope": "acceptance", "reason": "acceptance_invalid"}]
        assert result["safety"] == {
            "read_env": False,
            "called_fotmob": False,
            "wrote": False,
            "notified": False,
        }
        assert acceptance_path.read_bytes() == before

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        history_path = root / f"data/local/leagues/{EPL}/history/snapshot-1.json"
        history_path.write_text("not-json", encoding="utf-8")
        before = history_path.read_bytes()

        result = run_league_postmatch(root, now=NOW)

        assert result["status"] == "blocked"
        assert result["competitions"][EPL] == {"status": "blocked", "reason": "history_invalid"}
        assert result["safety"]["wrote"] is False
        assert history_path.read_bytes() == before


def test_nonblocking_lock_contention_exits_before_acceptance_or_provider_access():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        lock_path = root / "data/local/leagues/league_postmatch.lock"
        lock_path.parent.mkdir(parents=True)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run_league_postmatch(
                root,
                live=True,
                write=True,
                calendar_fetcher=_forbidden,
                detail_fetcher=_forbidden,
            )

        assert result["status"] == "locked"
        assert result["safety"]["called_fotmob"] is False


def test_live_commits_results_before_settlement_state_and_notification():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        provider_order: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(provider_order)
        stage_order: list[str] = []

        import worldcup.league_postmatch_runner as runner

        original_result_merge = runner.LeagueResultStore.merge
        original_closing_merge = runner.LeagueClosingStore.merge
        original_atomic_write = runner._write_json_atomic
        original_deliver = runner.LeaguePostmatchNotificationOutbox.deliver

        def result_merge(store, payload):
            stage_order.append("results")
            return original_result_merge(store, payload)

        def closing_merge(store, payload):
            stage_order.append("closing")
            return original_closing_merge(store, payload)

        def atomic_write(path, payload):
            labels = {
                "postmatch.json": "postmatch",
                "postmatch_statistics.json": "statistics",
                "postmatch_state.json": "state",
            }
            if path.name in labels:
                stage_order.append(labels[path.name])
            return original_atomic_write(path, payload)

        def deliver(outbox, event, **kwargs):
            stage_order.append("notification")
            return original_deliver(outbox, event, **kwargs)

        def notifier(_content: str, summary: str) -> dict:
            assert summary
            return {"status": "sent", "raw": "must-not-escape"}

        with (
            patch.object(runner.LeagueResultStore, "merge", result_merge),
            patch.object(runner.LeagueClosingStore, "merge", closing_merge),
            patch.object(runner, "_write_json_atomic", atomic_write),
            patch.object(runner.LeaguePostmatchNotificationOutbox, "deliver", deliver),
        ):
            result = run_league_postmatch(
                root,
                live=True,
                write=True,
                notify=True,
                now=NOW,
                calendar_fetcher=calendar_fetcher,
                detail_fetcher=detail_fetcher,
                notifier=notifier,
            )

        assert result["status"] == "settled"
        assert provider_order == [f"calendar:{EPL}", f"details:{EPL}"]
        assert stage_order == [
            "results", "closing", "postmatch", "statistics", "state", "notification", "state",
        ]
        receipt = json.loads((root / f"data/local/leagues/{EPL}/results.json").read_text())
        postmatch = json.loads((root / f"data/local/leagues/{EPL}/postmatch.json").read_text())
        assert postmatch["artifact_scope"] == "fotmob_formal_postmatch"
        assert postmatch["result_provider_schema"] == "fotmob_league_results_v1"
        assert postmatch["accepted_result_receipts"][receipt["fingerprint"]] == receipt
        assert result["safety"] == {
            "read_env": False,
            "called_fotmob": True,
            "wrote": True,
            "notified": True,
        }
        assert result["notifications"] == [{"event_type": "daily_settlement", "status": "sent"}]


def test_pending_notification_retries_and_exits_without_provider_call():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        outbox_path = root / "data/local/leagues/postmatch_notification_state.json"
        event = build_threshold_events(
            previous_decided=19,
            current_decided=20,
            sent_thresholds=set(),
            aggregate_fingerprint="a" * 64,
        )[0]
        failed = LeaguePostmatchNotificationOutbox(
            outbox_path,
            lambda *_args, **_kwargs: {"status": "failed"},
        )
        assert failed.deliver(event)["status"] == "failed"
        _write_json(root / "data/local/leagues/acceptance.json", {"malformed": True})
        _write_json(root / "data/local/leagues/postmatch_state.json", {"malformed": True})
        sent: list[str] = []

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            notify=True,
            calendar_fetcher=_forbidden,
            detail_fetcher=_forbidden,
            notifier=lambda _content, summary: sent.append(summary) or {"status": "sent"},
        )

        assert result["status"] == "notification_retried"
        assert result["notification_retry"] == {"status": "complete", "sent": 1, "failed": 0}
        assert len(sent) == 1
        assert result["safety"]["called_fotmob"] is False


def test_notify_disabled_consumes_without_outbox_and_next_run_processes_provider():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        provider_events: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(provider_events)

        first = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
            notifier=_forbidden,
        )

        assert first["status"] == "settled"
        assert first["safety"]["notified"] is False
        assert not (root / "data/local/leagues/postmatch_notification_state.json").exists()
        state = json.loads((root / "data/local/leagues/postmatch_state.json").read_text())
        assert state["notification_transition_consumed"] is True

        history_path = root / f"data/local/leagues/{EPL}/history/snapshot-1.json"
        history = json.loads(history_path.read_text())
        second = dict(history["matches"][0])
        second["source_event_id"] = "1002"
        history["matches"].append(second)
        _write_json(history_path, history)
        second_provider_events: list[str] = []

        second_run = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=lambda competition_id, _date: (
                second_provider_events.append(f"calendar:{competition_id}")
                or _calendar(competition_id, "1002")
            ),
            detail_fetcher=lambda competition_id, event_id: (
                second_provider_events.append(f"details:{competition_id}")
                or _details(competition_id, event_id)
            ),
            notifier=_forbidden,
        )
        assert second_run["status"] == "settled"
        assert second_run["safety"]["called_fotmob"] is True
        assert second_provider_events == [f"calendar:{EPL}", f"details:{EPL}"]
        assert not (root / "data/local/leagues/postmatch_notification_state.json").exists()

        next_day = run_league_postmatch(
            root,
            live=True,
            write=True,
            notify=True,
            now=datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc),
            calendar_fetcher=_forbidden,
            detail_fetcher=_forbidden,
            notifier=_forbidden,
        )
        assert next_day["notifications"] == []
        assert next_day["safety"]["notified"] is False


def test_crash_after_state_commit_before_intent_recovers_transition_before_provider():
    import worldcup.league_postmatch_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        events: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(events)
        with patch.object(
            runner.LeaguePostmatchNotificationOutbox,
            "deliver",
            side_effect=OSError("intent disk secret"),
        ):
            failed = run_league_postmatch(
                root,
                live=True,
                write=True,
                notify=True,
                now=NOW,
                calendar_fetcher=calendar_fetcher,
                detail_fetcher=detail_fetcher,
                notifier=_forbidden,
            )

        assert failed["status"] == "error"
        assert failed["reason"] == "notification_intent_commit_failed"
        assert "secret" not in json.dumps(failed)
        state = json.loads((root / "data/local/leagues/postmatch_state.json").read_text())
        assert state["notification_transition_consumed"] is False

        sent: list[str] = []
        recovered = run_league_postmatch(
            root,
            live=True,
            write=True,
            notify=True,
            now=datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc),
            calendar_fetcher=_forbidden,
            detail_fetcher=_forbidden,
            notifier=lambda _content, summary: sent.append(summary) or {"status": "sent"},
        )
        assert recovered["status"] == "notification_recovered"
        assert recovered["safety"]["called_fotmob"] is False
        assert recovered["safety"]["notified"] is True
        assert len(sent) == 1
        outbox = json.loads(
            (root / "data/local/leagues/postmatch_notification_state.json").read_text()
        )
        daily = next(iter(outbox["sent"].values()))["event"]
        assert daily["payload"]["settlement_date"] == "2026-08-29"


def test_legacy_v1_state_upgrades_as_consumed_without_replaying_historical_notification():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        events: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(events)
        initial = run_league_postmatch(
            root,
            live=True,
            write=True,
            notify=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
            notifier=lambda *_args: {"status": "sent"},
        )
        assert initial["status"] == "settled"
        state_path = root / "data/local/leagues/postmatch_state.json"
        state = json.loads(state_path.read_text())
        legacy = {
            key: value for key, value in state.items()
            if key not in {"notification_date", "notification_transition_consumed"}
        }
        legacy["schema_version"] = 1
        _write_json(state_path, legacy)

        upgraded = run_league_postmatch(
            root,
            live=True,
            write=True,
            notify=True,
            now=datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc),
            calendar_fetcher=_forbidden,
            detail_fetcher=_forbidden,
            notifier=_forbidden,
        )

        assert upgraded["status"] == "stored"
        assert upgraded["notifications"] == []
        normalized = json.loads(state_path.read_text())
        assert normalized["schema_version"] == 2
        assert normalized["notification_date"] is None
        assert normalized["notification_transition_consumed"] is True
        assert normalized["settled_count"] == legacy["settled_count"] == 1
        assert normalized["decided"] == legacy["decided"] == 1


def test_failed_and_already_sent_notification_statuses_report_truthful_safety():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        events: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(events)
        failed = run_league_postmatch(
            root,
            live=True,
            write=True,
            notify=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
            notifier=lambda *_args: {"status": "failed"},
        )
        assert failed["notifications"] == [{"event_type": "daily_settlement", "status": "failed"}]
        assert failed["safety"]["wrote"] is True
        assert failed["safety"]["notified"] is False

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        events = []
        calendar_fetcher, detail_fetcher = _fetchers(events)
        sent = run_league_postmatch(
            root,
            live=True,
            write=True,
            notify=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
            notifier=lambda *_args: {"status": "sent"},
        )
        assert sent["safety"]["notified"] is True
        state_path = root / "data/local/leagues/postmatch_state.json"
        state = json.loads(state_path.read_text())
        state["notification_transition_consumed"] = False
        _write_json(state_path, state)

        already = run_league_postmatch(
            root,
            live=True,
            write=True,
            notify=True,
            now=NOW,
            calendar_fetcher=_forbidden,
            detail_fetcher=_forbidden,
            notifier=_forbidden,
        )
        assert already["status"] == "notification_recovered"
        assert already["notifications"] == [
            {"event_type": "daily_settlement", "status": "already_sent"}
        ]
        assert already["safety"]["notified"] is False


def test_provider_failure_is_isolated_by_formal_competition_partition():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root, (EPL, LALIGA))
        events: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(events, failures={LALIGA})

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
        )

        assert result["status"] == "partial"
        assert result["competitions"][EPL]["status"] == "settled"
        assert result["competitions"][LALIGA] == {"status": "error", "reason": "calendar_fetch_failed"}
        assert (root / f"data/local/leagues/{EPL}/postmatch.json").exists()
        assert not (root / f"data/local/leagues/{LALIGA}/results.json").exists()
        assert "provider-token" not in json.dumps(result)


def test_brasileirao_268_result_commits_a_verified_receipt():
    """The runner must store an accepted 90-minute receipt from Brasileirão's real FotMob ID."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root, (BRAZIL,))

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=lambda _competition, _date: _calendar(BRAZIL, "1001"),
            detail_fetcher=lambda _competition, event_id: _details(BRAZIL, event_id),
        )

        receipt = json.loads((root / f"data/local/leagues/{BRAZIL}/results.json").read_text())
        assert result["competitions"][BRAZIL]["status"] == "settled"
        assert receipt["competition_id"] == BRAZIL
        assert [row["source_event_id"] for row in receipt["results"]] == ["1001"]


def test_invalid_brazil_provider_id_is_blocked_while_epl_still_settles():
    """One malformed provider partition must not prevent another formal league from advancing."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root, (BRAZIL, EPL))

        def calendar_fetcher(competition_id: str, _date: str) -> dict:
            payload = _calendar(competition_id, "1001" if competition_id == BRAZIL else "1002")
            if competition_id == BRAZIL:
                payload["leagues"][0]["id"] = 1122
            return payload

        def detail_fetcher(competition_id: str, event_id: str) -> dict:
            payload = _details(competition_id, event_id)
            if competition_id == BRAZIL:
                payload["general"]["leagueId"] = 1122
            return payload

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
        )

        assert result["status"] == "partial"
        assert result["competitions"][BRAZIL] == {
            "status": "error", "reason": "result_parse_failed",
        }
        assert result["competitions"][EPL]["status"] == "settled"
        assert not (root / f"data/local/leagues/{BRAZIL}/results.json").exists()
        assert (root / f"data/local/leagues/{EPL}/results.json").exists()


def test_calendar_404_blocks_only_affected_competition_as_provider_contract_drift():
    """A calendar route 404 must not be mistaken for a retryable calendar transport failure."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root, (EPL, LALIGA))
        drift = getattr(fotmob_source, "FotMobProviderContractDrift", RuntimeError)

        def calendar_fetcher(competition_id: str, _date: str) -> dict:
            if competition_id == LALIGA:
                raise drift("fotmob_provider_contract_drift_404")
            return _calendar(competition_id, "1001")

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=lambda competition_id, event_id: _details(competition_id, event_id),
        )

        assert result["status"] == "partial"
        assert result["competitions"][EPL]["status"] == "settled"
        assert result["competitions"][LALIGA] == {"status": "error", "reason": "provider_contract_drift"}
        assert (root / f"data/local/leagues/{EPL}/results.json").exists()
        assert not (root / f"data/local/leagues/{LALIGA}/results.json").exists()


def test_details_404_blocks_only_affected_competition_as_provider_contract_drift():
    """A detail route 404 must block its partition before any partial receipt is written."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root, (EPL, LALIGA))
        drift = getattr(fotmob_source, "FotMobProviderContractDrift", RuntimeError)

        def detail_fetcher(competition_id: str, event_id: str) -> dict:
            if competition_id == LALIGA:
                raise drift("fotmob_provider_contract_drift_404")
            return _details(competition_id, event_id)

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=lambda competition_id, _date: _calendar(
                competition_id, "1001" if competition_id == EPL else "1002"
            ),
            detail_fetcher=detail_fetcher,
        )

        assert result["status"] == "partial"
        assert result["competitions"][EPL]["status"] == "settled"
        assert result["competitions"][LALIGA] == {"status": "error", "reason": "provider_contract_drift"}
        assert (root / f"data/local/leagues/{EPL}/results.json").exists()
        assert not (root / f"data/local/leagues/{LALIGA}/results.json").exists()


def test_terminal_status_crossing_and_duplicate_provider_evidence_fail_closed():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=lambda _competition, _date: _calendar(EPL, "1001", finished=False),
            detail_fetcher=lambda _competition, event: _details(EPL, event, finished=True),
        )

        assert result["status"] == "pending"
        assert result["competitions"][EPL]["status"] == "pending"
        assert not (root / f"data/local/leagues/{EPL}/results.json").exists()

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)

        def duplicate_calendar(_competition: str, _date: str) -> dict:
            payload = _calendar(EPL, "1001")
            payload["leagues"][0]["matches"].append(dict(payload["leagues"][0]["matches"][0]))
            return payload

        conflict = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=duplicate_calendar,
            detail_fetcher=lambda _competition, event: _details(EPL, event),
        )

        assert conflict["status"] == "error"
        assert conflict["competitions"][EPL] == {
            "status": "conflict",
            "reason": "result_evidence_conflict",
            "conflict_count": 1,
        }
        assert not (root / f"data/local/leagues/{EPL}/postmatch.json").exists()


def test_missing_detail_90min_proof_remains_pending_without_receipt():
    """A nominal FT without every real-shape proof field cannot settle the due event."""
    for field, nested in (
        ("firstExtraHalfStarted", True),
        ("secondExtraHalfStarted", True),
        ("whoLostOnPenalties", False),
        ("whoLostOnAggregated", False),
    ):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _setup(root)

            def detail_fetcher(_competition: str, event_id: str) -> dict:
                payload = _details(EPL, event_id)
                status = payload["header"]["status"]
                if nested:
                    del status["halfs"][field]
                else:
                    del status[field]
                return payload

            result = run_league_postmatch(
                root,
                live=True,
                write=True,
                now=NOW,
                calendar_fetcher=lambda _competition, _date: _calendar(EPL, "1001"),
                detail_fetcher=detail_fetcher,
            )

            assert result["status"] == "pending"
            assert result["competitions"][EPL]["status"] == "pending"
            assert not (root / f"data/local/leagues/{EPL}/results.json").exists()


def test_parser_result_must_match_exact_immutable_due_team_and_kickoff_identity():
    for mismatch in ("wrong_team", "wrong_kickoff"):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _setup(root)

            def calendar_fetcher(_competition: str, _date: str) -> dict:
                payload = _calendar(EPL, "1001")
                if mismatch == "wrong_team":
                    payload["leagues"][0]["matches"][0]["away"]["name"] = "Liverpool"
                else:
                    payload["leagues"][0]["matches"][0]["status"]["utcTime"] = "2026-08-28T19:06:00Z"
                return payload

            def detail_fetcher(_competition: str, event_id: str) -> dict:
                payload = _details(EPL, event_id)
                if mismatch == "wrong_team":
                    payload["general"]["awayTeam"]["name"] = "Liverpool"
                else:
                    payload["general"]["matchTimeUTCDate"] = "2026-08-28T19:06:00Z"
                return payload

            result = run_league_postmatch(
                root,
                live=True,
                write=True,
                now=NOW,
                calendar_fetcher=calendar_fetcher,
                detail_fetcher=detail_fetcher,
            )

            assert result["status"] == "error"
            assert result["competitions"][EPL] == {
                "status": "error",
                "reason": "result_due_identity_mismatch",
            }
            assert not (root / f"data/local/leagues/{EPL}/results.json").exists()


def test_detail_failure_remains_explicit_when_another_due_event_settles():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        history_path = root / f"data/local/leagues/{EPL}/history/snapshot-1.json"
        history = json.loads(history_path.read_text())
        second = dict(history["matches"][0])
        second["source_event_id"] = "1002"
        history["matches"].append(second)
        _write_json(history_path, history)

        def calendar_fetcher(_competition: str, _date: str) -> dict:
            payload = _calendar(EPL, "1001")
            second_match = json.loads(json.dumps(payload["leagues"][0]["matches"][0]))
            second_match["id"] = 1002
            payload["leagues"][0]["matches"].append(second_match)
            return payload

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=lambda _competition, event_id: (
                _details(EPL, event_id)
                if event_id == "1001"
                else (_ for _ in ()).throw(RuntimeError("provider secret"))
            ),
        )

        assert result["status"] == "partial"
        assert result["competitions"][EPL] == {
            "status": "partial",
            "newly_settled": 1,
            "result_count": 1,
            "pending_count": 1,
            "source_error_count": 1,
            "pending": [{"source_event_id": "1002", "reason": "details_fetch_failed"}],
        }
        receipt = json.loads((root / f"data/local/leagues/{EPL}/results.json").read_text())
        assert [row["source_event_id"] for row in receipt["results"]] == ["1001"]
        assert "provider secret" not in json.dumps(result)


def test_derivative_atomic_crashes_recover_from_committed_receipt_without_provider_refetch():
    import worldcup.league_postmatch_runner as runner

    expected_reasons = {
        "postmatch.json": "postmatch_commit_failed",
        "postmatch_statistics.json": "statistics_commit_failed",
        "postmatch_state.json": "state_commit_failed",
    }
    for failing_name, expected_reason in expected_reasons.items():
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _setup(root)
            events: list[str] = []
            calendar_fetcher, detail_fetcher = _fetchers(events)
            original_atomic_write = runner._write_json_atomic

            def fail_stage(path: Path, payload: dict, *, _target: str = failing_name):
                if path.name == _target:
                    raise OSError("disk path contains sensitive detail")
                return original_atomic_write(path, payload)

            with patch.object(runner, "_write_json_atomic", fail_stage):
                failed = run_league_postmatch(
                    root,
                    live=True,
                    write=True,
                    now=NOW,
                    calendar_fetcher=calendar_fetcher,
                    detail_fetcher=detail_fetcher,
                )
            assert failed["status"] == "error"
            assert failed.get("reason") == expected_reason
            assert "sensitive" not in json.dumps(failed)

            recovered = run_league_postmatch(
                root,
                live=True,
                write=True,
                now=NOW,
                calendar_fetcher=_forbidden,
                detail_fetcher=_forbidden,
            )
            assert recovered["status"] == "settled"
            assert json.loads((root / f"data/local/leagues/{EPL}/postmatch.json").read_text())["decision_tally"]["hit"] == 1


def test_statistics_regression_is_validated_before_old_statistics_can_be_overwritten():
    import worldcup.league_postmatch_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        events: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(events)
        initial = run_league_postmatch(
            root,
            live=True,
            write=True,
            notify=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
            notifier=lambda *_args: {"status": "sent"},
        )
        assert initial["status"] == "settled"
        statistics_path = root / "data/local/leagues/postmatch_statistics.json"
        old_statistics = statistics_path.read_bytes()
        old_state = (root / "data/local/leagues/postmatch_state.json").read_bytes()
        regressed = build_league_statistics([])
        original_collect = runner._collect_statistics_components

        def collect_regressed(*args, **kwargs):
            _statistics, manifest, issues = original_collect(*args, **kwargs)
            return regressed, manifest, issues

        with patch.object(runner, "_collect_statistics_components", collect_regressed):
            result = run_league_postmatch(
                root,
                live=True,
                write=True,
                notify=True,
                now=NOW,
                calendar_fetcher=_forbidden,
                detail_fetcher=_forbidden,
                notifier=_forbidden,
            )

        assert result["status"] == "error"
        assert result["reason"] == "statistics_validation_failed"
        assert statistics_path.read_bytes() == old_statistics
        assert (root / "data/local/leagues/postmatch_state.json").read_bytes() == old_state


def test_result_and_closing_atomic_crashes_have_stage_specific_recovery():
    import worldcup.league_postmatch_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        events: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(events)
        with patch("worldcup.league_result_store._atomic_write", side_effect=OSError("disk")):
            failed = run_league_postmatch(
                root,
                live=True,
                write=True,
                now=NOW,
                calendar_fetcher=calendar_fetcher,
                detail_fetcher=detail_fetcher,
            )
        assert failed["status"] == "error"
        assert failed["competitions"][EPL]["reason"] == "result_commit_failed"
        assert not (root / f"data/local/leagues/{EPL}/results.json").exists()

        recovered = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
        )
        assert recovered["status"] == "settled"

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        events = []
        calendar_fetcher, detail_fetcher = _fetchers(events)
        with patch.object(runner.LeagueClosingStore, "merge", side_effect=OSError("disk")):
            failed = run_league_postmatch(
                root,
                live=True,
                write=True,
                now=NOW,
                calendar_fetcher=calendar_fetcher,
                detail_fetcher=detail_fetcher,
            )
        assert failed["status"] == "error"
        assert failed["competitions"][EPL]["reason"] == "closing_commit_failed"
        assert (root / f"data/local/leagues/{EPL}/results.json").exists()

        recovered = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=_forbidden,
            detail_fetcher=_forbidden,
        )
        assert recovered["status"] == "settled"


def test_live_output_never_projects_raw_provider_or_notifier_values():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        events: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(events)

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            notify=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
            notifier=lambda *_args: {"status": "sent", "token": "notifier-secret"},
        )

        projected = json.dumps(result, ensure_ascii=False)
        assert "provider-secret" not in projected
        assert "notifier-secret" not in projected
        assert "raw_response" not in projected
        assert "token" not in projected


def test_malformed_partition_uses_last_known_good_while_healthy_league_advances():
    malformed_values = (
        "{not-json",
        json.dumps({
            "schema_version": 2,
            "competition_id": EPL,
            "statistics_scope": "observed_schema_v2_match_pick_only",
            "matches": [],
            "decision_tally": {"hit": 1, "miss": 0, "push": 0, "no_pick": 0},
            "decision_sample": {"decided": 1},
            "decision_coverage": {"finished_result_count": 1},
        }),
    )
    expected_reasons = ("postmatch_partition_unreadable", "postmatch_partition_invalid")
    for malformed, expected_reason in zip(malformed_values, expected_reasons):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _setup(root, (EPL,))
            first_events: list[str] = []
            first_calendar, first_details = _fetchers(first_events)
            first = run_league_postmatch(
                root,
                live=True,
                write=True,
                notify=False,
                now=NOW,
                calendar_fetcher=first_calendar,
                detail_fetcher=first_details,
            )
            assert first["status"] == "settled"
            assert json.loads((root / "data/local/leagues/postmatch_statistics.json").read_text())[
                "aggregate"
            ]["decision_tally"]["hit"] == 1

            _setup(root, (EPL, LALIGA))
            (root / f"data/local/leagues/{EPL}/postmatch.json").write_text(
                malformed, encoding="utf-8"
            )
            provider_events: list[str] = []
            calendar_fetcher, detail_fetcher = _fetchers(provider_events)
            notifications: list[str] = []

            result = run_league_postmatch(
                root,
                live=True,
                write=True,
                notify=True,
                now=NOW,
                calendar_fetcher=calendar_fetcher,
                detail_fetcher=detail_fetcher,
                notifier=lambda content, _summary: notifications.append(content) or {"status": "sent"},
            )

            assert result["status"] == "settled"
            assert result["competitions"][EPL] == {
                "status": "stale",
                "reason": expected_reason,
                "using_last_known_good": True,
            }
            assert result["competitions"][LALIGA]["status"] == "settled"
            assert provider_events == [f"calendar:{LALIGA}", f"details:{LALIGA}"]
            statistics = json.loads((
                root / "data/local/leagues/postmatch_statistics.json"
            ).read_text())
            assert statistics["aggregate"]["decision_tally"]["hit"] == 2
            assert set(statistics["competitions"]) == {EPL, LALIGA}
            state = json.loads((root / "data/local/leagues/postmatch_state.json").read_text())
            assert state["decided"] == 2
            assert len(notifications) == 1
            components = json.loads((
                root / "data/local/leagues/postmatch_components.json"
            ).read_text())
            assert components["components"][EPL]["status"] == "stale"
            assert components["components"][EPL]["reason"] == expected_reason
            assert components["components"][LALIGA]["status"] == "fresh"


def _empty_formal_postmatch(competition_id: str) -> dict:
    return {
        "schema_version": 2,
        "competition_id": competition_id,
        "statistics_scope": "observed_schema_v2_match_pick_only",
        "matches": [],
        "decision_tally": {"hit": 0, "miss": 0, "push": 0, "no_pick": 0},
        "decision_sample": {
            "min_sample": 20,
            "decided": 0,
            "actionable": 0,
            "decision_count": 0,
            "sample_too_small": True,
            "hit_rate": None,
            "pick_rate": None,
        },
        "decision_coverage": {
            "finished_result_count": 0,
            "closing_available_count": 0,
            "missing_closing_count": 0,
            "decision_available_count": 0,
            "missing_decision_count": 0,
            "invalid_decision_count": 0,
            "unresolved_count": 0,
            "legacy_decision_count": 0,
        },
        "skipped_no_closing": 0,
        "missing_closing_event_ids": [],
        "missing_closing_results": {},
        "accepted_result_receipts": {},
        "artifact_scope": "fotmob_formal_postmatch",
        "result_provider_schema": "fotmob_league_results_v1",
    }


def _replacement_formal_postmatch(competition_id: str, event_id: str) -> dict:
    closing = select_league_closings(
        [_snapshot(competition_id, event_id)],
        competition_id,
    )
    home, away, home_canonical, away_canonical = _teams(competition_id)
    row = {
        "competition_id": competition_id,
        "source_event_id": event_id,
        "kickoff_at_utc": "2026-08-28T19:00:00+00:00",
        "home_team": home,
        "away_team": away,
        "home_canonical": home_canonical,
        "away_canonical": away_canonical,
        "home_score": 2,
        "away_score": 1,
        "captured_at": NOW.isoformat(),
        "result_scope": "football_90min",
        "source_fingerprint": "b" * 64,
    }
    core = {"schema_version": 1, "competition_id": competition_id, "results": [row]}
    receipt = {
        **core,
        "fingerprint": hashlib.sha256(json.dumps(
            core,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()).hexdigest(),
    }
    return {
        **build_league_postmatch(closing, receipt, competition_id),
        "artifact_scope": "fotmob_formal_postmatch",
        "result_provider_schema": "fotmob_league_results_v1",
    }


def test_valid_empty_partition_uses_lkg_while_healthy_league_advances():
    for incompatible_lkg in (None, "identity", "schema", "membership"):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _setup(root, (EPL,))
            first_calendar, first_details = _fetchers([])
            assert run_league_postmatch(
                root,
                live=True,
                write=True,
                now=NOW,
                calendar_fetcher=first_calendar,
                detail_fetcher=first_details,
            )["status"] == "settled"

            manifest_path = root / "data/local/leagues/postmatch_components.json"
            manifest = json.loads(manifest_path.read_text())
            if incompatible_lkg == "identity":
                manifest["components"][EPL]["competition_id"] = LALIGA
            elif incompatible_lkg == "schema":
                manifest["components"][EPL]["component_schema_version"] = 999
            elif incompatible_lkg == "membership":
                manifest["components"][EPL]["membership"] = None
            _write_json(manifest_path, manifest)
            original_fingerprint = manifest["components"][EPL]["postmatch_fingerprint"]

            _setup(root, (LALIGA,))
            _write_json(
                root / f"data/local/leagues/{LALIGA}/history/snapshot-1.json",
                _snapshot(LALIGA, "1002"),
            )
            _write_json(
                root / f"data/local/leagues/{EPL}/postmatch.json",
                _empty_formal_postmatch(EPL),
            )
            provider_events: list[str] = []
            calendar_fetcher, detail_fetcher = _fetchers(provider_events)
            notifications: list[str] = []

            result = run_league_postmatch(
                root,
                live=True,
                write=True,
                notify=True,
                now=NOW,
                calendar_fetcher=calendar_fetcher,
                detail_fetcher=detail_fetcher,
                notifier=lambda content, _summary: notifications.append(content) or {"status": "sent"},
            )

            assert result["status"] == "settled"
            assert result["competitions"][EPL] == {
                "status": "stale",
                "reason": "postmatch_partition_regression",
                "using_last_known_good": True,
            }
            assert result["competitions"][LALIGA]["status"] == "settled"
            assert provider_events == [f"calendar:{LALIGA}", f"details:{LALIGA}"]
            assert len(notifications) == 1
            statistics = json.loads((
                root / "data/local/leagues/postmatch_statistics.json"
            ).read_text())
            assert statistics["aggregate"]["decision_tally"]["hit"] == 2
            manifest = json.loads(manifest_path.read_text())
            epl_component = manifest["components"][EPL]
            assert epl_component["status"] == "stale"
            if incompatible_lkg is None:
                assert epl_component["postmatch_fingerprint"] == original_fingerprint
                assert epl_component["membership"]["settled_event_ids"] == ["1001"]
            else:
                assert epl_component["postmatch_fingerprint"] is None
                assert epl_component["membership"] is None
            assert epl_component["competition_id"] == EPL
            assert epl_component["component_schema_version"] == 1


def test_replacement_membership_cannot_overwrite_lkg_component():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root, (EPL,))
        first_calendar, first_details = _fetchers([])
        assert run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=first_calendar,
            detail_fetcher=first_details,
        )["status"] == "settled"
        manifest_path = root / "data/local/leagues/postmatch_components.json"
        original = json.loads(manifest_path.read_text())["components"][EPL]

        _setup(root, (LALIGA,))
        _write_json(
            root / f"data/local/leagues/{LALIGA}/history/snapshot-1.json",
            _snapshot(LALIGA, "1002"),
        )
        _write_json(
            root / f"data/local/leagues/{EPL}/postmatch.json",
            _replacement_formal_postmatch(EPL, "replacement-event"),
        )
        calendar_fetcher, detail_fetcher = _fetchers([])

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            now=NOW,
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
        )

        assert result["competitions"][EPL]["reason"] == "postmatch_partition_regression"
        stored = json.loads(manifest_path.read_text())["components"][EPL]
        assert stored["postmatch_fingerprint"] == original["postmatch_fingerprint"]
        assert stored["membership"] == original["membership"]


def test_daily_settlement_date_uses_beijing_calendar_day_and_positional_notifier_contract():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup(root)
        events: list[str] = []
        calendar_fetcher, detail_fetcher = _fetchers(events)
        sent: list[tuple[str, str]] = []

        result = run_league_postmatch(
            root,
            live=True,
            write=True,
            notify=True,
            now=datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc),
            calendar_fetcher=calendar_fetcher,
            detail_fetcher=detail_fetcher,
            notifier=lambda content, summary: sent.append((content, summary)) or {"status": "sent"},
        )

        assert result["notifications"] == [{"event_type": "daily_settlement", "status": "sent"}]
        assert len(sent) == 1
        outbox = json.loads(
            (root / "data/local/leagues/postmatch_notification_state.json").read_text()
        )
        daily = next(iter(outbox["sent"].values()))["event"]
        assert daily["payload"]["settlement_date"] == "2026-08-29"


def test_live_cli_rejects_explicit_now_before_runner_execution():
    try:
        main(["--live", "--write", "--now", "2026-08-29T00:00:00Z"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("live CLI must reject replay time")
