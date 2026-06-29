# CSL Observation Report And Pending Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-only CSL observation report and a historical club-rating pending gate so CSL can be reviewed during the season without lifting `club_rating_pending` prematurely.

**Architecture:** Add two focused standard-library CLIs. `worldcup.csl_observation_report` reads an existing local CSL league snapshot and produces a sanitized Markdown/JSON observation report from model probabilities, market probabilities, EV/Edge, signal caps, and data-quality warnings. `worldcup.csl_pending_gate` reads the local `club_results_csl_2026.csv`, runs a walk-forward club-rating replay over historical finished matches, compares model metrics with simple baselines, and emits a gate decision that keeps `can_lift_club_rating_pending=false` until historical market/closing-odds validation exists.

**Tech Stack:** Python standard library, existing `worldcup.league_runner` snapshot contract, existing `worldcup.club_rating`, existing `worldcup.backtest` metric helpers, ignored local `data/cache/` and `data/local/diagnostics/`, current custom `tests/run_tests.py`, no new dependencies.

---

## Scope And Safety

P9.14 implements local review artifacts only.

Do not:

- call The Odds API
- read `.env`
- consume quota
- publish snapshots
- deploy
- modify ECS, SQLite server state, LaunchAgent, nginx, DNS, or cloud resources
- change `worldcup/competitions.py`
- lift `csl_2026.rating_policy`
- output stake sizes, bankroll language, execution advice, chasing, parlays, or "bet this" phrasing
- show raw bookmaker names, raw provider payloads, API keys, HMAC values, URLs containing secrets, or `.env` values

The observation report may show model probabilities, market probabilities, EV, Edge, final grade, raw grade, and reasons because those are derived research outputs already present in the local snapshot. It must not show raw bookmaker rows or per-book prices.

The pending gate must be conservative:

- Historical CSL result cache currently has results but not historical closing odds.
- Without historical market/closing odds, the gate may report model health versus simple baselines, but must keep `can_lift_club_rating_pending=false`.
- A healthy result can only recommend `observe_only_no_lift`.
- A weak, small, or invalid result must recommend `keep_pending`.

No commit is allowed during execution unless the user separately confirms a local commit.

## Current Baseline

Current local CSL state after P9.13:

```text
CSL live odds: ok events=8 fixtures=8 odds_events=8
observed_at=2026-06-29T02:32:31.106142+00:00
provider=theoddsapi_secondary quota_remaining=34 quota_last=3
guards: synthetic=false alias_unmatched=0 invalid_odds=0 issues=none
runner: ok matches=8 rating_policy=club_rating_pending club_rating=sample_replay replayed=840 teams=22
warnings=club_rating_pending,odds_event_only strong_grades=none
```

The local snapshot path produced by the current workflow:

```text
data/local/diagnostics/csl_live_league_snapshot.json
```

The club-rating input cache path:

```text
data/cache/club_results_csl_2026.csv
```

## File Structure

- Create: `worldcup/csl_observation_report.py`
  - Reads a CSL league snapshot JSON.
  - Builds a safe `schema_version=1` report payload.
  - Formats Markdown or JSON.
  - Writes under ignored `data/cache/` by default.
- Create: `tests/test_csl_observation_report.py`
  - Tests report payload, Markdown formatting, safety filtering, default path, and CLI behavior.
- Create: `worldcup/csl_pending_gate.py`
  - Loads `club_results_csl_2026.csv`.
  - Builds walk-forward `BacktestMatch` rows without market odds.
  - Compares model 1X2 metrics with uniform and home-prior baselines.
  - Emits a conservative pending gate JSON/Markdown report.
- Create: `tests/test_csl_pending_gate.py`
  - Tests walk-forward no-leakage behavior, metric summaries, conservative no-market gate, malformed input handling, and CLI writing.
- Modify: `README.md`
  - Document local CSL observation report and pending gate commands.
- Modify after implementation: `RECENT_WORK.md`
  - Record implementation, safety boundaries, verification, and generated ignored artifacts.

## Output Contracts

### Observation Report Payload

```python
{
    "schema_version": 1,
    "generated_at": "2026-06-29T10:40:00Z",
    "mode": "local_csl_observation",
    "research_notice": "仅用于研究分析，不构成投注建议。",
    "competition": {"id": "csl_2026", "name": "中超 2026"},
    "snapshot_at": "2026-06-29T02:32:31.106142+00:00",
    "status": "warn",
    "counts": {"matches": 8, "final_strong_grades": 0, "raw_strong_candidates": 3},
    "warnings": ["club_rating_pending", "odds_event_only"],
    "data_quality": {
        "fixture_source": "odds_event_only",
        "club_alias_unmatched": [],
        "invalid_odds_count": 0,
        "club_rating": {"mode": "sample_replay", "matches_replayed": 840, "teams_rated": 22},
    },
    "matches": [
        {
            "source_event_id": "event-id",
            "kickoff_at_utc": "2026-07-03T12:00:00+00:00",
            "home_team": "Yunnan Yukun",
            "away_team": "Henan FC",
            "elo": {"home": 1556, "away": 1556},
            "model_1x2": {"home": 0.4741, "draw": 0.2446, "away": 0.2813},
            "market_1x2": {"home": 0.3837, "draw": 0.2646, "away": 0.3517},
            "ou_2_5": {"model_over": 0.5701, "market_over": 0.6051},
            "signals": [
                {
                    "market_type": "1X2_90min",
                    "selection": "home",
                    "grade": "B",
                    "raw_grade": "S",
                    "ev": 0.1335,
                    "edge": 0.0904,
                    "reasons": ["ah_not_supporting_1x2"],
                }
            ],
        }
    ],
}
```

