from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.csl_snapshot_archive import (
    archive_snapshot,
    main as archive_main,
    target_snapshot_path,
)


def _snapshot(
    *,
    snapshot_at: str = "2026-07-03T11:30:00+00:00",
    competition_id: str = "csl_2026",
    matches: list[dict] | None = None,
) -> dict:
    return {
        "snapshot_at": snapshot_at,
        "competition": {"id": competition_id, "name": "Chinese Super League"},
        "matches": matches
        if matches is not None
        else [
            {
                "kickoff_at_utc": "2026-07-03T12:00:00+00:00",
                "home_team": "Yunnan Yukun",
                "away_team": "Henan FC",
                "market": {
                    "1x2": {
                        "bookmaker": "must-not-leak",
                        "odds": {"home": 2.4, "draw": 3.4, "away": 3.0},
                    }
                },
            }
        ],
    }


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_target_path_uses_snapshot_at_utc_stamp():
    with TemporaryDirectory() as tmp:
        history = Path(tmp) / "history"

        path = target_snapshot_path(
            _snapshot(snapshot_at="2026-07-03T19:30:00+08:00"),
            history,
        )

        assert path == history / "snapshot_20260703T113000Z-live.json"


def test_archive_writes_csl_snapshot_to_history_with_stable_summary():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        history = root / "data/local/diagnostics/csl_history"
        _write_json(source, _snapshot())

        summary = archive_snapshot(source=source, history=history)

        archived = history / "snapshot_20260703T113000Z-live.json"
        assert summary == {
            "status": "created",
            "created": True,
            "duplicate": False,
            "dry_run": False,
            "competition_id": "csl_2026",
            "snapshot_at": "2026-07-03T11:30:00Z",
            "matches": 1,
            "source": str(source),
            "path": str(archived),
        }
        assert json.loads(archived.read_text(encoding="utf-8")) == _snapshot()


def test_archive_is_idempotent_for_same_snapshot_content():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "csl_live_league_snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot())

        first = archive_snapshot(source=source, history=history)
        second = archive_snapshot(source=source, history=history)

        assert first["status"] == "created"
        assert second["status"] == "duplicate"
        assert second["created"] is False
        assert second["duplicate"] is True
        assert len(list(history.glob("snapshot_*.json"))) == 1


def test_archive_dry_run_validates_without_writing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "csl_live_league_snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot())

        summary = archive_snapshot(source=source, history=history, dry_run=True)

        assert summary["status"] == "dry_run"
        assert summary["created"] is False
        assert not list(history.glob("snapshot_*.json"))


def test_archive_rejects_wrong_competition_without_writing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot(competition_id="fifa_world_cup_2026"))

        try:
            archive_snapshot(source=source, history=history)
        except ValueError as exc:
            assert "unexpected_competition" in str(exc)
        else:
            raise AssertionError("expected wrong competition to be rejected")

        assert not history.exists()


def test_archive_rejects_empty_matches_without_writing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot(matches=[]))

        try:
            archive_snapshot(source=source, history=history)
        except ValueError as exc:
            assert "insufficient_matches" in str(exc)
        else:
            raise AssertionError("expected empty snapshot to be rejected")

        assert not history.exists()


def test_cli_archives_from_root_defaults_and_prints_safe_summary():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        _write_json(source, _snapshot())
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = archive_main(["--root", str(root)])

        assert exit_code == 0
        summary = json.loads(stdout.getvalue())
        assert summary["status"] == "created"
        assert summary["matches"] == 1
        assert summary["path"].endswith(
            "data/local/diagnostics/csl_history/snapshot_20260703T113000Z-live.json"
        )
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        for forbidden in ("bookmaker", "must-not-leak", "odds", "api_key", "secret"):
            assert forbidden not in serialized
