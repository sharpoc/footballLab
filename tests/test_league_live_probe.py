import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_live_probe import evaluate_league_probe_bundle, run_league_live_probe
from worldcup.league_result_evidence import build_result_contract_evidence
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


def _plan():
    return {
        "requests": [{
            "competition_id": "epl_2026_27",
            "sport_key": "soccer_epl",
            "anchor": "T-90m",
            "markets": ["h2h"],
            "event_ids": ["epl-1"],
            "estimated_credits": 1,
        }],
        "estimated_credits": 1,
    }


def _fail(*args, **kwargs):
    raise AssertionError("dry-run must not call dependencies")


def test_probe_dry_run_does_not_load_env_fetch_or_write():
    with TemporaryDirectory() as tmp:
        result = run_league_live_probe(
            root=tmp,
            plan=_plan(),
            live=False,
            write=False,
            env_loader=_fail,
            payload_fetcher=_fail,
        )
        assert result["status"] == "dry_run"
        assert result["estimated_credits"] == 1
        assert list(Path(tmp).rglob("*")) == []


def test_probe_live_write_saves_partitioned_sanitized_bundle():
    def fetch(request):
        return {
            "odds": [{
                "id": "epl-1",
                "sport_key": "soccer_epl",
                "commence_time": "2026-08-24T18:00:00Z",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "bookmakers": [],
                "apiKey": "must-not-persist",
            }],
            "headers": {"x-requests-remaining": "99", "Authorization": "secret"},
            "url": "https://example.test?apiKey=must-not-persist",
        }

    with TemporaryDirectory() as tmp:
        result = run_league_live_probe(
            root=tmp,
            plan=_plan(),
            live=True,
            write=True,
            env_loader=lambda: {"THE_ODDS_API_KEY_PRIMARY": "secret"},
            payload_fetcher=fetch,
        )
        path = Path(tmp) / "data/probe/leagues/epl_2026_27/probe.json"
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)

        assert result["status"] == "stored"
        assert payload["competition_id"] == "epl_2026_27"
        assert payload["odds"][0]["id"] == "epl-1"
        assert payload["quota"] == {"remaining": 99}
        assert "must-not-persist" not in text
        assert "Authorization" not in text
        assert "url" not in payload


def test_probe_bundle_offline_evaluation_derives_active_only_from_complete_evidence():
    registry = LeagueTeamIdentityRegistry({
        "epl_2026_27": {"arsenal": ("Arsenal",), "chelsea": ("Chelsea",)},
    })
    result_evidence = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="theoddsapi_scores_v1",
        score_scope="football_90min",
        source_reference="saved-score-sample-sha256",
    )
    bundle = {
        "schema_version": 1,
        "competition_id": "epl_2026_27",
        "sport_key": "soccer_epl",
        "odds": [{
            "id": "epl-1",
            "sport_key": "soccer_epl",
            "commence_time": "2026-08-25T18:00:00Z",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "bookmakers": [{
                "key": "book-1",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Arsenal", "price": 2.0},
                    {"name": "Draw", "price": 3.4},
                    {"name": "Chelsea", "price": 3.8},
                ]}],
            }],
        }],
    }

    result = evaluate_league_probe_bundle(bundle, identity_registry=registry, result_contract_evidence=result_evidence)
    assert result["state"] == "active"
    assert set(result["fingerprints"]) == {"sport_catalog", "odds_sample", "team_identity", "result_contract"}


def test_fotmob_acceptance_binds_complete_registry_and_requires_bound_probe_path():
    def registry(extra_alias: str) -> LeagueTeamIdentityRegistry:
        return LeagueTeamIdentityRegistry({
            "epl_2026_27": {
                "arsenal": ("Arsenal",),
                "chelsea": ("Chelsea",),
                "liverpool": (extra_alias,),
            },
        })

    bundle = {
        "schema_version": 1,
        "competition_id": "epl_2026_27",
        "sport_key": "soccer_epl",
        "odds": [{
            "id": "epl-1",
            "sport_key": "soccer_epl",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "bookmakers": [{
                "key": "book-1",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Arsenal", "price": 2.0},
                    {"name": "Draw", "price": 3.4},
                    {"name": "Chelsea", "price": 3.8},
                ]}],
            }],
        }],
    }
    bound = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference="a" * 64,
        provider="fotmob",
        sample_path="data/probe/leagues/results/epl-finished.json",
    )
    unbound = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference="a" * 64,
        provider="fotmob",
    )

    first = evaluate_league_probe_bundle(
        bundle,
        identity_registry=registry("Liverpool"),
        result_contract_evidence=bound,
    )
    changed_registry = evaluate_league_probe_bundle(
        bundle,
        identity_registry=registry("Liverpool FC"),
        result_contract_evidence=bound,
    )
    missing_path = evaluate_league_probe_bundle(
        bundle,
        identity_registry=registry("Liverpool"),
        result_contract_evidence=unbound,
    )

    assert first["state"] == "active"
    assert changed_registry["state"] == "active"
    assert first["fingerprints"]["team_identity"] != changed_registry["fingerprints"]["team_identity"]
    assert missing_path["state"] == "identity_verified"