### Pending Gate Payload

```python
{
    "schema_version": 1,
    "generated_at": "2026-06-29T10:40:00Z",
    "mode": "local_csl_pending_gate",
    "research_notice": "仅用于研究分析，不构成投注建议。",
    "competition_id": "csl_2026",
    "source": "data/cache/club_results_csl_2026.csv",
    "sample": {
        "total_results": 840,
        "warmup_matches": 300,
        "evaluated_matches": 540,
        "min_eval_matches": 200,
        "sample_too_small": false,
        "has_market_odds": false,
    },
    "metrics": {
        "model_1x2": {"n": 540, "brier": 0.61, "log_loss": 0.98},
        "uniform_1x2": {"n": 540, "brier": 0.67, "log_loss": 1.10},
        "home_prior_1x2": {"n": 540, "brier": 0.64, "log_loss": 1.04},
    },
    "checks": {
        "sample_size_ok": true,
        "model_beats_uniform_brier": true,
        "model_beats_home_prior_brier": true,
        "market_baseline_available": false,
    },
    "decision": {
        "status": "observe_only_no_lift",
        "can_lift_club_rating_pending": false,
        "reasons": ["historical_market_odds_missing"],
    },
}
```

Numeric examples above are shape examples. Tests must assert structure and deterministic behavior using local fixtures, not these exact production metric values.

## Task 1: Observation Report Contract Tests

**Files:**
- Create: `tests/test_csl_observation_report.py`
- Create in Task 2: `worldcup/csl_observation_report.py`

- [ ] **Step 1: Write failing tests for report building and Markdown safety**

Create `tests/test_csl_observation_report.py`:

```python
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.csl_observation_report import (
    build_observation_report,
    default_report_path,
    format_observation_markdown,
    main as observation_main,
)


def _snapshot() -> dict:
    return {
        "snapshot_at": "2026-06-29T02:32:31.106142+00:00",
        "competition": {
            "id": "csl_2026",
            "name": "中超 2026",
            "rating_policy": "club_rating_pending",
        },
        "counts": {"fixtures": 1, "odds_events": 1, "match_inputs": 1, "matches": 1},
        "data_quality": {
            "fixture_source": "odds_event_only",
            "warnings": ["club_rating_pending", "odds_event_only"],
            "club_alias_unmatched": [],
            "invalid_odds_count": 0,
            "invalid_odds_examples": [],
            "club_rating": {
                "mode": "sample_replay",
                "source": "data/cache/club_results_csl_2026.csv",
                "competition_id": "csl_2026",
                "matches_replayed": 840,
                "teams_rated": 22,
                "missing_teams": [],
                "skipped_rows": 0,
                "sample_too_small": False,
                "errors": [],
            },
        },
        "matches": [
            {
                "source_event_id": "csl-event-1",
                "kickoff_at_utc": "2026-07-03T12:00:00+00:00",
                "home_team": "Yunnan Yukun",
                "away_team": "Henan FC",
                "elo": {"home": 1556, "away": 1556},
                "model": {
                    "combined_1x2": {"home": 0.4741499, "draw": 0.2445535, "away": 0.2812966},
                    "ou_2_5": {"over": 0.5701251, "under": 0.4298749},
                    "mu_total": 2.9703123,
                },
                "market": {
                    "1x2": {
                        "market_probs": {"home": 0.3837166, "draw": 0.2646045, "away": 0.3516789},
                        "odds": {"home": 2.39, "draw": 3.46, "away": 2.61},
                        "n_books_by_selection": {"home": 18, "draw": 18, "away": 18},
                    },
                    "ou_2_5": {
                        "line": 2.5,
                        "market_probs": {"over": 0.6050856, "under": 0.3949144},
                        "odds": {"over": 1.52, "under": 2.33},
                        "n_books_by_selection": {"over": 5, "under": 5},
                    },
                },
                "signals": [
                    {
                        "market_type": "1X2_90min",
                        "selection": "home",
                        "grade": "B",
                        "raw_grade": "S",
                        "ev": 0.1334817,
                        "edge": 0.0904333,
                        "status": "OK",
                        "reasons": ["ah_not_supporting_1x2"],
                    },
                    {
                        "market_type": "OU_2_5_90min",
                        "selection": "under",
                        "grade": "C",
                        "raw_grade": "C",
                        "ev": -0.02,
                        "edge": -0.01,
                        "status": "OK",
                        "reasons": [],
                    },
                ],
            }
        ],
    }


def test_build_observation_report_sanitizes_snapshot_and_counts_caps():
    report = build_observation_report(
        _snapshot(),
        generated_at="2026-06-29T10:40:00Z",
    )

    assert report["schema_version"] == 1
    assert report["mode"] == "local_csl_observation"
    assert report["generated_at"] == "2026-06-29T10:40:00Z"
    assert report["status"] == "warn"
    assert report["competition"]["id"] == "csl_2026"
    assert report["counts"]["matches"] == 1
    assert report["counts"]["final_strong_grades"] == 0
    assert report["counts"]["raw_strong_candidates"] == 1
    assert report["warnings"] == ["club_rating_pending", "odds_event_only"]
    assert report["data_quality"]["club_rating"]["mode"] == "sample_replay"

    match = report["matches"][0]
    assert match["home_team"] == "Yunnan Yukun"
    assert match["away_team"] == "Henan FC"
    assert match["model_1x2"]["home"] == 0.4741
    assert match["market_1x2"]["away"] == 0.3517
    assert match["ou_2_5"] == {"line": 2.5, "model_over": 0.5701, "market_over": 0.6051}
    assert match["signals"] == [
        {
            "market_type": "1X2_90min",
            "selection": "home",
            "grade": "B",
            "raw_grade": "S",
            "ev": 0.1335,
            "edge": 0.0904,
            "status": "OK",
            "reasons": ["ah_not_supporting_1x2"],
        }
    ]

    text = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert "\"odds\"" not in text
    assert "bookmaker" not in text
    assert "api_key" not in text
    assert "stake" not in text.lower()


def test_format_observation_markdown_is_reviewable_and_research_only():
    report = build_observation_report(
        _snapshot(),
        generated_at="2026-06-29T10:40:00Z",
    )

    markdown = format_observation_markdown(report)

    assert markdown.startswith("# CSL Observation Report\n")
    assert "仅用于研究分析，不构成投注建议。" in markdown
    assert "status: warn" in markdown
    assert "matches: 1" in markdown
    assert "raw strong candidates: 1" in markdown
    assert "final strong grades: 0" in markdown
    assert "Yunnan Yukun vs Henan FC" in markdown
    assert "1X2_90min home grade=B raw=S EV=0.1335 Edge=0.0904" in markdown
    assert "club_rating_pending" in markdown
    assert "下注" not in markdown
    assert "stake" not in markdown.lower()
    assert "2.39" not in markdown


def test_default_observation_report_path_uses_cache_timestamp():
    path = default_report_path(
        Path("/tmp/worldcup"),
        generated_at="2026-06-29T10:40:00Z",
        output_format="markdown",
    )

    assert path == Path("/tmp/worldcup/data/cache/csl_observation_report_20260629T104000Z.md")


def test_observation_report_cli_writes_markdown():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_text(json.dumps(_snapshot(), ensure_ascii=False), encoding="utf-8")
        out = root / "data/cache/custom_csl_report.md"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = observation_main(
                [
                    "--root",
                    str(root),
                    "--snapshot",
                    str(snapshot_path),
                    "--generated-at",
                    "2026-06-29T10:40:00Z",
                    "--out",
                    str(out),
                ]
            )

        assert exit_code == 0
        assert out.exists()
        summary = json.loads(stdout.getvalue())
        assert summary == {
            "status": "warn",
            "matches": 1,
            "raw_strong_candidates": 1,
            "final_strong_grades": 0,
            "format": "markdown",
            "path": str(out),
        }
        assert "CSL Observation Report" in out.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run focused tests and verify they fail because module is missing**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_observation_report -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'worldcup.csl_observation_report'`.

