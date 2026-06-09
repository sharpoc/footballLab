from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from worldcup.preview import build_preview_html
from worldcup.query import project_match_rows


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def export_static_site(snapshot: dict[str, Any], out_dir: str | Path) -> dict[str, str]:
    root = Path(out_dir)
    index_path = root / "index.html"
    snapshot_path = root / "api" / "snapshot" / "latest.json"
    matches_path = root / "api" / "matches.json"
    manifest_path = root / "manifest.json"

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(build_preview_html(snapshot), encoding="utf-8")
    _write_json(snapshot_path, {"snapshot": snapshot})
    _write_json(matches_path, {"matches": project_match_rows(snapshot)})

    manifest = {
        "schema_version": 1,
        "snapshot_at": snapshot.get("snapshot_at"),
        "run_id": (snapshot.get("run") or {}).get("run_id"),
        "files": [
            "index.html",
            "api/snapshot/latest.json",
            "api/matches.json",
        ],
    }
    _write_json(manifest_path, manifest)
    return {
        "index_path": str(index_path),
        "snapshot_path": str(snapshot_path),
        "matches_path": str(matches_path),
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
