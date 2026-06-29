import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.scores_capture import run_scores_capture


def _scores_body() -> list[dict]:
    return [
        {
            "id": "e1",
            "commence_time": "2026-06-11T19:00:00Z",
            "completed": True,
            "home_team": "Mexico",
            "away_team": "South Africa",
            "scores": [
                {"name": "Mexico", "score": "2"},
                {"name": "South Africa", "score": "0"},
            ],
        }
    ]


def _knockout_scores_body() -> list[dict]:
    return [
        {
            "id": "e73",
            "commence_time": "2026-06-28T19:00:00Z",
            "completed": True,
            "home_team": "South Africa",
            "away_team": "Canada",
            "scores": [
                {"name": "South Africa", "score": "1"},
                {"name": "Canada", "score": "2"},
            ],
        }
    ]


class FakeResponse:
    status = 200
    headers = {
        "x-requests-used": "73",
        "x-requests-remaining": "427",
        "x-requests-last": "2",
    }

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def test_scores_capture_dry_run_does_not_fetch():
    calls = []

    def transport(url):
        calls.append(url)
        raise AssertionError("dry-run must not fetch")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = run_scores_capture(
            live=False,
            env={"THE_ODDS_API_KEY": "fake-key"},
            cache_path=root / "scores.json",
            quota_path=root / "quota.json",
            results_out=root / "results.csv",
            transport=transport,
        )

    assert out["status"] == "dry_run"
    assert calls == []
    assert not (root / "results.csv").exists()


def test_scores_capture_live_upserts_results():
    def transport(_url):
        return FakeResponse(json.dumps(_scores_body()).encode())

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = run_scores_capture(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "fake-key"},
            cache_path=root / "scores.json",
            quota_path=root / "quota.json",
            results_out=root / "results.csv",
            transport=transport,
            observed_at="2026-06-12T08:00:00+00:00",
        )

        assert out["status"] == "captured"
        assert out["completed"] == 1
        assert out["added"] == 1
        assert out["slot"] == "primary"
        rows = (root / "results.csv").read_text()
        assert "mexico" in rows and "2" in rows


def test_scores_capture_live_blocks_when_all_slots_exhausted():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        quota_path = root / "quota.json"
        quota_path.write_text(json.dumps({"providers": {"theoddsapi_primary": {"remaining": 0}}}))

        out = run_scores_capture(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "fake-key"},
            cache_path=root / "scores.json",
            quota_path=quota_path,
            results_out=root / "results.csv",
            transport=lambda url: (_ for _ in ()).throw(AssertionError("no fetch")),
        )

    assert out["status"] == "blocked"
    assert out["reason"] == "quota_exhausted"


def test_scores_capture_blocks_after_knockout_boundary_without_manual_review():
    calls = []

    def transport(url):
        calls.append(url)
        return FakeResponse(json.dumps(_knockout_scores_body()).encode())

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = run_scores_capture(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "fake-key"},
            cache_path=root / "scores.json",
            quota_path=root / "quota.json",
            results_out=root / "results.csv",
            transport=transport,
            observed_at="2026-06-29T00:00:00+00:00",
        )

        assert out["status"] == "blocked"
        assert out["reason"] == "knockout_score_manual_review_required"
        assert out["knockout_boundary"] == "2026-06-28T00:00:00+00:00"
        assert calls == []
        assert not (root / "results.csv").exists()


def test_scores_capture_allows_knockout_scores_after_manual_review_opt_in():
    def transport(_url):
        return FakeResponse(json.dumps(_knockout_scores_body()).encode())

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = run_scores_capture(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "fake-key"},
            cache_path=root / "scores.json",
            quota_path=root / "quota.json",
            results_out=root / "results.csv",
            transport=transport,
            observed_at="2026-06-29T00:00:00+00:00",
            allow_knockout_scores=True,
        )

        assert out["status"] == "captured"
        assert out["completed"] == 1
        assert out["added"] == 1
        rows = (root / "results.csv").read_text()
        assert "south_africa" in rows and "canada" in rows


def test_scores_capture_returns_safe_fetch_error_without_writing_results():
    def fail_transport(_url):
        raise TimeoutError("scores failed for fake-key")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = run_scores_capture(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "fake-key"},
            cache_path=root / "scores.json",
            quota_path=root / "quota.json",
            results_out=root / "results.csv",
            transport=fail_transport,
            observed_at="2026-06-12T08:00:00+00:00",
        )

        assert out["status"] == "error"
        assert out["reason"] == "network_error"
        assert out["retryable"] is True
        assert out["attempts"] == 2
        assert "fake-key" not in json.dumps(out)
        assert not (root / "results.csv").exists()
