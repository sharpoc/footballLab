from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_batch_runner import run_league_batch


def _fail(*args, **kwargs):
    raise AssertionError("dependency must not be called")


def test_batch_dry_run_does_not_read_env_call_transport_or_write():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = run_league_batch(
            root=root,
            observed_at="2026-08-24T12:00:00Z",
            live=False,
            write=False,
            env_loader=_fail,
            odds_fetcher=_fail,
            score_fetcher=_fail,
        )
        assert result["status"] == "dry_run"
        assert set(result["competitions"]) == set(FORMAL_SINGLE_MATCH_IDS)
        assert list(root.rglob("*")) == []


def test_live_and_write_remain_blocked_before_acceptance():
    result = run_league_batch(root=".", observed_at="2026-08-24T12:00:00Z", live=True)
    assert result == {"status": "blocked", "reason": "live_acceptance_not_enabled"}


def test_batch_isolates_one_league_failure_and_reports_partial_status():
    payloads = {competition_id: [] for competition_id in FORMAL_SINGLE_MATCH_IDS}

    def build(payload, competition_id, observed_at):
        del payload, observed_at
        if competition_id == "epl_2026_27":
            raise ValueError("invalid fixture identity")
        return {"matches": [{"competition_id": competition_id}]}

    result = run_league_batch(
        root=".",
        observed_at="2026-08-24T12:00:00Z",
        odds_payloads=payloads,
        snapshot_builder=build,
    )

    assert result["status"] == "partial"
    assert result["competitions"]["epl_2026_27"] == {"status": "error", "reason": "ValueError"}
    assert sum(row["status"] == "built" for row in result["competitions"].values()) == 5
