"""Local CSL observation report builder.

Reads an already-created local league snapshot and writes a sanitized research
report. It does not fetch sources, read secrets, touch quota, or publish data.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESEARCH_NOTICE = "仅用于研究分析，不构成投注建议。"
KNOWN_GRADES = {"S", "A", "B", "C", "D"}
STRONG_GRADES = {"S", "A"}
DEFAULT_SNAPSHOT = "data/local/diagnostics/csl_live_league_snapshot.json"
SAFE_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9_.:+-]+$")
SAFE_TEXT_ALLOWLIST = {"odds_event_only"}
SENSITIVE_TEXT_PARTS = (
    "api_key",
    "secret",
    "token",
    "cookie",
    "private",
    "hmac",
    "provider",
    "bookmaker",
    "stake",
    "price",
    "odds",
    "http",
    "://",
    "must-not-leak",
)


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


def _round4(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _safe_grade(value: Any) -> str:
    grade = str(value or "").upper()
    return grade if grade in KNOWN_GRADES else ""


def _strong(value: Any) -> bool:
    return _safe_grade(value) in STRONG_GRADES


def _contains_price_like_decimal(value: str) -> bool:
    for match in re.finditer(r"\d+\.\d+", value):
        try:
            if float(match.group(0)) >= 1.0:
                return True
        except ValueError:
            continue
    return False


def _safe_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if text in SAFE_TEXT_ALLOWLIST:
        return text
    lowered = text.lower()
    if not SAFE_TEXT_PATTERN.fullmatch(text):
        return None
    if any(part in lowered for part in SENSITIVE_TEXT_PARTS):
        return None
    if _contains_price_like_decimal(text):
        return None
    return text


def _safe_text_list(value: Any) -> list[str]:
    safe_values: list[str] = []
    for item in _list(value):
        safe = _safe_text(item)
        if safe is not None:
            safe_values.append(safe)
    return safe_values


def _safe_competition(snapshot: dict[str, Any]) -> dict[str, Any]:
    competition = snapshot.get("competition") if isinstance(snapshot.get("competition"), dict) else {}
    return {
        "id": _safe_text(competition.get("id")),
        "name": competition.get("name"),
        "rating_policy": _safe_text(competition.get("rating_policy")),
    }


def _safe_club_rating(data_quality: dict[str, Any]) -> dict[str, Any]:
    rating = data_quality.get("club_rating") if isinstance(data_quality.get("club_rating"), dict) else {}
    safe: dict[str, Any] = {}
    mode = _safe_text(rating.get("mode"))
    if mode is not None:
        safe["mode"] = mode
    for key in ("matches_replayed", "teams_rated", "sample_too_small"):
        if key in rating:
            safe[key] = rating.get(key)
    safe["missing_teams"] = _safe_text_list(rating.get("missing_teams"))
    safe["errors"] = _safe_text_list(rating.get("errors"))
    return safe


def _safe_data_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    data_quality = (
        snapshot.get("data_quality") if isinstance(snapshot.get("data_quality"), dict) else {}
    )
    return {
        "fixture_source": _safe_text(data_quality.get("fixture_source")),
        "club_alias_unmatched": _safe_text_list(data_quality.get("club_alias_unmatched")),
        "invalid_odds_count": data_quality.get("invalid_odds_count", 0),
        "club_rating": _safe_club_rating(data_quality),
    }


def _rounded_probs(block: Any, keys: tuple[str, ...]) -> dict[str, float | None]:
    if not isinstance(block, dict):
        block = {}
    return {key: _round4(block.get(key)) for key in keys}


def _safe_elo(match: dict[str, Any]) -> dict[str, Any]:
    elo = match.get("elo") if isinstance(match.get("elo"), dict) else {}
    return {key: elo.get(key) for key in ("home", "away") if key in elo}


def _safe_ou(match: dict[str, Any]) -> dict[str, float | None]:
    model = match.get("model") if isinstance(match.get("model"), dict) else {}
    market = match.get("market") if isinstance(match.get("market"), dict) else {}
    model_ou = model.get("ou_2_5") if isinstance(model.get("ou_2_5"), dict) else {}
    market_ou = market.get("ou_2_5") if isinstance(market.get("ou_2_5"), dict) else {}
    market_probs = (
        market_ou.get("market_probs") if isinstance(market_ou.get("market_probs"), dict) else {}
    )
    line = market_ou.get("line", model.get("ou_line"))
    return {
        "line": _round4(line),
        "model_over": _round4(model_ou.get("over")),
        "market_over": _round4(market_probs.get("over")),
    }


def _signal_reportable(signal: dict[str, Any]) -> bool:
    return _strong(signal.get("grade")) or _strong(signal.get("raw_grade"))


def _safe_signal(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "market_type": _safe_text(signal.get("market_type")),
        "selection": _safe_text(signal.get("selection")),
        "grade": _safe_grade(signal.get("grade")),
        "raw_grade": _safe_grade(signal.get("raw_grade")),
        "ev": _round4(signal.get("ev")),
        "edge": _round4(signal.get("edge")),
        "status": _safe_text(signal.get("status")),
        "reasons": _safe_text_list(signal.get("reasons")),
    }


def _safe_match(match: dict[str, Any]) -> dict[str, Any]:
    model = match.get("model") if isinstance(match.get("model"), dict) else {}
    market = match.get("market") if isinstance(match.get("market"), dict) else {}
    market_1x2 = market.get("1x2") if isinstance(market.get("1x2"), dict) else {}
    market_probs = (
        market_1x2.get("market_probs")
        if isinstance(market_1x2.get("market_probs"), dict)
        else {}
    )
    signals = [
        _safe_signal(signal)
        for signal in match.get("signals") or []
        if isinstance(signal, dict) and _signal_reportable(signal)
    ]
    return {
        "source_event_id": _safe_text(match.get("source_event_id")),
        "kickoff_at_utc": match.get("kickoff_at_utc"),
        "home_team": match.get("home_team"),
        "away_team": match.get("away_team"),
        "elo": _safe_elo(match),
        "model_1x2": _rounded_probs(model.get("combined_1x2"), ("home", "draw", "away")),
        "market_1x2": _rounded_probs(market_probs, ("home", "draw", "away")),
        "ou_2_5": _safe_ou(match),
        "signals": signals,
    }


def _all_signals(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for match in snapshot.get("matches") or []:
        if not isinstance(match, dict):
            continue
        signals.extend(signal for signal in match.get("signals") or [] if isinstance(signal, dict))
    return signals


def build_observation_report(
    snapshot: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    signals = _all_signals(snapshot)
    final_strong_grades = sum(1 for signal in signals if _strong(signal.get("grade")))
    raw_strong_candidates = sum(1 for signal in signals if _strong(signal.get("raw_grade")))
    data_quality = (
        snapshot.get("data_quality") if isinstance(snapshot.get("data_quality"), dict) else {}
    )
    warnings = _safe_text_list(data_quality.get("warnings"))
    matches = [
        _safe_match(match)
        for match in snapshot.get("matches") or []
        if isinstance(match, dict)
    ]
    status = "warn" if warnings or final_strong_grades or raw_strong_candidates else "ok"

    return {
        "schema_version": 1,
        "mode": "local_csl_observation",
        "generated_at": _utc_iso(generated_at),
        "research_notice": RESEARCH_NOTICE,
        "competition": _safe_competition(snapshot),
        "snapshot_at": snapshot.get("snapshot_at"),
        "status": status,
        "counts": {
            "matches": len(matches),
            "final_strong_grades": final_strong_grades,
            "raw_strong_candidates": raw_strong_candidates,
        },
        "warnings": warnings,
        "data_quality": _safe_data_quality(snapshot),
        "matches": matches,
    }


def default_report_path(root: Path, generated_at: str, output_format: str) -> Path:
    suffix = ".json" if output_format == "json" else ".md"
    stamp = _parse_utc(generated_at).strftime("%Y%m%dT%H%M%SZ")
    return root / "data" / "cache" / f"csl_observation_report_{stamp}{suffix}"


def _fmt4(value: Any) -> str:
    rounded = _round4(value)
    return "n/a" if rounded is None else f"{rounded:.4f}"


def _fmt_line(value: Any) -> str:
    rounded = _round4(value)
    if rounded is None:
        return "n/a"
    return str(int(rounded)) if rounded.is_integer() else str(rounded)


def _join_values(values: list[Any]) -> str:
    return ", ".join(str(value) for value in values) if values else "none"


def format_observation_markdown(report: dict[str, Any]) -> str:
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    competition = report.get("competition") if isinstance(report.get("competition"), dict) else {}
    data_quality = report.get("data_quality") if isinstance(report.get("data_quality"), dict) else {}
    club_rating = (
        data_quality.get("club_rating") if isinstance(data_quality.get("club_rating"), dict) else {}
    )

    lines = [
        "# CSL Observation Report",
        "",
        str(report.get("research_notice") or RESEARCH_NOTICE),
        "",
        f"generated_at: {report.get('generated_at')}",
        f"snapshot_at: {report.get('snapshot_at')}",
        f"status: {report.get('status')}",
        f"matches: {counts.get('matches', 0)}",
        f"raw strong candidates: {counts.get('raw_strong_candidates', 0)}",
        f"final strong grades: {counts.get('final_strong_grades', 0)}",
        "",
        "## Competition",
        f"- id: {competition.get('id')}",
        f"- name: {competition.get('name')}",
        f"- rating_policy: {competition.get('rating_policy')}",
        "",
        "## Data Quality",
        f"- warnings: {_join_values(_list(report.get('warnings')))}",
        f"- fixture_source: {data_quality.get('fixture_source')}",
        f"- club_alias_unmatched: {_join_values(_list(data_quality.get('club_alias_unmatched')))}",
        f"- invalid_odds_count: {data_quality.get('invalid_odds_count', 0)}",
        f"- club_rating.mode: {club_rating.get('mode')}",
        f"- club_rating.matches_replayed: {club_rating.get('matches_replayed')}",
        f"- club_rating.teams_rated: {club_rating.get('teams_rated')}",
        f"- club_rating.sample_too_small: {club_rating.get('sample_too_small')}",
        f"- club_rating.missing_teams: {_join_values(_list(club_rating.get('missing_teams')))}",
        f"- club_rating.errors: {_join_values(_list(club_rating.get('errors')))}",
        "",
        "## Matches",
    ]

    matches = report.get("matches") if isinstance(report.get("matches"), list) else []
    for match in matches:
        if not isinstance(match, dict):
            continue
        model_1x2 = match.get("model_1x2") if isinstance(match.get("model_1x2"), dict) else {}
        market_1x2 = match.get("market_1x2") if isinstance(match.get("market_1x2"), dict) else {}
        ou = match.get("ou_2_5") if isinstance(match.get("ou_2_5"), dict) else {}
        elo = match.get("elo") if isinstance(match.get("elo"), dict) else {}
        lines.extend(
            [
                "",
                f"### {match.get('home_team')} vs {match.get('away_team')}",
                f"- source_event_id: {match.get('source_event_id')}",
                f"- kickoff_at_utc: {match.get('kickoff_at_utc')}",
                f"- elo: home={elo.get('home')} away={elo.get('away')}",
                "- model_1x2: "
                f"home={_fmt4(model_1x2.get('home'))} "
                f"draw={_fmt4(model_1x2.get('draw'))} "
                f"away={_fmt4(model_1x2.get('away'))}",
                "- market_1x2: "
                f"home={_fmt4(market_1x2.get('home'))} "
                f"draw={_fmt4(market_1x2.get('draw'))} "
                f"away={_fmt4(market_1x2.get('away'))}",
                "- ou_2_5: "
                f"line={_fmt_line(ou.get('line'))} "
                f"model_over={_fmt4(ou.get('model_over'))} "
                f"market_over={_fmt4(ou.get('market_over'))}",
            ]
        )
        signals = match.get("signals") if isinstance(match.get("signals"), list) else []
        if signals:
            lines.append("- signals:")
            for signal in signals:
                if not isinstance(signal, dict):
                    continue
                reasons = _join_values(_list(signal.get("reasons")))
                lines.append(
                    "  - "
                    f"{signal.get('market_type')} {signal.get('selection')} "
                    f"grade={signal.get('grade')} raw={signal.get('raw_grade')} "
                    f"EV={_fmt4(signal.get('ev'))} Edge={_fmt4(signal.get('edge'))} "
                    f"status={signal.get('status')} reasons={reasons}"
                )
        else:
            lines.append("- signals: none")

    return "\n".join(lines).rstrip() + "\n"


def write_report(report: dict[str, Any], path: Path, output_format: str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    elif output_format == "markdown":
        content = format_observation_markdown(report)
    else:
        raise ValueError(f"unsupported output format: {output_format}")
    out.write_text(content, encoding="utf-8")
    return out


def _resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a sanitized local CSL observation report.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root)
    snapshot_path = _resolve_under_root(root, args.snapshot)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    report = build_observation_report(snapshot, generated_at=args.generated_at)
    out_path = (
        _resolve_under_root(root, args.out)
        if args.out is not None
        else default_report_path(root, report["generated_at"], args.format)
    )
    written = write_report(report, out_path, args.format)
    summary = {
        "research_notice": report["research_notice"],
        "status": report["status"],
        "matches": report["counts"]["matches"],
        "raw_strong_candidates": report["counts"]["raw_strong_candidates"],
        "final_strong_grades": report["counts"]["final_strong_grades"],
        "format": args.format,
        "path": str(written),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