## Task 2: Observation Report Implementation

**Files:**
- Create: `worldcup/csl_observation_report.py`
- Test: `tests/test_csl_observation_report.py`

- [ ] **Step 1: Implement the local observation report module**

Create `worldcup/csl_observation_report.py`:

```python
"""Local CSL observation report.

Reads a generated CSL league snapshot and writes a safe research report.
No network, no .env, no quota use, no publish, no deployment.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESEARCH_NOTICE = "仅用于研究分析，不构成投注建议。"
STRONG_GRADES = {"S", "A"}


def _normalize_generated_at(value: str | None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp_for_path(generated_at: str) -> str:
    parsed = datetime.fromisoformat(_normalize_generated_at(generated_at).replace("Z", "+00:00"))
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def default_report_path(root: Path, generated_at: str, output_format: str) -> Path:
    suffix = "md" if output_format == "markdown" else "json"
    return root / "data/cache" / f"csl_observation_report_{_timestamp_for_path(generated_at)}.{suffix}"


def _round_prob_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for key, raw in value.items():
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            out[str(key)] = round(float(raw), 4)
    return out


def _round_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 4)
    return None


def _club_rating_quality(raw: dict[str, Any]) -> dict[str, Any]:
    club = raw.get("club_rating") if isinstance(raw.get("club_rating"), dict) else {}
    return {
        "mode": club.get("mode"),
        "matches_replayed": int(club.get("matches_replayed") or 0),
        "teams_rated": int(club.get("teams_rated") or 0),
        "sample_too_small": bool(club.get("sample_too_small")),
        "missing_teams": list(club.get("missing_teams") or []),
        "errors": list(club.get("errors") or []),
    }


def _safe_signals(match: dict[str, Any]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for signal in match.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        grade = str(signal.get("grade") or "")
        raw_grade = str(signal.get("raw_grade") or grade)
        ev = _round_float(signal.get("ev"))
        edge = _round_float(signal.get("edge"))
        if grade in STRONG_GRADES or raw_grade in STRONG_GRADES or ev is not None or edge is not None:
            safe.append(
                {
                    "market_type": str(signal.get("market_type") or ""),
                    "selection": str(signal.get("selection") or ""),
                    "grade": grade,
                    "raw_grade": raw_grade,
                    "ev": ev,
                    "edge": edge,
                    "status": str(signal.get("status") or ""),
                    "reasons": list(signal.get("reasons") or []),
                }
            )
    return safe


def _safe_match(match: dict[str, Any]) -> dict[str, Any]:
    model = match.get("model") if isinstance(match.get("model"), dict) else {}
    market = match.get("market") if isinstance(match.get("market"), dict) else {}
    market_1x2 = market.get("1x2") if isinstance(market.get("1x2"), dict) else {}
    market_ou = market.get("ou_2_5") if isinstance(market.get("ou_2_5"), dict) else {}
    model_ou = model.get("ou_2_5") if isinstance(model.get("ou_2_5"), dict) else {}
    return {
        "source_event_id": str(match.get("source_event_id") or ""),
        "kickoff_at_utc": str(match.get("kickoff_at_utc") or ""),
        "home_team": str(match.get("home_team") or ""),
        "away_team": str(match.get("away_team") or ""),
        "elo": dict(match.get("elo") or {}),
        "model_1x2": _round_prob_map(model.get("combined_1x2")),
        "market_1x2": _round_prob_map(market_1x2.get("market_probs")),
        "ou_2_5": {
            "line": _round_float(market_ou.get("line")),
            "model_over": _round_float(model_ou.get("over")),
            "market_over": _round_float((market_ou.get("market_probs") or {}).get("over") if isinstance(market_ou.get("market_probs"), dict) else None),
        },
        "signals": _safe_signals(match),
    }


def build_observation_report(snapshot: dict[str, Any], *, generated_at: str | None = None) -> dict[str, Any]:
    generated = _normalize_generated_at(generated_at)
    quality = snapshot.get("data_quality") if isinstance(snapshot.get("data_quality"), dict) else {}
    warnings = list(quality.get("warnings") or [])
    matches = [_safe_match(match) for match in snapshot.get("matches") or [] if isinstance(match, dict)]
    final_strong = 0
    raw_strong = 0
    for match in matches:
        for signal in match["signals"]:
            if signal["grade"] in STRONG_GRADES:
                final_strong += 1
            if signal["raw_grade"] in STRONG_GRADES:
                raw_strong += 1
    status = "warn" if warnings or raw_strong or final_strong else "ok"
    return {
        "schema_version": 1,
        "generated_at": generated,
        "mode": "local_csl_observation",
        "research_notice": RESEARCH_NOTICE,
        "competition": {
            "id": (snapshot.get("competition") or {}).get("id"),
            "name": (snapshot.get("competition") or {}).get("name"),
            "rating_policy": (snapshot.get("competition") or {}).get("rating_policy"),
        },
        "snapshot_at": snapshot.get("snapshot_at"),
        "status": status,
        "counts": {
            "matches": len(matches),
            "final_strong_grades": final_strong,
            "raw_strong_candidates": raw_strong,
        },
        "warnings": warnings,
        "data_quality": {
            "fixture_source": quality.get("fixture_source"),
            "club_alias_unmatched": list(quality.get("club_alias_unmatched") or []),
            "invalid_odds_count": int(quality.get("invalid_odds_count") or 0),
            "club_rating": _club_rating_quality(quality),
        },
        "matches": matches,
    }


def format_observation_markdown(report: dict[str, Any]) -> str:
    counts = report.get("counts") or {}
    lines = [
        "# CSL Observation Report",
        "",
        f"generated_at: {report.get('generated_at')}",
        f"snapshot_at: {report.get('snapshot_at')}",
        f"status: {report.get('status')}",
        f"matches: {counts.get('matches', 0)}",
        f"raw strong candidates: {counts.get('raw_strong_candidates', 0)}",
        f"final strong grades: {counts.get('final_strong_grades', 0)}",
        "",
        str(report.get("research_notice") or RESEARCH_NOTICE),
        "",
        "## Data Quality",
        "",
        f"warnings: {', '.join(report.get('warnings') or []) or 'none'}",
        f"club_rating: {(report.get('data_quality') or {}).get('club_rating', {}).get('mode')}",
        f"invalid_odds_count: {(report.get('data_quality') or {}).get('invalid_odds_count', 0)}",
        "",
        "## Matches",
    ]
    for match in report.get("matches") or []:
        lines.extend(
            [
                "",
                f"### {match.get('home_team')} vs {match.get('away_team')}",
                "",
                f"kickoff_at_utc: {match.get('kickoff_at_utc')}",
                f"model_1x2: {match.get('model_1x2')}",
                f"market_1x2: {match.get('market_1x2')}",
                f"ou_2_5: {match.get('ou_2_5')}",
            ]
        )
        signals = match.get("signals") or []
        if signals:
            lines.append("signals:")
            for signal in signals:
                lines.append(
                    "- "
                    f"{signal.get('market_type')} {signal.get('selection')} "
                    f"grade={signal.get('grade')} raw={signal.get('raw_grade')} "
                    f"EV={signal.get('ev')} Edge={signal.get('edge')} "
                    f"reasons={','.join(signal.get('reasons') or []) or 'none'}"
                )
        else:
            lines.append("signals: none")
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict[str, Any], path: Path, output_format: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        path.write_text(format_observation_markdown(report), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a local CSL observation report from a league snapshot.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--snapshot", default="data/local/diagnostics/csl_live_league_snapshot.json")
    parser.add_argument("--generated-at")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    root = Path(args.root)
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = root / snapshot_path
    generated_at = _normalize_generated_at(args.generated_at)
    report = build_observation_report(
        json.loads(snapshot_path.read_text(encoding="utf-8")),
        generated_at=generated_at,
    )
    out = Path(args.out) if args.out else default_report_path(root, generated_at, args.format)
    written = write_report(report, out, args.format)
    print(
        json.dumps(
            {
                "status": report["status"],
                "matches": report["counts"]["matches"],
                "raw_strong_candidates": report["counts"]["raw_strong_candidates"],
                "final_strong_grades": report["counts"]["final_strong_grades"],
                "format": args.format,
                "path": str(written),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_observation_report -v
```

