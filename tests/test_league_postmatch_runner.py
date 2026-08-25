from __future__ import annotations

import fcntl
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from worldcup.league_postmatch_notifications import (
    LeaguePostmatchNotificationOutbox,
    build_threshold_events,
)
from worldcup.league_postmatch_runner import main, run_league_postmatch
from worldcup.league_result_evidence import build_result_contract_evidence


EPL = "epl_2026_27"
LALIGA = "laliga_2026_27"
NOW = datetime(2026, 8, 29, 0, 0, tzinfo=timezone.utc)


def _forbidden(*_args: object, **_kwargs: object) -> dict:
    raise AssertionError("external dependency must not be called")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _evidence(competition_id: str) -> dict:
    sport_keys = {EPL: "soccer_epl", LALIGA: "soccer_spain_la_liga"}
    return build_result_contract_evidence(
        competition_id=competition_id,
        sport_key=sport_keys[competition_id],
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference=hashlib.sha256(f"{competition_id}:saved-sample".encode()).hexdigest(),
        provider="fotmob",
    )


def _acceptance(evidence_by_competition: dict[str, dict]) -> dict:
    return {
        "schema_version": 1,
        "competitions": {
            competition_id: {
                "competition_id": competition_id,
                "state": "active",
                "reason": None,
                "fingerprints": {
                    "sport_catalog": "sport",
                    "odds_sample": "odds",
                    "team_identity": "identity",
                    "result_contract": evidence["fingerprint"],
                },
            }
            for competition_id, evidence in evidence_by_competition.items()
        },
    }


def _teams(competition_id: str) -> tuple[str, str, str, str]:
    if competition_id == EPL:
        return "Arsenal", "Chelsea", "arsenal", "chelsea"
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
    evidence_by_competition = {competition_id: _evidence(competition_id) for competition_id in competitions}
    _write_json(root / "data/local/leagues/acceptance.json", _acceptance(evidence_by_competition))
    for index, competition_id in enumerate(competitions, start=1):
        partition = root / "data/local/leagues" / competition_id
        _write_json(partition / "result_contract_evidence.json", evidence_by_competition[competition_id])
        _write_json(partition / "history/snapshot-1.json", _snapshot(competition_id, str(1000 + index)))


def _status(*, finished: bool, score: str = "2 - 1") -> dict:
    return {
        "utcTime": "2026-08-28T19:00:00Z",
        "started": finished,
        "cancelled": False,
        "finished": finished,
        "scoreStr": score,
        "reason": {"short": "FT" if finished else "NS", "long": "Full-Time", "extraTime": False},
    }


def _calendar(competition_id: str, event_id: str, *, finished: bool = True, score: str = "2 - 1") -> dict:
    home, away, _home_canonical, _away_canonical = _teams(competition_id)
    league_ids = {EPL: 47, LALIGA: 87}
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
    league_ids = {EPL: 47, LALIGA: 87}
    return {
        "general": {
            "matchId": int(event_id),
            "leagueId": league_ids[competition_id],
            "matchTimeUTC": "2026-08-28T19:00:00Z",
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
        evidence_path = root / f"data/local/leagues/{EPL}/result_contract_evidence.json"
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
        original_closing_commit = runner.LeagueClosingStore.commit
        original_atomic_write = runner._write_json_atomic
        original_deliver = runner.LeaguePostmatchNotificationOutbox.deliver

        def result_merge(store, payload):
            stage_order.append("results")
            return original_result_merge(store, payload)

        def closing_commit(store, payload):
            stage_order.append("closing")
            return original_closing_commit(store, payload)

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
            patch.object(runner.LeagueClosingStore, "commit", closing_commit),
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
            "results", "closing", "postmatch", "statistics", "state", "notification",
        ]
        receipt = json.loads((root / f"data/local/leagues/{EPL}/results.json").read_text())
        postmatch = json.loads((root / f"data/local/leagues/{EPL}/postmatch.json").read_text())
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
        with patch.object(runner.LeagueClosingStore, "commit", side_effect=OSError("disk")):
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
