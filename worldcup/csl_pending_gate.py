"""Local CSL club-rating pending gate.

Reads local historical club results only. This module does not fetch sources,
read secrets, touch quota, publish data, or lift the pending gate.
"""
from __future__ import annotations

import argparse
import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worldcup.backtest import (
    BacktestMatch,
    brier_multiclass,
    log_loss,
    outcome_1x2,
    replay_match,
)
from worldcup.club_rating import DEFAULT_CLUB_K, ClubResult, load_club_results_csv
from worldcup.elo_replay import DEFAULT_INITIAL_RATING, update_pair

RESEARCH_NOTICE = "仅用于研究分析，不构成投注建议。"
OUTCOMES = ("home", "draw", "away")
DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_PENDING_GATE_CONFIG: dict[str, Any] = {
    "poisson": {
        "mu_total": 2.6,
        "gd_div": 250,
        "gd_clamp": 2.5,
        "lambda_min": 0.15,
        "lambda_max": 4.5,
        "max_goals": 10,
        "tail_mass_max": 0.01,
        "mu_market_weight": 0.7,
        "dc_rho": 0.0,
        "mu_dr_slope": 0.0,
    },
    "elo": {
        "home_adv": 100,
        "base_draw": 0.28,
        "draw_k": 0.0003,
        "draw_min": 0.12,
        "draw_max": 0.32,
    },
    "ensemble": {
        "w_elo": 0.5,
        "w_poisson": 0.5,
    },
}


@dataclass(frozen=True)
class _ClubBacktestMatch(BacktestMatch):
    home_canonical: str = ""
    away_canonical: str = ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_utc(value: str | None) -> datetime:
    if value in (None, ""):
        return _utc_now()
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _utc_iso(value: str | None) -> str:
    return _parse_utc(value).isoformat().replace("+00:00", "Z")


def _sorted_results(results: list[ClubResult]) -> list[ClubResult]:
    return sorted(
        results,
        key=lambda item: (item.date, item.home_canonical, item.away_canonical),
    )


def build_walk_forward_matches(
    results: list[ClubResult],
    *,
    warmup_matches: int = 300,
    initial: float = DEFAULT_INITIAL_RATING,
    k: float = DEFAULT_CLUB_K,
    home_adv: float = 100.0,
) -> list[BacktestMatch]:
    """Build backtest rows with ratings captured before each evaluated match."""
    sorted_results = _sorted_results(results)
    ratings: dict[str, float] = {}
    rows: list[BacktestMatch] = []
    idx = 0
    while idx < len(sorted_results):
        result_date = sorted_results[idx].date
        day_results: list[ClubResult] = []
        while idx + len(day_results) < len(sorted_results):
            candidate = sorted_results[idx + len(day_results)]
            if candidate.date != result_date:
                break
            day_results.append(candidate)

        day_start_ratings = dict(ratings)
        rating_deltas: dict[str, float] = {}
        for day_offset, result in enumerate(day_results):
            match_idx = idx + day_offset
            home_rating = day_start_ratings.get(result.home_canonical, initial)
            away_rating = day_start_ratings.get(result.away_canonical, initial)
            if match_idx >= warmup_matches:
                rows.append(
                    _ClubBacktestMatch(
                        match_id=(
                            f"{result.competition_id}:{result.date}:"
                            f"{result.home_canonical}:{result.away_canonical}"
                        ),
                        kickoff_at_utc=f"{result.date}T00:00:00Z",
                        home_team=result.home_team,
                        away_team=result.away_team,
                        home_score=result.home_score,
                        away_score=result.away_score,
                        home_elo_before=int(round(home_rating)),
                        away_elo_before=int(round(away_rating)),
                        neutral=result.neutral,
                        odds_1x2=None,
                        home_canonical=result.home_canonical,
                        away_canonical=result.away_canonical,
                    )
                )
            new_home, new_away = update_pair(
                home_rating,
                away_rating,
                result.home_score,
                result.away_score,
                k=k,
                neutral=result.neutral,
                home_adv=home_adv,
            )
            rating_deltas[result.home_canonical] = rating_deltas.get(
                result.home_canonical, 0.0
            ) + (new_home - home_rating)
            rating_deltas[result.away_canonical] = rating_deltas.get(
                result.away_canonical, 0.0
            ) + (new_away - away_rating)

        for team, delta in rating_deltas.items():
            ratings[team] = day_start_ratings.get(team, initial) + delta
        idx += len(day_results)
    return rows