Expected: all tests in `tests.test_csl_observation_report` pass.

- [ ] **Step 3: Run a local observation report smoke**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_observation_report \
  --snapshot data/local/diagnostics/csl_live_league_snapshot.json \
  --generated-at 2026-06-29T10:40:00Z
```

Expected: prints JSON with `matches=8`, `format=markdown`, and a path under `data/cache/csl_observation_report_20260629T104000Z.md`.

Run:

```bash
rg -n -i 'stake|下注金额|重注|追损|串关|api_key|secret|bookmaker' data/cache/csl_observation_report_20260629T104000Z.md
```

Expected: no matches.

## Task 3: Pending Gate Contract Tests

**Files:**
- Create: `tests/test_csl_pending_gate.py`
- Create in Task 4: `worldcup/csl_pending_gate.py`

- [ ] **Step 1: Write failing tests for walk-forward evaluation and conservative gate**

Create `tests/test_csl_pending_gate.py`:

```python
from __future__ import annotations

import csv
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.club_rating import load_club_results_csv
from worldcup.csl_pending_gate import (
    build_pending_gate_report,
    build_walk_forward_matches,
    default_gate_path,
    format_pending_gate_markdown,
    main as pending_gate_main,
)


FIELDNAMES = [
    "competition_id",
    "season",
    "date",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "neutral",
]


