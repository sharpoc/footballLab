from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from worldcup.preview import build_preview_html
from worldcup.query import project_finished_rows, project_match_rows


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _as_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, list):
        return len(value)
    return 1


def _quality_count(snapshot: dict[str, Any], key: str) -> int:
    data_quality = snapshot.get("data_quality") or {}
    if key in data_quality:
        return _as_count(data_quality.get(key))
    return _as_count((snapshot.get("run") or {}).get(key))


def build_public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "snapshot_at": snapshot.get("snapshot_at"),
        "counts": dict(snapshot.get("counts") or {}),
        "data_quality": {
            "source_error_count": _quality_count(snapshot, "source_errors"),
            "stale_source_count": _quality_count(snapshot, "stale_sources"),
            "missing_odds_count": _quality_count(snapshot, "missing_odds"),
            "missing_elo_count": _quality_count(snapshot, "missing_elo"),
            "time_mismatch_count": _quality_count(snapshot, "time_mismatches"),
            "enrichment_error_count": _quality_count(snapshot, "enrichment_errors"),
        },
        "matches": project_match_rows(snapshot),
        "finished": project_finished_rows(snapshot),
    }


def export_static_site(snapshot: dict[str, Any], out_dir: str | Path) -> dict[str, str]:
    root = Path(out_dir)
    index_path = root / "index.html"
    snapshot_path = root / "api" / "snapshot" / "latest.json"
    matches_path = root / "api" / "matches.json"
    finished_path = root / "api" / "finished.json"
    manifest_path = root / "manifest.json"

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(build_preview_html(snapshot), encoding="utf-8")
    _write_json(snapshot_path, {"snapshot": build_public_snapshot(snapshot)})
    _write_json(matches_path, {"matches": project_match_rows(snapshot)})
    _write_json(finished_path, {"finished": project_finished_rows(snapshot)})

    manifest = {
        "schema_version": 1,
        "snapshot_at": snapshot.get("snapshot_at"),
        "files": [
            "index.html",
            "api/snapshot/latest.json",
            "api/matches.json",
            "api/finished.json",
        ],
    }
    _write_json(manifest_path, manifest)
    return {
        "index_path": str(index_path),
        "snapshot_path": str(snapshot_path),
        "matches_path": str(matches_path),
        "finished_path": str(finished_path),
        "manifest_path": str(manifest_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a static local site bundle from a snapshot.")
    parser.add_argument("--snapshot", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--out-dir", default="data/cache/site")
    args = parser.parse_args(argv)

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    result = export_static_site(snapshot, args.out_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