def _mean_metrics(rows: list[tuple[dict[str, float], str]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "brier": None, "log_loss": None}
    n = len(rows)
    return {
        "n": n,
        "brier": round(sum(brier_multiclass(probs, outcome) for probs, outcome in rows) / n, 6),
        "log_loss": round(sum(log_loss(probs, outcome) for probs, outcome in rows) / n, 6),
    }


def _home_prior_probs_before_date(results: list[ClubResult], before_date: str) -> dict[str, float]:
    counts = {outcome: 1 for outcome in OUTCOMES}
    for result in _sorted_results(results):
        if result.date >= before_date:
            break
        counts[outcome_1x2(result.home_score, result.away_score)] += 1
    total = sum(counts.values())
    return {outcome: counts[outcome] / total for outcome in OUTCOMES}


def _competition_id(results: list[ClubResult], explicit: str | None) -> str:
    if explicit:
        return explicit
    if results:
        return results[0].competition_id
    return DEFAULT_COMPETITION_ID


def _cfg_with_home_adv(cfg: dict[str, Any] | None, home_adv: float | None) -> dict[str, Any]:
    out = copy.deepcopy(cfg if cfg is not None else DEFAULT_PENDING_GATE_CONFIG)
    if home_adv is not None:
        out.setdefault("elo", {})["home_adv"] = home_adv
    return out


def _beats(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        isinstance(left.get("brier"), float)
        and isinstance(right.get("brier"), float)
        and left["brier"] < right["brier"]
    )


def _market_baseline_from_report(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"n": 0, "brier": None, "log_loss": None}
    markets = report.get("markets") if isinstance(report.get("markets"), dict) else {}
    x12 = markets.get("1x2") if isinstance(markets.get("1x2"), dict) else {}
    market = x12.get("market") if isinstance(x12.get("market"), dict) else {}
    n = market.get("n", 0)
    if not isinstance(n, int) or n <= 0:
        return {"n": 0, "brier": None, "log_loss": None}
    return {
        "n": n,
        "brier": market.get("brier") if isinstance(market.get("brier"), float) else None,
        "log_loss": (
            market.get("log_loss") if isinstance(market.get("log_loss"), float) else None
        ),
    }


def build_pending_gate_report(
    results: list[ClubResult],
    *,
    source: str | Path,
    competition_id: str | None = None,
    generated_at: str | None = None,
    warmup_matches: int = 300,
    min_eval_matches: int = 200,
    cfg: dict[str, Any] | None = None,
    home_adv: float | None = None,
    market_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if source is None:
        raise ValueError("source is required")
    generated = _utc_iso(generated_at)
    effective_cfg = _cfg_with_home_adv(cfg, home_adv)
    home_adv_value = float(effective_cfg["elo"]["home_adv"])
    rows = build_walk_forward_matches(
        results,
        warmup_matches=warmup_matches,
        home_adv=home_adv_value,
    )

    uniform_probs = {outcome: 1.0 / 3.0 for outcome in OUTCOMES}
    model_rows: list[tuple[dict[str, float], str]] = []
    uniform_rows: list[tuple[dict[str, float], str]] = []
    home_prior_rows: list[tuple[dict[str, float], str]] = []
    for row in rows:
        actual = outcome_1x2(row.home_score, row.away_score)
        home_prior = _home_prior_probs_before_date(results, row.kickoff_at_utc[:10])
        model_rows.append((replay_match(row, effective_cfg)["model_1x2"], actual))
        uniform_rows.append((uniform_probs, actual))
        home_prior_rows.append((home_prior, actual))

    model_metrics = _mean_metrics(model_rows)
    uniform_metrics = _mean_metrics(uniform_rows)
    home_prior_metrics = _mean_metrics(home_prior_rows)
    evaluated = len(rows)
    sample_too_small = evaluated < min_eval_matches
    sample_size_ok = not sample_too_small
    model_beats_uniform = _beats(model_metrics, uniform_metrics)
    model_beats_home_prior = _beats(model_metrics, home_prior_metrics)
    market_baseline = _market_baseline_from_report(market_report)
    market_baseline_available = bool(market_baseline.get("n"))

    if sample_too_small:
        reasons = ["sample_too_small"]
        if not market_baseline_available:
            reasons.append("historical_market_odds_missing")
        status = "keep_pending"
    else:
        reasons = []
        if not model_beats_uniform:
            reasons.append("model_not_beating_uniform_brier")
        if not model_beats_home_prior:
            reasons.append("model_not_beating_home_prior_brier")
        if not market_baseline_available:
            reasons.append("historical_market_odds_missing")
        status = "observe_only_no_lift" if not reasons else "keep_pending"

    sample = {
        "total_results": len(results),
        "warmup_matches": warmup_matches,
        "evaluated_matches": evaluated,
        "min_eval_matches": min_eval_matches,
        "sample_too_small": sample_too_small,
        "has_market_odds": market_baseline_available,
    }
    decision: dict[str, Any] = {
        "status": status,
        "can_lift_club_rating_pending": False,
        "reasons": reasons,
    }

    return {
        "schema_version": 1,
        "mode": "local_csl_pending_gate",
        "generated_at": generated,
        "research_notice": RESEARCH_NOTICE,
        "competition_id": _competition_id(results, competition_id),
        "source": str(source),
        "sample": sample,
        "metrics": {
            "model_1x2": model_metrics,
            "uniform_1x2": uniform_metrics,
            "home_prior_1x2": home_prior_metrics,
            "market_baseline": market_baseline,
        },
        "checks": {
            "sample_size_ok": sample_size_ok,
            "model_beats_uniform_brier": model_beats_uniform,
            "model_beats_home_prior_brier": model_beats_home_prior,
            "market_baseline_available": market_baseline_available,
        },
        "decision": decision,
        "can_lift_club_rating_pending": False,
    }


def default_gate_path(
    root: Path,
    generated_at: str,
    output_format: str = "json",
) -> Path:
    suffix = ".md" if output_format == "markdown" else ".json"
    stamp = _parse_utc(generated_at).strftime("%Y%m%dT%H%M%SZ")
    return root / "data" / "local" / "diagnostics" / f"csl_pending_gate_{stamp}{suffix}"


def _join_values(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "none"
    return ", ".join(str(value) for value in values)


def _fmt_metric(metric: dict[str, Any]) -> str:
    return (
        f"n={metric.get('n', 0)} "
        f"brier={metric.get('brier', 'n/a')} "
        f"log_loss={metric.get('log_loss', 'n/a')}"
    )


def _bool_text(value: Any) -> str:
    return "true" if bool(value) else "false"


def format_pending_gate_markdown(report: dict[str, Any]) -> str:
    sample = report.get("sample") if isinstance(report.get("sample"), dict) else {}
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    lines = [
        "# CSL Pending Gate",
        "",
        str(report.get("research_notice") or RESEARCH_NOTICE),
        "",
        f"generated_at: {report.get('generated_at')}",
        f"competition_id: {report.get('competition_id')}",
        f"status: {decision.get('status')}",
        f"can_lift_club_rating_pending: {_bool_text(report.get('can_lift_club_rating_pending'))}",
        f"reasons: {_join_values(decision.get('reasons'))}",
        "",
        "## Sample",
    ]
    for key in (
        "total_results",
        "warmup_matches",
        "evaluated_matches",
        "min_eval_matches",
        "sample_too_small",
        "has_market_odds",
    ):
        if key in sample:
            value = _bool_text(sample[key]) if isinstance(sample[key], bool) else sample[key]
            lines.append(f"- {key}: {value}")

    lines.extend(["", "## Metrics"])
    for key in ("model_1x2", "uniform_1x2", "home_prior_1x2", "market_baseline"):
        metric = metrics.get(key) if isinstance(metrics.get(key), dict) else {}
        lines.append(f"- {key}: {_fmt_metric(metric)}")
    return "\n".join(lines).rstrip() + "\n"


def write_gate(report: dict[str, Any], path: str | Path, output_format: str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    elif output_format == "markdown":
        content = format_pending_gate_markdown(report)
    else:
        raise ValueError(f"unsupported output format: {output_format}")
    out.write_text(content, encoding="utf-8")
    return out


def _resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _load_market_report(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a local CSL pending gate report.",
        allow_abbrev=False,
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument(
        "--competition",
        "--competition-id",
        dest="competition_id",
        default=DEFAULT_COMPETITION_ID,
    )
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--warmup-matches", type=int, default=300)
    parser.add_argument("--min-eval-matches", type=int, default=200)
    parser.add_argument(
        "--market-report",
        default=None,
        help="Optional local backtest report JSON with CSL market baseline metrics.",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root)
    cache_dir = _resolve_under_root(root, args.cache_dir)
    source = cache_dir / f"club_results_{args.competition_id}.csv"
    results = load_club_results_csv(source, args.competition_id)
    market_report_path = (
        _resolve_under_root(root, args.market_report) if args.market_report else None
    )
    report = build_pending_gate_report(
        results,
        competition_id=args.competition_id,
        source=source,
        generated_at=args.generated_at,
        warmup_matches=args.warmup_matches,
        min_eval_matches=args.min_eval_matches,
        market_report=_load_market_report(market_report_path),
    )
    out_path = (
        _resolve_under_root(root, args.out)
        if args.out is not None
        else default_gate_path(root, report["generated_at"], args.format)
    )
    written = write_gate(report, out_path, args.format)

    summary = {
        "research_notice": report["research_notice"],
        "competition_id": report["competition_id"],
        "evaluated_matches": report["sample"]["evaluated_matches"],
        "decision_status": report["decision"]["status"],
        "can_lift_club_rating_pending": report["can_lift_club_rating_pending"],
        "path": str(written),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
