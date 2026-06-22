from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Sequence

from worldcup.collectors.csl_results import (
    compare_csl_sources,
    parse_csl_result_rows,
    write_replay_candidate_csv,
)


DEFAULT_OUTPUT = "data/local/diagnostics/csl_results_source_probe.json"


def read_sample_rows(path: str | Path) -> list[dict[str, Any]]:
    sample_path = Path(path)
    suffix = sample_path.suffix.lower()
    if suffix == ".csv":
        with sample_path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    if suffix == ".json":
        with sample_path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise ValueError(f"sample JSON must be a list of row objects: {sample_path}")
        return payload
    raise ValueError(f"unsupported sample file suffix: {sample_path.suffix}")


def _write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a local-only dry-run probe for saved CSL result samples.",
    )
    parser.add_argument("--competition", default="csl_2026")
    parser.add_argument("--primary-source-id", required=True)
    parser.add_argument("--primary-sample", required=True)
    parser.add_argument("--check-source-id", required=True)
    parser.add_argument("--check-sample", required=True)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--write-replay-candidate", default=None)
    parser.add_argument("--min-valid-matches", type=int, default=300)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    primary_rows = read_sample_rows(args.primary_sample)
    check_rows = read_sample_rows(args.check_sample)
    primary = parse_csl_result_rows(
        primary_rows,
        competition_id=args.competition,
        source_id=args.primary_source_id,
        source_role="primary",
    )
    check = parse_csl_result_rows(
        check_rows,
        competition_id=args.competition,
        source_id=args.check_source_id,
        source_role="check",
    )
    result = compare_csl_sources(
        primary,
        check,
        min_valid_matches=args.min_valid_matches,
    )
    diagnostics = result.to_diagnostics()
    _write_json(args.output, diagnostics)

    if args.write_replay_candidate and result.pending_gate["can_enter_replay"]:
        write_replay_candidate_csv(args.write_replay_candidate, result.clean_rows)

    return 0 if not result.quality["manual_review_required"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
