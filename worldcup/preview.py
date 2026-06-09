from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from worldcup.ledger_html import build_research_ledger_html


def build_preview_html(snapshot: dict[str, Any]) -> str:
    return build_research_ledger_html(snapshot)


def write_preview(snapshot: dict[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_preview_html(snapshot), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a static local HTML preview from an analysis snapshot.")
    parser.add_argument("--snapshot", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--out", default="data/cache/preview.html")
    args = parser.parse_args(argv)

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    write_preview(snapshot, args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