def _write_results(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("2026-03-01", "Shanghai Port", "Shandong Taishan", 2, 0),
        ("2026-03-02", "Beijing Guoan", "Henan FC", 1, 1),
        ("2026-03-08", "Shanghai Port", "Henan FC", 2, 1),
        ("2026-03-09", "Shandong Taishan", "Beijing Guoan", 0, 1),
        ("2026-03-15", "Shanghai Port", "Beijing Guoan", 1, 0),
        ("2026-03-16", "Henan FC", "Shandong Taishan", 1, 2),
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for date, home, away, hs, away_score in rows:
            writer.writerow(
                {
                    "competition_id": "csl_2026",
                    "season": "2026",
                    "date": date,
                    "home_team": home,
                    "away_team": away,
                    "home_score": str(hs),
                    "away_score": str(away_score),
                    "neutral": "0",
                }
            )


def test_build_walk_forward_matches_uses_only_prior_results_for_ratings():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.csv"
        _write_results(path)
        results = load_club_results_csv(path, "csl_2026")

        rows = build_walk_forward_matches(results, warmup_matches=2, home_adv=100.0)

        assert len(rows) == 4
        first = rows[0]
        assert first.match_id == "csl_2026:2026-03-08:shanghai_port:henan"
        assert first.home_team == "shanghai_port"
        assert first.away_team == "henan"
        assert first.home_score == 2
        assert first.away_score == 1
        assert first.neutral is False
        assert first.home_elo_before > 1500
        assert first.away_elo_before < 1500
        assert first.odds_1x2 is None


def test_build_pending_gate_report_keeps_no_lift_without_market_odds():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.csv"
        _write_results(path)
        results = load_club_results_csv(path, "csl_2026")

        report = build_pending_gate_report(
            results,
            competition_id="csl_2026",
            source=str(path),
            generated_at="2026-06-29T10:50:00Z",
            warmup_matches=2,
            min_eval_matches=3,
        )

        assert report["schema_version"] == 1
        assert report["mode"] == "local_csl_pending_gate"
        assert report["sample"]["total_results"] == 6
        assert report["sample"]["warmup_matches"] == 2
        assert report["sample"]["evaluated_matches"] == 4
        assert report["sample"]["sample_too_small"] is False
        assert report["sample"]["has_market_odds"] is False
        assert report["metrics"]["model_1x2"]["n"] == 4
        assert report["metrics"]["uniform_1x2"]["n"] == 4
        assert report["metrics"]["home_prior_1x2"]["n"] == 4
        assert report["checks"]["market_baseline_available"] is False
        assert report["decision"]["can_lift_club_rating_pending"] is False
        assert report["decision"]["status"] in {"observe_only_no_lift", "keep_pending"}
        assert "historical_market_odds_missing" in report["decision"]["reasons"]


def test_pending_gate_marks_small_sample_as_keep_pending():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.csv"
        _write_results(path)
        results = load_club_results_csv(path, "csl_2026")

        report = build_pending_gate_report(
            results,
            competition_id="csl_2026",
            source=str(path),
            generated_at="2026-06-29T10:50:00Z",
            warmup_matches=4,
            min_eval_matches=10,
        )

        assert report["sample"]["evaluated_matches"] == 2
        assert report["sample"]["sample_too_small"] is True
        assert report["decision"] == {
            "status": "keep_pending",
            "can_lift_club_rating_pending": False,
            "reasons": ["sample_too_small", "historical_market_odds_missing"],
        }


def test_format_pending_gate_markdown_is_clear_and_safe():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.csv"
        _write_results(path)
        results = load_club_results_csv(path, "csl_2026")
        report = build_pending_gate_report(
            results,
            competition_id="csl_2026",
            source=str(path),
            generated_at="2026-06-29T10:50:00Z",
            warmup_matches=2,
            min_eval_matches=3,
        )

        markdown = format_pending_gate_markdown(report)

        assert markdown.startswith("# CSL Pending Gate\n")
        assert "仅用于研究分析，不构成投注建议。" in markdown
        assert "can_lift_club_rating_pending: false" in markdown
        assert "historical_market_odds_missing" in markdown
        assert "model_1x2" in markdown
        assert "下注" not in markdown
        assert "stake" not in markdown.lower()


def test_default_gate_path_uses_diagnostics_timestamp():
    path = default_gate_path(
        Path("/tmp/worldcup"),
        generated_at="2026-06-29T10:50:00Z",
        output_format="json",
    )

    assert path == Path("/tmp/worldcup/data/local/diagnostics/csl_pending_gate_20260629T105000Z.json")


def test_pending_gate_cli_writes_json():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_results(cache_dir / "club_results_csl_2026.csv")
        out = root / "data/local/diagnostics/custom_gate.json"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = pending_gate_main(
                [
                    "--root",
                    str(root),
                    "--cache-dir",
                    str(cache_dir),
                    "--generated-at",
                    "2026-06-29T10:50:00Z",
                    "--warmup-matches",
                    "2",
                    "--min-eval-matches",
                    "3",
                    "--out",
                    str(out),
                ]
            )

        assert exit_code == 0
        assert out.exists()
        summary = json.loads(stdout.getvalue())
        assert summary["competition_id"] == "csl_2026"
        assert summary["evaluated_matches"] == 4
        assert summary["can_lift_club_rating_pending"] is False
        assert summary["path"] == str(out)
```

- [ ] **Step 2: Run focused tests and verify they fail because module is missing**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_pending_gate -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'worldcup.csl_pending_gate'`.

## Task 4: Pending Gate Implementation

**Files:**
- Create: `worldcup/csl_pending_gate.py`
- Test: `tests/test_csl_pending_gate.py`

- [ ] **Step 1: Implement the pending gate module**

Create `worldcup/csl_pending_gate.py`:

```python
"""Local CSL club-rating pending gate.

Runs a walk-forward historical evaluation from local CSL results only.
No network, no .env, no quota use, no publish, no deployment.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worldcup.backtest import BacktestMatch, brier_multiclass, log_loss, outcome_1x2, replay_match
from worldcup.club_rating import DEFAULT_CLUB_K, ClubResult, load_club_results_csv
from worldcup.config import load_config
from worldcup.elo_replay import DEFAULT_INITIAL_RATING, update_pair

RESEARCH_NOTICE = "仅用于研究分析，不构成投注建议。"
OUTCOMES = ("home", "draw", "away")


def _normalize_generated_at(value: str | None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp_for_path(generated_at: str) -> str:
    parsed = datetime.fromisoformat(_normalize_generated_at(generated_at).replace("Z", "+00:00"))
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def default_gate_path(root: Path, generated_at: str, output_format: str) -> Path:
    suffix = "md" if output_format == "markdown" else "json"
    return root / "data/local/diagnostics" / f"csl_pending_gate_{_timestamp_for_path(generated_at)}.{suffix}"


def _match_id(result: ClubResult) -> str:
    return f"{result.competition_id}:{result.date}:{result.home_canonical}:{result.away_canonical}"


def build_walk_forward_matches(
    results: list[ClubResult],
    *,
    warmup_matches: int = 300,
    initial: float = DEFAULT_INITIAL_RATING,
    k: float = DEFAULT_CLUB_K,
    home_adv: float = 100.0,
) -> list[BacktestMatch]:
    ratings: dict[str, float] = {}
    rows: list[BacktestMatch] = []
    ordered = sorted(results, key=lambda item: (item.date, item.home_canonical, item.away_canonical))
    for index, result in enumerate(ordered):
        home_before = ratings.get(result.home_canonical, initial)
        away_before = ratings.get(result.away_canonical, initial)
        if index >= warmup_matches:
            rows.append(
                BacktestMatch(
                    match_id=_match_id(result),
                    kickoff_at_utc=f"{result.date}T00:00:00Z",
                    home_team=result.home_canonical,
                    away_team=result.away_canonical,
                    home_score=result.home_score,
                    away_score=result.away_score,
                    home_elo_before=home_before,
                    away_elo_before=away_before,
                    neutral=result.neutral,
                )
            )
        new_home, new_away = update_pair(
            home_before,
            away_before,
            result.home_score,
            result.away_score,
            k=k,
            neutral=result.neutral,
            home_adv=home_adv,
        )
        ratings[result.home_canonical] = new_home
        ratings[result.away_canonical] = new_away
    return rows


def _mean_metrics(rows: list[tuple[dict[str, float], str]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "brier": None, "log_loss": None}
    return {
        "n": len(rows),
        "brier": round(sum(brier_multiclass(probs, outcome) for probs, outcome in rows) / len(rows), 6),
        "log_loss": round(sum(log_loss(probs, outcome) for probs, outcome in rows) / len(rows), 6),
    }


def _home_prior(results: list[ClubResult], warmup_matches: int) -> dict[str, float]:
    warmup = sorted(results, key=lambda item: (item.date, item.home_canonical, item.away_canonical))[:warmup_matches]
    counts = {key: 1 for key in OUTCOMES}
    for result in warmup:
        counts[outcome_1x2(result.home_score, result.away_score)] += 1
    total = sum(counts.values())
    return {key: counts[key] / total for key in OUTCOMES}


def _decision(sample_too_small: bool, model: dict[str, Any], uniform: dict[str, Any], home_prior: dict[str, Any]) -> tuple[str, list[str], dict[str, bool]]:
    checks = {
        "sample_size_ok": not sample_too_small,
        "model_beats_uniform_brier": bool(model.get("brier") is not None and uniform.get("brier") is not None and model["brier"] <= uniform["brier"]),
        "model_beats_home_prior_brier": bool(model.get("brier") is not None and home_prior.get("brier") is not None and model["brier"] <= home_prior["brier"]),
        "market_baseline_available": False,
    }
    reasons: list[str] = []
    if sample_too_small:
        reasons.append("sample_too_small")
    if not checks["model_beats_uniform_brier"]:
        reasons.append("model_not_better_than_uniform_brier")
    if not checks["model_beats_home_prior_brier"]:
        reasons.append("model_not_better_than_home_prior_brier")
    reasons.append("historical_market_odds_missing")
    if sample_too_small or len(reasons) > 1:
        return "keep_pending", reasons, checks
    return "observe_only_no_lift", reasons, checks


def build_pending_gate_report(
    results: list[ClubResult],
    *,
    competition_id: str,
    source: str,
    generated_at: str | None = None,
    warmup_matches: int = 300,
    min_eval_matches: int = 200,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated = _normalize_generated_at(generated_at)
    cfg = cfg or load_config()
    rows = build_walk_forward_matches(
        results,
        warmup_matches=warmup_matches,
        home_adv=float(cfg["elo"]["home_adv"]),
    )
    uniform_probs = {key: 1 / 3 for key in OUTCOMES}
    home_prior_probs = _home_prior(results, warmup_matches)
    model_rows: list[tuple[dict[str, float], str]] = []
    uniform_rows: list[tuple[dict[str, float], str]] = []
    home_prior_rows: list[tuple[dict[str, float], str]] = []
    for row in rows:
        actual = outcome_1x2(row.home_score, row.away_score)
        model_rows.append((replay_match(row, cfg)["model_1x2"], actual))
        uniform_rows.append((uniform_probs, actual))
        home_prior_rows.append((home_prior_probs, actual))
    model = _mean_metrics(model_rows)
    uniform = _mean_metrics(uniform_rows)
    prior = _mean_metrics(home_prior_rows)
    sample_too_small = len(rows) < min_eval_matches
    status, reasons, checks = _decision(sample_too_small, model, uniform, prior)
    return {
        "schema_version": 1,
        "generated_at": generated,
        "mode": "local_csl_pending_gate",
        "research_notice": RESEARCH_NOTICE,
        "competition_id": competition_id,
        "source": source,
        "sample": {
            "total_results": len(results),
            "warmup_matches": warmup_matches,
            "evaluated_matches": len(rows),
            "min_eval_matches": min_eval_matches,
            "sample_too_small": sample_too_small,
            "has_market_odds": False,
        },
        "metrics": {
            "model_1x2": model,
            "uniform_1x2": uniform,
            "home_prior_1x2": prior,
        },
        "checks": checks,
        "decision": {
            "status": status,
            "can_lift_club_rating_pending": False,
            "reasons": reasons,
        },
    }


def format_pending_gate_markdown(report: dict[str, Any]) -> str:
    sample = report.get("sample") or {}
    decision = report.get("decision") or {}
    metrics = report.get("metrics") or {}
    lines = [
        "# CSL Pending Gate",
        "",
        f"generated_at: {report.get('generated_at')}",
        f"competition_id: {report.get('competition_id')}",
        f"source: {report.get('source')}",
        "",
        str(report.get("research_notice") or RESEARCH_NOTICE),
        "",
        "## Decision",
        "",
        f"status: {decision.get('status')}",
        f"can_lift_club_rating_pending: {str(bool(decision.get('can_lift_club_rating_pending'))).lower()}",
        f"reasons: {', '.join(decision.get('reasons') or [])}",
        "",
        "## Sample",
        "",
        f"total_results: {sample.get('total_results')}",
        f"warmup_matches: {sample.get('warmup_matches')}",
        f"evaluated_matches: {sample.get('evaluated_matches')}",
        f"sample_too_small: {str(bool(sample.get('sample_too_small'))).lower()}",
        f"has_market_odds: {str(bool(sample.get('has_market_odds'))).lower()}",
        "",
        "## Metrics",
        "",
    ]
    for key in ("model_1x2", "uniform_1x2", "home_prior_1x2"):
        lines.append(f"{key}: {metrics.get(key)}")
    lines.append("")
    return "\n".join(lines)


def write_gate(report: dict[str, Any], path: Path, output_format: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "markdown":
        path.write_text(format_pending_gate_markdown(report), encoding="utf-8")
    else:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a local CSL club-rating pending gate report.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--competition", "--competition-id", dest="competition_id", default="csl_2026")
    parser.add_argument("--generated-at")
    parser.add_argument("--warmup-matches", type=int, default=300)
    parser.add_argument("--min-eval-matches", type=int, default=200)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    root = Path(args.root)
    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = root / cache_dir
    source = cache_dir / f"club_results_{args.competition_id}.csv"
    generated_at = _normalize_generated_at(args.generated_at)
    results = load_club_results_csv(source, args.competition_id)
    report = build_pending_gate_report(
        results,
        competition_id=args.competition_id,
        source=str(source),
        generated_at=generated_at,
        warmup_matches=args.warmup_matches,
        min_eval_matches=args.min_eval_matches,
    )
    out = Path(args.out) if args.out else default_gate_path(root, generated_at, args.format)
    written = write_gate(report, out, args.format)
    print(
        json.dumps(
            {
                "competition_id": args.competition_id,
                "evaluated_matches": report["sample"]["evaluated_matches"],
                "decision_status": report["decision"]["status"],
                "can_lift_club_rating_pending": report["decision"]["can_lift_club_rating_pending"],
                "path": str(written),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_pending_gate -v
```

Expected: all tests in `tests.test_csl_pending_gate` pass.

- [ ] **Step 3: Run local pending gate smoke**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_pending_gate \
  --cache-dir data/cache \
  --generated-at 2026-06-29T10:50:00Z \
  --warmup-matches 300 \
  --min-eval-matches 200 \
  --out data/local/diagnostics/csl_pending_gate.json
```

Expected: prints JSON with `competition_id="csl_2026"`, `can_lift_club_rating_pending=false`, and `path="data/local/diagnostics/csl_pending_gate.json"`.

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("data/local/diagnostics/csl_pending_gate.json").read_text(encoding="utf-8"))
print(
    {
        "evaluated_matches": payload["sample"]["evaluated_matches"],
        "sample_too_small": payload["sample"]["sample_too_small"],
        "decision": payload["decision"],
        "market_baseline_available": payload["checks"]["market_baseline_available"],
    }
)
assert payload["sample"]["total_results"] == 840
assert payload["sample"]["evaluated_matches"] == 540
assert payload["sample"]["has_market_odds"] is False
assert payload["checks"]["market_baseline_available"] is False
assert payload["decision"]["can_lift_club_rating_pending"] is False
assert "historical_market_odds_missing" in payload["decision"]["reasons"]
PY
```

Expected: exits `0`.

## Task 5: README And Recent Work

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Add README usage under the CSL section**

Modify the CSL section in `README.md` after the `CSL Historical Results Probe` section:

```markdown
### CSL Observation Report And Pending Gate

P9.14 adds two local-only review commands for CSL. They do not call The Odds API, do not read `.env`, do not consume quota, do not publish, do not deploy, and do not modify LaunchAgent.

Generate a local observation report from the latest CSL league snapshot:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_observation_report \
  --snapshot data/local/diagnostics/csl_live_league_snapshot.json
```

Run the conservative club-rating pending gate:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_pending_gate \
  --cache-dir data/cache \
  --out data/local/diagnostics/csl_pending_gate.json
```

The pending gate uses local historical CSL results only. Because the current historical cache does not include historical closing odds, the gate must keep `can_lift_club_rating_pending=false`; a healthy walk-forward result can only support observation mode, not formal S/A CSL signal publication.
```

- [ ] **Step 2: Add a `RECENT_WORK.md` entry after implementation verification**

Add a new top entry:

```markdown
## 2026-06-29 P9.14 CSL observation report and pending gate implementation

- Added local-only `worldcup.csl_observation_report` to turn the latest CSL league snapshot into a safe Markdown/JSON observation report; it shows model probabilities, market probabilities, EV/Edge, raw/final grade caps and data-quality warnings without raw bookmaker rows or per-book prices.
- Added local-only `worldcup.csl_pending_gate` to run walk-forward club-rating evaluation from `data/cache/club_results_csl_2026.csv`; output remains conservative because historical market/closing odds are absent, so `can_lift_club_rating_pending=false`.
- Generated ignored local artifacts under `data/cache/` and `data/local/diagnostics/`; no live refresh, `.env` read, quota use, publish, deploy, LaunchAgent change, commit or push.
- Verification: focused CSL observation and pending gate tests passed; full `tests/run_tests.py` passed; `git diff --check` passed.
```

Update the verification line with the actual observed test counts.

## Task 6: Final Verification

**Files:**
- All files touched in Tasks 1-5

- [ ] **Step 1: Run focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_observation_report tests.test_csl_pending_gate -v
```

Expected: all tests pass.

- [ ] **Step 2: Run full project tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass. The exact count may be higher than the current P9.13 baseline because this plan adds tests.

- [ ] **Step 3: Run local CLI smoke commands**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_observation_report \
  --snapshot data/local/diagnostics/csl_live_league_snapshot.json \
  --generated-at 2026-06-29T10:40:00Z \
  --out data/cache/csl_observation_report_20260629T104000Z.md
```

Expected: exits `0` and writes the Markdown report.

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_pending_gate \
  --cache-dir data/cache \
  --generated-at 2026-06-29T10:50:00Z \
  --warmup-matches 300 \
  --min-eval-matches 200 \
  --out data/local/diagnostics/csl_pending_gate.json
```

Expected: exits `0`, writes the JSON gate, and keeps `can_lift_club_rating_pending=false`.

- [ ] **Step 4: Scan generated reports for forbidden wording**

Run:

```bash
rg -n -i 'stake|下注金额|重注|追损|串关|api_key|secret|hmac|cookie|private|bookmaker' \
  data/cache/csl_observation_report_20260629T104000Z.md \
  data/local/diagnostics/csl_pending_gate.json
```

Expected: no matches. If `historical_market_odds_missing` includes the substring `odds`, that is acceptable; this command does not flag `odds`.

- [ ] **Step 5: Check formatting and tracked changes**

Run:

```bash
git diff --check
```

Expected: exits `0`.

Run:

```bash
git status --short
```

Expected: tracked changes are limited to:

```text
 M README.md
 M RECENT_WORK.md
?? tests/test_csl_observation_report.py
?? tests/test_csl_pending_gate.py
?? worldcup/csl_observation_report.py
?? worldcup/csl_pending_gate.py
```

The ignored generated artifacts under `data/cache/` and `data/local/diagnostics/` may not appear in normal `git status --short`.

## Commit Gate

Do not commit unless the user explicitly confirms a local commit after reviewing the implemented changes.

If the user confirms, run:

```bash
git add README.md RECENT_WORK.md tests/test_csl_observation_report.py tests/test_csl_pending_gate.py worldcup/csl_observation_report.py worldcup/csl_pending_gate.py
git commit -m "feat: add csl observation gate"
```

Expected: commit succeeds locally. Do not push without a separate explicit push confirmation.

## Antagonistic Self-Review

- Root cause: this plan addresses the real blocker after P9.13: CSL can run locally, but there is no evidence yet to lift `club_rating_pending`.
- Scope control: the plan avoids live refresh, scheduled publish, ECS ingest, LaunchAgent and public UI. It creates review artifacts only.
- Data limitation: the historical result cache lacks closing odds. Therefore the pending gate cannot compare against market closing prices and must keep `can_lift_club_rating_pending=false`.
- Semantic boundary: observation report is not a value-signal publication surface. It can show capped/raw grades for research review, but not formal CSL S/A recommendations.
- Security: reports are derived from snapshots and result CSVs; they must not read `.env` or include raw provider payloads, secrets, URLs, HMAC, or per-bookmaker details.
- Quota: no command in this plan calls The Odds API or consumes quota.
- Verification: implementation must pass focused tests, full tests, CLI smoke, forbidden-word scans, and `git diff --check` before being described as complete.

## Execution Notes

Recommended execution mode: subagent-driven task-by-task implementation with review after each task. Inline execution is also acceptable because the plan touches a small, well-bounded set of files.
