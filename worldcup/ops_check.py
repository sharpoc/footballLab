"""Read-only operational check for the World Cup analysis site."""
from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from worldcup.collectors.league_odds import parse_league_odds_events
from worldcup.query import summarize_finished_block
from worldcup.refresh_audit import DEFAULT_LAUNCH_AGENT, inspect_launch_agent, summarize_history

DEFAULT_PUBLIC_BASE_URL = "https://football.celab.xin"
DEFAULT_REMOTE_HOST = "strategy-lab-ecs"
DEFAULT_LOCAL_LOGS = (
    Path.home() / "Library" / "Logs" / "worldcup" / "scheduled-publish.out.log",
    Path.home() / "Library" / "Logs" / "worldcup" / "scheduled-publish.err.log",
)
DEFAULT_PRE_MATCH_LAUNCH_AGENT = (
    Path.home() / "Library" / "LaunchAgents" / "xin.celab.football.pre-match.plist"
)
DEFAULT_PRE_MATCH_LOGS = (
    Path.home() / "Library" / "Logs" / "worldcup" / "pre-match.out.log",
    Path.home() / "Library" / "Logs" / "worldcup" / "pre-match.err.log",
)
DEFAULT_LINEUP_AUDIT_PATH = Path("data/local/diagnostics/lineup_audit.json")
DEFAULT_CSL_COMPETITION_ID = "csl_2026"
DEFAULT_CSL_LIVE_ODDS_CACHE_PATH = Path("data/cache/theoddsapi_csl_2026_odds.json")
DEFAULT_CSL_LIVE_REFRESH_DIAGNOSTIC_PATH = Path(
    "data/local/diagnostics/csl_live_odds_refresh.json"
)
DEFAULT_CSL_LIVE_RUNNER_CHECK_PATH = Path(
    "data/local/diagnostics/csl_live_league_runner_check.json"
)
SAFE_QUOTA_FIELDS = ("remaining", "used", "last")
CSL_RUNNER_BLOCKING_WARNINGS = {
    "club_rating_missing",
    "club_rating_invalid",
    "club_rating_sample_too_small",
}
DANGEROUS_SAFE_STRING_TERMS = ("secret", "hmac", "credential", "apikey")
SAFE_STRING_MAX_LENGTH = 120
ALLOWED_QUOTA_PROVIDERS = {
    "theoddsapi",
    "theoddsapi_primary",
    "theoddsapi_secondary",
}
ALLOWED_CSL_SPORT_KEYS = {
    "soccer_china_superleague",
    "soccer_china_super_league",
}
ALLOWED_REFRESH_STATUSES = {"ok", "missing", "fetched", "dry_run", "skipped", "error", "warn"}
ALLOWED_RUNNER_STATUSES = {"ok", "missing", "skipped", "error", "warn"}
ALLOWED_FIXTURE_SOURCES = {"odds_event_only", "explicit_fixture_source", "openfootball"}
ALLOWED_RATING_POLICIES = {"club_rating_pending", "national_team_elo"}
ALLOWED_CLUB_RATING_MODES = {"sample_replay", "sample_too_small", "missing", "invalid", "fallback"}
ALLOWED_DIAGNOSTIC_CODES = {
    "club_rating_pending",
    "club_rating_missing",
    "club_rating_invalid",
    "club_rating_sample_too_small",
    "odds_event_only",
    "missing",
    "runner_failed",
    "no_valid_rows",
}
ALLOWED_STRONG_GRADES = {"S", "A", "B", "C", "D"}
ALLOWED_RUNNER_COUNT_KEYS = {"fixtures", "odds_events", "match_inputs", "matches"}
ALLOWED_REFRESH_CACHE_PATHS = {
    "data/cache/theoddsapi_csl_2026_odds.json",
    "data/local/diagnostics/csl_live_odds_refresh.json",
    "data/local/diagnostics/csl_live_league_runner_check.json",
}
SAFE_ISOISH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?$")
SAFE_TEAM_LABEL_RE = re.compile(r"^[A-Za-z .&'()/-]{1,80}$")
DISCLAIMER = "仅用于研究分析，不构成投注建议"
FORBIDDEN_PUBLIC_TERMS = [
    "stake",
    "bet amount",
    "bankroll",
    "payout",
    "wager",
    "unit",
    "下注金额",
    "投注金额",
    "本金",
    "重注",
    "追损",
    "串关",
    "喊单",
]
SENSITIVE_RE = re.compile(
    r"api[_-]?key|the_odds_api_key|ingest_hmac_secret|hmac secret|database_url|"
    r"x-worldcup-signature|authorization|cookie|token|password|private[-_ ]?key|"
    r"request body|body\":|signature",
    re.I,
)
ERROR_RE = re.compile(r"traceback|\berror\b|exception|failed|panic|critical", re.I)
SAFE_SENSITIVE_VALUES = {
    "",
    "null",
    "none",
    "true",
    "false",
    "configured",
    "present",
    "set",
    "unset",
    "missing",
    "redacted",
    "<redacted>",
    "masked",
}
REMOTE_DROP_KEYS = {
    "payload",
    "payload_json",
    "snapshot",
    "snapshot_json",
    "body",
    "body_json",
    "secret",
    "signature",
}

FetchResult = dict[str, Any]
Fetcher = Callable[[str, int], FetchResult]
RemoteRunner = Callable[[str, int], dict[str, Any]]


def _field_value_after_match(text: str, end: int) -> tuple[bool, str | None]:
    suffix = text[end : end + 120]
    match = re.match(
        r"""(?P<quote>["']?)\s*(?:(?P<sep>[:=])\s*(?P<value>"[^"]*"|'[^']*'|[^,\s}\]]+))?""",
        suffix,
    )
    if not match:
        return False, None
    sep = match.group("sep")
    if sep is None:
        return True, None
    raw_value = match.group("value")
    return True, raw_value


def _is_safe_sensitive_field_name(text: str, match: re.Match[str]) -> bool:
    term = match.group(0).lower()
    if "api" not in term or "key" not in term:
        return False
    has_field_shape, raw_value = _field_value_after_match(text, match.end())
    if not has_field_shape:
        return False
    if raw_value is None:
        return True
    value = raw_value.strip().strip("\"'")
    normalized = value.lower()
    return normalized in SAFE_SENSITIVE_VALUES or set(value) <= {"*", "x", "X", "."}


def scan_text(text: str) -> dict[str, int]:
    sensitive_hits = 0
    sensitive_field_name_hits = 0
    for match in SENSITIVE_RE.finditer(text):
        if _is_safe_sensitive_field_name(text, match):
            sensitive_field_name_hits += 1
            continue
        sensitive_hits += 1
    return {
        "bytes_checked": len(text.encode("utf-8", errors="replace")),
        "sensitive_hits": sensitive_hits,
        "sensitive_field_name_hits": sensitive_field_name_hits,
        "error_hits": len(ERROR_RE.findall(text)),
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_json_any(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_safe_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_safe_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _safe_quota_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in SAFE_QUOTA_FIELDS
        if key in payload and _is_safe_number(payload[key])
    }


def _safe_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) > SAFE_STRING_MAX_LENGTH:
        return None
    lowered = value.lower()
    if "http://" in lowered or "https://" in lowered:
        return None
    if ".env" in lowered:
        return None
    if any(term in lowered for term in DANGEROUS_SAFE_STRING_TERMS):
        return None
    if "?" in value or "=" in value:
        return None
    if SENSITIVE_RE.search(value):
        return None
    return value


def _safe_allowed_string(value: Any, allowed: set[str]) -> str | None:
    safe = _safe_string(value)
    return safe if safe in allowed else None


def _safe_allowed_string_list(value: Any, allowed: set[str]) -> list[str]:
    safe: list[str] = []
    if not isinstance(value, list):
        return safe
    for item in value:
        safe_item = _safe_allowed_string(item, allowed)
        if safe_item is not None:
            safe.append(safe_item)
    return safe


def _safe_isoish(value: Any) -> str | None:
    safe = _safe_string(value)
    if safe is None:
        return None
    return safe if SAFE_ISOISH_RE.match(safe) else None


def _safe_team_label(value: Any) -> str | None:
    safe = _safe_string(value)
    if safe is None or SAFE_TEAM_LABEL_RE.match(safe) is None:
        return None
    if " " not in safe:
        return None
    return safe


def _safe_team_label_list(value: Any) -> list[str]:
    safe: list[str] = []
    if not isinstance(value, list):
        return safe
    for item in value:
        safe_item = _safe_team_label(item)
        if safe_item is not None:
            safe.append(safe_item)
    return safe


def _safe_relative_path(value: Any) -> str | None:
    safe = _safe_string(value)
    if safe is None:
        return None
    path = PurePosixPath(safe)
    if path.is_absolute() or ".." in path.parts:
        return None
    if safe in ALLOWED_REFRESH_CACHE_PATHS:
        return safe
    return None


def _safe_counts(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, Any] = {}
    for key, item in value.items():
        safe_key = _safe_allowed_string(key, ALLOWED_RUNNER_COUNT_KEYS)
        if safe_key is not None and _is_safe_number(item):
            counts[safe_key] = item
    return counts


def _raw_list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _safe_quota_providers(root: Path) -> dict[str, Any]:
    path = root / "data/cache/quota.json"
    quota = _read_json(path)
    if quota is None:
        return {"status": "missing", "path": str(path), "providers": {}}
    providers = quota.get("providers") if isinstance(quota.get("providers"), dict) else {}
    safe_providers: dict[str, Any] = {}
    for provider, value in providers.items():
        safe_provider = _safe_allowed_string(provider, ALLOWED_QUOTA_PROVIDERS)
        if safe_provider is not None and isinstance(value, dict):
            safe_providers[safe_provider] = _safe_quota_fields(value)
    return {"status": "ok", "path": str(path), "providers": safe_providers}


def _safe_refresh_diagnostic(root: Path) -> dict[str, Any]:
    path = root / DEFAULT_CSL_LIVE_REFRESH_DIAGNOSTIC_PATH
    payload = _read_json(path)
    if payload is None:
        return {"status": "missing", "path": str(path)}
    result: dict[str, Any] = {"path": str(path)}
    status = _safe_allowed_string(payload.get("status"), ALLOWED_REFRESH_STATUSES)
    if status is not None:
        result["status"] = status
    observed_at = _safe_isoish(payload.get("observed_at"))
    if observed_at is not None:
        result["observed_at"] = observed_at
    provider = _safe_allowed_string(payload.get("theoddsapi_provider"), ALLOWED_QUOTA_PROVIDERS)
    if provider is not None:
        result["theoddsapi_provider"] = provider
    for key in ("events", "quota_remaining", "quota_last"):
        if _is_safe_number(payload.get(key)):
            result[key] = payload[key]
    if _is_safe_bool(payload.get("has_synthetic_marker")):
        result["has_synthetic_marker"] = payload["has_synthetic_marker"]
    cache_path = _safe_relative_path(payload.get("cache_path"))
    if cache_path is not None:
        result["cache_path"] = cache_path
    if "status" not in result:
        result["status"] = "ok"
    return result


def _safe_club_rating(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    mode = _safe_allowed_string(payload.get("mode"), ALLOWED_CLUB_RATING_MODES)
    if mode is not None:
        result["mode"] = mode
    for key in ("matches_replayed", "teams_rated", "skipped_rows"):
        if _is_safe_number(payload.get(key)):
            result[key] = payload[key]
    if _is_safe_bool(payload.get("sample_too_small")):
        result["sample_too_small"] = payload["sample_too_small"]
    result["errors_count"] = _raw_list_count(payload.get("errors"))
    result["errors"] = _safe_allowed_string_list(payload.get("errors"), ALLOWED_DIAGNOSTIC_CODES)
    return result


def _safe_runner_check(root: Path) -> dict[str, Any]:
    path = root / DEFAULT_CSL_LIVE_RUNNER_CHECK_PATH
    payload = _read_json(path)
    if payload is None:
        return {"status": "missing", "path": str(path)}
    result: dict[str, Any] = {"path": str(path)}
    status = _safe_allowed_string(payload.get("status"), ALLOWED_RUNNER_STATUSES)
    if status is not None:
        result["status"] = status
    fixture_source = _safe_allowed_string(payload.get("fixture_source"), ALLOWED_FIXTURE_SOURCES)
    if fixture_source is not None:
        result["fixture_source"] = fixture_source
    rating_policy = _safe_allowed_string(payload.get("rating_policy"), ALLOWED_RATING_POLICIES)
    if rating_policy is not None:
        result["rating_policy"] = rating_policy
    counts = _safe_counts(payload.get("counts"))
    if counts:
        result["counts"] = counts
    result["warnings"] = _safe_allowed_string_list(payload.get("warnings"), ALLOWED_DIAGNOSTIC_CODES)
    result["errors_count"] = _raw_list_count(payload.get("errors"))
    result["errors"] = _safe_allowed_string_list(payload.get("errors"), ALLOWED_DIAGNOSTIC_CODES)
    result["club_alias_unmatched_count"] = _raw_list_count(payload.get("club_alias_unmatched"))
    result["club_alias_unmatched"] = _safe_team_label_list(payload.get("club_alias_unmatched"))
    if _is_safe_number(payload.get("invalid_odds_count")):
        result["invalid_odds_count"] = payload["invalid_odds_count"]
    if _is_safe_number(payload.get("signals")):
        result["signals"] = payload["signals"]
    result["strong_grades"] = _safe_allowed_string_list(payload.get("strong_grades"), ALLOWED_STRONG_GRADES)
    club_rating = payload.get("club_rating")
    if isinstance(club_rating, dict):
        result["club_rating"] = _safe_club_rating(club_rating)
    if "status" not in result:
        result["status"] = "ok"
    return result


def _csl_live_odds_summary(
    root: Path,
    competition_id: str = DEFAULT_CSL_COMPETITION_ID,
) -> dict[str, Any]:
    cache_path = root / DEFAULT_CSL_LIVE_ODDS_CACHE_PATH
    if not cache_path.exists():
        return {
            "status": "missing",
            "competition_id": competition_id,
            "path": str(cache_path),
            "message": "live_odds_cache_missing",
            "quota": _safe_quota_providers(root),
            "refresh_diagnostic": _safe_refresh_diagnostic(root),
            "runner_check": _safe_runner_check(root),
        }

    payload = _read_json_any(cache_path)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        return {
            "status": "error",
            "competition_id": competition_id,
            "path": str(cache_path),
            "message": "invalid_odds_cache_shape",
            "quota": _safe_quota_providers(root),
            "refresh_diagnostic": _safe_refresh_diagnostic(root),
            "runner_check": _safe_runner_check(root),
        }

    try:
        parse_result = parse_league_odds_events(payload, competition_id)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return {
            "status": "error",
            "competition_id": competition_id,
            "path": str(cache_path),
            "message": "invalid_odds_cache_payload",
            "error_type": type(exc).__name__,
            "quota": _safe_quota_providers(root),
            "refresh_diagnostic": _safe_refresh_diagnostic(root),
            "runner_check": _safe_runner_check(root),
        }

    raw_unmatched = parse_result.unmatched_clubs
    safe_sport_keys = sorted(
        set(
            _safe_allowed_string(item.get("sport_key"), ALLOWED_CSL_SPORT_KEYS)
            for item in payload
        )
        - {None}
    )
    safe_unmatched = _safe_team_label_list(raw_unmatched)

    return {
        "status": "ok",
        "competition_id": competition_id,
        "path": str(cache_path),
        "events": len(payload),
        "fixtures": len(parse_result.fixtures),
        "odds_events": len(parse_result.odds_events),
        "sport_keys": safe_sport_keys,
        "has_synthetic_marker": any(item.get("_synthetic_smoke") is True for item in payload),
        "club_alias_unmatched": safe_unmatched,
        "club_alias_unmatched_count": len(raw_unmatched),
        "invalid_odds_count": sum(len(event.invalid_odds) for event in parse_result.odds_events),
        "quota": _safe_quota_providers(root),
        "refresh_diagnostic": _safe_refresh_diagnostic(root),
        "runner_check": _safe_runner_check(root),
    }


def _snapshot_summary(path: Path) -> dict[str, Any]:
    snapshot = _read_json(path)
    if snapshot is None:
        return {"status": "error", "path": str(path), "message": "missing_or_unreadable"}

    data_quality = snapshot.get("data_quality") or {}
    matches = snapshot.get("matches")
    counts = snapshot.get("counts") or {}
    match_count = counts.get("matches")
    if match_count is None and isinstance(matches, list):
        match_count = len(matches)

    return {
        "status": "ok",
        "path": str(path),
        "run_id": (snapshot.get("run") or {}).get("run_id"),
        "snapshot_at": snapshot.get("snapshot_at"),
        "matches": match_count,
        "source_errors_count": len(data_quality.get("source_errors") or []),
        "stale_sources": list(data_quality.get("stale_sources") or []),
    }


def _zero_tally() -> dict[str, int]:
    return {"hit": 0, "miss": 0, "push": 0}


def _normalize_tally(tally: dict[str, Any], grades: set[str]) -> dict[str, dict[str, int]]:
    normalized: dict[str, dict[str, int]] = {}
    for grade in sorted(grades):
        entry = tally.get(grade) if isinstance(tally.get(grade), dict) else {}
        normalized[grade] = {
            "hit": _as_int(entry.get("hit")),
            "miss": _as_int(entry.get("miss")),
            "push": _as_int(entry.get("push")),
        }
    return normalized


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _recompute_finished_tally(records: list[Any]) -> dict[str, dict[str, int]]:
    tally: dict[str, dict[str, int]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        for signal in record.get("closing_signals") or []:
            if not isinstance(signal, dict):
                continue
            grade = str(signal.get("grade") or "")
            if grade not in {"S", "A"}:
                continue
            status = str((signal.get("prediction") or {}).get("status") or "")
            if status not in {"hit", "miss", "push"}:
                continue
            tally.setdefault(grade, _zero_tally())[status] += 1
    return tally


def _csv_row_count(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        return {"status": "unreadable", "path": str(path)}
    return {"status": "ok", "path": str(path), "count": len(rows)}


def _finished_consistency(root: Path) -> dict[str, Any]:
    snapshot = _read_json(root / "data/cache/analysis_snapshot.json")
    if snapshot is None:
        return {"status": "missing_snapshot"}

    finished = snapshot.get("finished") or {}
    records = finished.get("matches") if isinstance(finished.get("matches"), list) else []
    summary = summarize_finished_block(snapshot)
    if not finished:
        return {"status": "missing", "summary": summary}

    declared_raw = finished.get("tally") if isinstance(finished.get("tally"), dict) else {}
    recomputed_raw = _recompute_finished_tally(records)
    grades = set(declared_raw) | set(recomputed_raw) | {"S", "A"}
    declared = _normalize_tally(declared_raw, grades)
    recomputed = _normalize_tally(recomputed_raw, grades)

    results = _csv_row_count(root / "data/local/results/wc2026_results.csv")
    if results.get("status") == "ok":
        expected = (summary.get("coverage") or {}).get("finished_result_count")
        results["finished_result_count"] = expected
        results["matches_finished_result_count"] = results.get("count") == expected

    return {
        "status": "ok",
        "summary": summary,
        "declared_tally": declared,
        "recomputed_tally": recomputed,
        "tally_matches": declared == recomputed,
        "results": results,
    }


def _quota_summary(path: Path) -> dict[str, Any]:
    quota = _read_json(path)
    if quota is None:
        return {"status": "warn", "path": str(path), "message": "missing_or_unreadable"}
    providers = quota.get("providers") if isinstance(quota.get("providers"), dict) else {}
    safe_providers: dict[str, Any] = {}
    for provider, value in providers.items():
        safe_provider = _safe_allowed_string(provider, ALLOWED_QUOTA_PROVIDERS)
        if safe_provider is not None and isinstance(value, dict):
            safe_providers[safe_provider] = _safe_quota_fields(value)
    return {"status": "ok", "path": str(path), "providers": safe_providers}


def _scan_log_file(path: Path, max_bytes: int = 200_000) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        raw = path.read_bytes()
    except OSError:
        return {"status": "unreadable", "path": str(path)}
    text = raw[-max_bytes:].decode("utf-8", errors="replace")
    return {"status": "ok", "path": str(path), **scan_text(text)}


def _launch_agent_wiring(launch_agent: dict[str, Any]) -> dict[str, bool]:
    args = launch_agent.get("program_arguments")
    values = set(str(arg) for arg in args) if isinstance(args, list) else set()
    return {
        "has_live_lineups": "--live-lineups" in values,
        "has_write_lineups": "--write-lineups" in values,
        "has_notify_missing": "--notify-missing" in values,
        "has_notify_audit": "--notify-audit" in values,
        "has_refresh_guard": "--refresh-guard" in values,
        "has_refresh_after_lineups": "--refresh-after-lineups" in values,
        "has_live_refresh": "--live-refresh" in values,
    }


def _lineup_audit_summary(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    if payload is None:
        return {"status": "missing", "path": str(path)}
    return {
        "status": "ok",
        "path": str(path),
        "generated_at": payload.get("generated_at"),
        "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
        "notifications": payload.get("notifications")
        if isinstance(payload.get("notifications"), dict)
        else {},
    }


def _pre_match_checks(
    root: Path,
    launch_agent_path: str | Path | None,
    log_paths: list[str | Path],
    lineup_audit_path: str | Path,
) -> dict[str, Any]:
    if launch_agent_path is None:
        return {"status": "skipped"}
    launch_agent = inspect_launch_agent(launch_agent_path)
    return {
        "status": "ok",
        "launch_agent": launch_agent,
        "wiring": _launch_agent_wiring(launch_agent),
        "logs": [_scan_log_file(Path(path).expanduser()) for path in log_paths],
        "lineup_audit": _lineup_audit_summary(root / Path(lineup_audit_path)),
    }


def _local_checks(
    root: Path,
    launch_agent_path: str | Path,
    local_log_paths: list[str | Path],
    pre_match_launch_agent_path: str | Path | None,
    pre_match_log_paths: list[str | Path],
    lineup_audit_path: str | Path,
) -> dict[str, Any]:
    return {
        "snapshot": _snapshot_summary(root / "data/cache/analysis_snapshot.json"),
        "quota": _quota_summary(root / "data/cache/quota.json"),
        "csl_live_odds": _csl_live_odds_summary(root),
        "finished": _finished_consistency(root),
        "history": summarize_history(root / "data/local/history", limit=3),
        "launch_agent": inspect_launch_agent(launch_agent_path),
        "logs": [_scan_log_file(Path(path).expanduser()) for path in local_log_paths],
        "pre_match": _pre_match_checks(
            root,
            launch_agent_path=pre_match_launch_agent_path,
            log_paths=pre_match_log_paths,
            lineup_audit_path=lineup_audit_path,
        ),
    }


def _default_fetcher(url: str, timeout: int) -> FetchResult:
    request = Request(url, headers={"User-Agent": "worldcup-ops-check/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return {
                "status": response.status,
                "body": body,
                "headers": dict(response.headers.items()),
            }
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "status": exc.code,
            "body": body,
            "headers": dict(exc.headers.items()),
        }
    except URLError as exc:
        return {"status": None, "body": "", "headers": {}, "error": exc.reason}


def _header(headers: dict[str, Any], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return None


def _forbidden_hits(text: str) -> list[str]:
    lower = text.lower()
    return [term for term in FORBIDDEN_PUBLIC_TERMS if term.lower() in lower]


def _last_update(text: str) -> str | None:
    match = re.search(r"最后更新\s*(?:<br\s*/?>)?\s*([^<]+)", text, flags=re.I)
    return match.group(1).strip() if match else None


def _public_json_check(base_url: str, path: str, fetcher: Fetcher, timeout: int) -> dict[str, Any]:
    response = fetcher(base_url.rstrip("/") + path, timeout)
    body = str(response.get("body") or "")
    result: dict[str, Any] = {
        "http_status": response.get("status"),
        "bytes": len(body.encode("utf-8", errors="replace")),
        "content_type": _header(response.get("headers") or {}, "content-type"),
    }
    try:
        parsed = json.loads(body) if body else None
    except json.JSONDecodeError:
        parsed = None
    if path == "/api/matches" and isinstance(parsed, dict):
        matches = parsed.get("matches")
        result["count"] = len(matches) if isinstance(matches, list) else None
        result["forbidden_hits"] = _forbidden_hits(body)
    if path == "/api/finished" and isinstance(parsed, dict):
        finished = parsed.get("finished") if isinstance(parsed.get("finished"), dict) else {}
        summary = finished.get("summary") if isinstance(finished.get("summary"), dict) else {}
        result["summary"] = summary
        result["match_count"] = summary.get("match_count")
        result["sample_too_small"] = (summary.get("sample") or {}).get("sample_too_small")
        result["forbidden_hits"] = _forbidden_hits(body)
    return result


def _public_html_check(base_url: str, path: str, fetcher: Fetcher, timeout: int) -> dict[str, Any]:
    response = fetcher(base_url.rstrip("/") + path, timeout)
    body = str(response.get("body") or "")
    return {
        "http_status": response.get("status"),
        "bytes": len(body.encode("utf-8", errors="replace")),
        "content_type": _header(response.get("headers") or {}, "content-type"),
        "has_disclaimer": DISCLAIMER in body,
        "forbidden_hits": _forbidden_hits(body),
        "last_update": _last_update(body),
    }


def _public_checks(base_url: str, fetcher: Fetcher, timeout: int) -> dict[str, Any]:
    return {
        "healthz": _public_json_check(base_url, "/healthz", fetcher, timeout),
        "matches": _public_json_check(base_url, "/api/matches", fetcher, timeout),
        "finished": _public_json_check(base_url, "/api/finished", fetcher, timeout),
        "snapshot_latest": _public_json_check(base_url, "/api/snapshot/latest", fetcher, timeout),
        "home": _public_html_check(base_url, "/", fetcher, timeout),
        "preview": _public_html_check(base_url, "/preview", fetcher, timeout),
    }


REMOTE_SCRIPT = r"""
from __future__ import annotations
import json, re, sqlite3, subprocess, urllib.error, urllib.request
from pathlib import Path

def run(cmd):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {"active": p.returncode == 0 and p.stdout.strip() == "active"}

def fetch(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8788" + path, timeout=10) as resp:
            body = resp.read()
            return {"http_status": resp.status, "bytes": len(body), "content_type": resp.headers.get("content-type")}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return {"http_status": exc.code, "bytes": len(body), "content_type": exc.headers.get("content-type")}
    except Exception:
        return {"http_status": None, "bytes": 0}

def journal_counts():
    text = subprocess.run(
        ["journalctl", "-u", "worldcup.service", "--since", "24 hours ago", "--no-pager"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    return {
        "line_count": len(text.splitlines()),
        "sensitive_hits": len(re.findall(r"api[_-]?key|the_odds_api_key|ingest_hmac_secret|hmac secret|database_url|x-worldcup-signature|authorization|cookie|token|password|private[-_ ]?key|request body|body\":|signature", text, flags=re.I)),
        "error_hits": len(re.findall(r"traceback|\berror\b|exception|failed|panic|critical", text, flags=re.I)),
        "ingest_hits": len(re.findall(r"/api/ingest/snapshot|stored|duplicate|conflict", text, flags=re.I)),
    }

def nginx_counts():
    result = {}
    for path in [Path("/var/log/nginx/error.log"), Path("/var/log/nginx/access.log")]:
        if not path.exists():
            result[str(path)] = {"exists": False}
            continue
        text = path.read_text(errors="replace")[-200000:]
        result[str(path)] = {
            "exists": True,
            "sensitive_project_hits": len(re.findall(r"THE_ODDS_API_KEY|INGEST_HMAC_SECRET|DATABASE_URL|X-Worldcup-Signature|X-Worldcup-Body-SHA256", text, flags=re.I)),
            "errors_5xx_or_upstream": len(re.findall(r"\b(500|502|503|504)\b|upstream.*error|connect\(\) failed|crit|alert|emerg", text, flags=re.I)),
        }
    return result

summary = {
    "services": {
        "worldcup": run(["systemctl", "is-active", "worldcup.service"]),
        "nginx": run(["systemctl", "is-active", "nginx"]),
    },
    "release": {
        "current": str(Path("/opt/worldcup/current").resolve()) if Path("/opt/worldcup/current").exists() else None,
    },
    "local_http": {
        "/healthz": fetch("/healthz"),
        "/api/matches": fetch("/api/matches"),
        "/api/finished": fetch("/api/finished"),
        "/api/snapshot/latest": fetch("/api/snapshot/latest"),
    },
    "logs": {
        "journal": journal_counts(),
        "nginx": nginx_counts(),
    },
}
try:
    con = sqlite3.connect("/var/lib/worldcup/worldcup.db")
    con.row_factory = sqlite3.Row
    latest = con.execute("select idempotency_key, run_id, snapshot_id, snapshot_at, stored_at from snapshots order by stored_at desc limit 1").fetchone()
    summary["sqlite"] = {
        "snapshot_count": con.execute("select count(*) as c from snapshots").fetchone()["c"],
        "latest_meta": dict(latest) if latest is not None else None,
    }
    con.close()
except Exception as exc:
    summary["sqlite"] = {"error": type(exc).__name__}
print(json.dumps(summary, ensure_ascii=False))
"""


def _default_remote_runner(host: str, timeout: int) -> dict[str, Any]:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout}",
        host,
        "python3 -c " + shlex.quote(REMOTE_SCRIPT),
    ]
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _sanitize_remote(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_remote(item)
            for key, item in value.items()
            if key.lower() not in REMOTE_DROP_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_remote(item) for item in value]
    return value


def _remote_checks(host: str | None, runner: RemoteRunner, timeout: int) -> dict[str, Any]:
    if not host:
        return {"status": "skipped"}
    result = runner(host, timeout)
    if result.get("returncode") != 0:
        return {
            "status": "error",
            "host": host,
            "message": "remote_command_failed",
            "returncode": result.get("returncode"),
        }
    try:
        parsed = json.loads(str(result.get("stdout") or "{}"))
    except json.JSONDecodeError:
        return {"status": "error", "host": host, "message": "invalid_remote_json"}
    sanitized = _sanitize_remote(parsed)
    if isinstance(sanitized, dict):
        sanitized["status"] = "ok"
        sanitized["host"] = host
        return sanitized
    return {"status": "error", "host": host, "message": "invalid_remote_shape"}


def _list_values(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _runner_has_blocking_warning(runner: dict[str, Any]) -> bool:
    warnings = {str(item) for item in _list_values(runner.get("warnings"))}
    return bool(warnings & CSL_RUNNER_BLOCKING_WARNINGS)


def _csl_runner_has_error(runner: dict[str, Any]) -> bool:
    if runner.get("status") == "missing":
        return False
    if runner.get("status") == "error":
        return True
    club_rating = runner.get("club_rating") if isinstance(runner.get("club_rating"), dict) else {}
    return (
        _runner_has_blocking_warning(runner)
        or bool(_list_values(runner.get("club_alias_unmatched")))
        or _as_int(runner.get("club_alias_unmatched_count")) > 0
        or _as_int(runner.get("invalid_odds_count")) > 0
        or _as_int(runner.get("errors_count")) > 0
        or _as_int(club_rating.get("errors_count")) > 0
        or bool(_list_values(runner.get("strong_grades")))
    )


def _count_issues(result: dict[str, Any]) -> dict[str, int]:
    errors = 0
    warnings = 0

    local = result.get("local") or {}
    csl_live_odds = local.get("csl_live_odds") if isinstance(local.get("csl_live_odds"), dict) else {}
    csl_status = csl_live_odds.get("status")
    warnings += int(csl_status == "missing")
    errors += int(csl_status == "error")
    if csl_status == "ok":
        errors += int(csl_live_odds.get("has_synthetic_marker") is True)
        errors += int(
            bool(_list_values(csl_live_odds.get("club_alias_unmatched")))
            or _as_int(csl_live_odds.get("club_alias_unmatched_count")) > 0
        )
        errors += int(_as_int(csl_live_odds.get("invalid_odds_count")) > 0)
        runner = (
            csl_live_odds.get("runner_check")
            if isinstance(csl_live_odds.get("runner_check"), dict)
            else {}
        )
        warnings += int(runner.get("status") == "missing")
        errors += int(_csl_runner_has_error(runner))
    if (local.get("snapshot") or {}).get("status") != "ok":
        errors += 1
    for log in local.get("logs") or []:
        errors += int(log.get("sensitive_hits", 0) > 0)
        warnings += int(log.get("error_hits", 0) > 0)
    pre_match = local.get("pre_match") or {}
    pre_match_wiring = pre_match.get("wiring") if isinstance(pre_match.get("wiring"), dict) else {}
    errors += int(
        pre_match_wiring.get("has_live_refresh") is True
        and pre_match_wiring.get("has_refresh_guard") is not True
    )
    for log in pre_match.get("logs") or []:
        errors += int(log.get("sensitive_hits", 0) > 0)
        warnings += int(log.get("error_hits", 0) > 0)
    finished = local.get("finished") or {}
    errors += int(finished.get("tally_matches") is False)
    results = finished.get("results") if isinstance(finished.get("results"), dict) else {}
    errors += int(results.get("matches_finished_result_count") is False)

    public = result.get("public") or {}
    if public.get("status") != "skipped":
        errors += int((public.get("healthz") or {}).get("http_status") != 200)
        errors += int((public.get("matches") or {}).get("http_status") != 200)
        errors += int(bool((public.get("matches") or {}).get("forbidden_hits")))
        errors += int((public.get("finished") or {}).get("http_status") != 200)
        errors += int(bool((public.get("finished") or {}).get("forbidden_hits")))
        for key in ("home", "preview"):
            item = public.get(key) or {}
            errors += int(item.get("http_status") != 200)
            errors += int(item.get("has_disclaimer") is not True)
            errors += int(bool(item.get("forbidden_hits")))

    remote = result.get("remote") or {}
    errors += int(remote.get("status") == "error")
    local_http = remote.get("local_http") or {}
    for path in ("/healthz", "/api/matches", "/api/finished"):
        if path in local_http:
            errors += int((local_http.get(path) or {}).get("http_status") != 200)
    logs = remote.get("logs") or {}
    journal = logs.get("journal") or {}
    errors += int(journal.get("sensitive_hits", 0) > 0)
    warnings += int(journal.get("error_hits", 0) > 0)
    nginx = logs.get("nginx") or {}
    nginx_items = (
        list(nginx.values())
        if any(isinstance(item, dict) for item in nginx.values())
        else [nginx]
    )
    for item in nginx_items:
        if not isinstance(item, dict):
            continue
        errors += int(item.get("sensitive_project_hits", 0) > 0)
        warnings += int(item.get("errors_5xx_or_upstream", 0) > 0)

    return {"errors": errors, "warnings": warnings}


def _report_status_from_counts(summary: dict[str, Any]) -> str:
    if _as_int(summary.get("errors")) > 0:
        return "error"
    if _as_int(summary.get("warnings")) > 0:
        return "warn"
    return "ok"


def _first_safe_number(*values: Any) -> int | float | None:
    for value in values:
        if _is_safe_number(value):
            return value
    return None


def _report_csl_issue_codes(csl: dict[str, Any], runner: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    status = csl.get("status")
    if status == "missing":
        issues.append("live_odds_cache_missing")
        return issues
    if status == "error":
        message = _safe_string(csl.get("message")) or "live_odds_cache_error"
        issues.append(message)
        return issues
    if status != "ok":
        issues.append("live_odds_status_unknown")
        return issues
    if csl.get("has_synthetic_marker") is True:
        issues.append("synthetic_live_cache")
    if _as_int(csl.get("club_alias_unmatched_count")) > 0:
        issues.append("club_alias_unmatched")
    if _as_int(csl.get("invalid_odds_count")) > 0:
        issues.append("invalid_decimal_odds")
    if runner.get("status") == "missing" and not issues:
        issues.append("runner_diagnostic_missing")
    elif _csl_runner_has_error(runner):
        if runner.get("status") == "error":
            issues.append("runner_status_error")
        if _runner_has_blocking_warning(runner):
            issues.append("runner_blocking_warning")
        if _as_int(runner.get("club_alias_unmatched_count")) > 0:
            issues.append("runner_club_alias_unmatched")
        if _as_int(runner.get("invalid_odds_count")) > 0:
            issues.append("runner_invalid_odds")
        if _as_int(runner.get("errors_count")) > 0:
            issues.append("runner_errors")
        club_rating = runner.get("club_rating") if isinstance(runner.get("club_rating"), dict) else {}
        if _as_int(club_rating.get("errors_count")) > 0:
            issues.append("runner_club_rating_errors")
        if _list_values(runner.get("strong_grades")):
            issues.append("runner_strong_grades_present")
    return issues


def _report_csl_live_odds(csl: dict[str, Any]) -> dict[str, Any]:
    runner = csl.get("runner_check") if isinstance(csl.get("runner_check"), dict) else {}
    refresh = (
        csl.get("refresh_diagnostic")
        if isinstance(csl.get("refresh_diagnostic"), dict)
        else {}
    )
    quota = csl.get("quota") if isinstance(csl.get("quota"), dict) else {}
    providers = quota.get("providers") if isinstance(quota.get("providers"), dict) else {}
    provider = refresh.get("theoddsapi_provider")
    provider_quota = providers.get(provider) if isinstance(providers.get(provider), dict) else {}
    club_rating = runner.get("club_rating") if isinstance(runner.get("club_rating"), dict) else {}
    counts = runner.get("counts") if isinstance(runner.get("counts"), dict) else {}
    issues = _report_csl_issue_codes(csl, runner)
    status = "ok"
    if csl.get("status") == "missing" or runner.get("status") == "missing":
        status = "warn"
    if any(issue not in {"live_odds_cache_missing", "runner_diagnostic_missing"} for issue in issues):
        status = "error"
    report: dict[str, Any] = {
        "status": status,
        "competition_id": csl.get("competition_id"),
        "events": csl.get("events"),
        "fixtures": csl.get("fixtures"),
        "odds_events": csl.get("odds_events"),
        "sport_keys": csl.get("sport_keys") or [],
        "observed_at": refresh.get("observed_at"),
        "provider": provider,
        "quota_remaining": _first_safe_number(
            refresh.get("quota_remaining"),
            provider_quota.get("remaining"),
        ),
        "quota_last": _first_safe_number(refresh.get("quota_last"), provider_quota.get("last")),
        "has_synthetic_marker": csl.get("has_synthetic_marker"),
        "club_alias_unmatched_count": _as_int(csl.get("club_alias_unmatched_count")),
        "invalid_odds_count": _as_int(csl.get("invalid_odds_count")),
        "runner_status": runner.get("status"),
        "runner_matches": counts.get("matches"),
        "runner_warnings": runner.get("warnings") or [],
        "runner_errors_count": _as_int(runner.get("errors_count")),
        "runner_strong_grades": runner.get("strong_grades") or [],
        "rating_policy": runner.get("rating_policy"),
        "club_rating_mode": club_rating.get("mode"),
        "club_rating_matches_replayed": club_rating.get("matches_replayed"),
        "club_rating_teams_rated": club_rating.get("teams_rated"),
        "issues": issues,
    }
    return {key: value for key, value in report.items() if value is not None}


def build_ops_report(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    local = result.get("local") if isinstance(result.get("local"), dict) else {}
    csl = local.get("csl_live_odds") if isinstance(local.get("csl_live_odds"), dict) else {}
    return {
        "schema_version": 1,
        "status": _report_status_from_counts(summary),
        "errors": _as_int(summary.get("errors")),
        "warnings": _as_int(summary.get("warnings")),
        "csl_live_odds": _report_csl_live_odds(csl),
    }


def _format_bool(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


def _format_list(value: Any) -> str:
    items = [str(item) for item in value] if isinstance(value, list) else []
    return ",".join(items) if items else "none"


def format_ops_report(report: dict[str, Any]) -> str:
    csl = report.get("csl_live_odds") if isinstance(report.get("csl_live_odds"), dict) else {}
    lines = [
        (
            f"ops_check: {report.get('status')} "
            f"errors={_as_int(report.get('errors'))} "
            f"warnings={_as_int(report.get('warnings'))}"
        ),
        (
            f"CSL live odds: {csl.get('status')} "
            f"events={csl.get('events', 'n/a')} "
            f"fixtures={csl.get('fixtures', 'n/a')} "
            f"odds_events={csl.get('odds_events', 'n/a')}"
        ),
        (
            f"  observed_at={csl.get('observed_at', 'n/a')} "
            f"provider={csl.get('provider', 'n/a')} "
            f"quota_remaining={csl.get('quota_remaining', 'n/a')} "
            f"quota_last={csl.get('quota_last', 'n/a')}"
        ),
        (
            f"  guards: synthetic={_format_bool(csl.get('has_synthetic_marker'))} "
            f"alias_unmatched={_as_int(csl.get('club_alias_unmatched_count'))} "
            f"invalid_odds={_as_int(csl.get('invalid_odds_count'))} "
            f"issues={_format_list(csl.get('issues'))}"
        ),
        (
            f"  runner: {csl.get('runner_status', 'n/a')} "
            f"matches={csl.get('runner_matches', 'n/a')} "
            f"rating_policy={csl.get('rating_policy', 'n/a')} "
            f"club_rating={csl.get('club_rating_mode', 'n/a')} "
            f"replayed={csl.get('club_rating_matches_replayed', 'n/a')} "
            f"teams={csl.get('club_rating_teams_rated', 'n/a')}"
        ),
        (
            f"  warnings={_format_list(csl.get('runner_warnings'))} "
            f"strong_grades={_format_list(csl.get('runner_strong_grades'))}"
        ),
    ]
    return "\n".join(lines)


def run_ops_check(
    root: str | Path = ".",
    public_base_url: str | None = DEFAULT_PUBLIC_BASE_URL,
    remote_host: str | None = DEFAULT_REMOTE_HOST,
    fetcher: Fetcher = _default_fetcher,
    remote_runner: RemoteRunner = _default_remote_runner,
    launch_agent_path: str | Path = DEFAULT_LAUNCH_AGENT,
    local_log_paths: list[str | Path] | None = None,
    pre_match_launch_agent_path: str | Path | None = DEFAULT_PRE_MATCH_LAUNCH_AGENT,
    pre_match_log_paths: list[str | Path] | None = None,
    lineup_audit_path: str | Path = DEFAULT_LINEUP_AUDIT_PATH,
    timeout: int = 20,
) -> dict[str, Any]:
    project_root = Path(root)
    result: dict[str, Any] = {
        "schema_version": 1,
        "local": _local_checks(
            project_root,
            launch_agent_path=launch_agent_path,
            local_log_paths=list(DEFAULT_LOCAL_LOGS if local_log_paths is None else local_log_paths),
            pre_match_launch_agent_path=pre_match_launch_agent_path,
            pre_match_log_paths=list(
                DEFAULT_PRE_MATCH_LOGS if pre_match_log_paths is None else pre_match_log_paths
            ),
            lineup_audit_path=lineup_audit_path,
        ),
        "public": {"status": "skipped"}
        if public_base_url is None
        else _public_checks(public_base_url, fetcher=fetcher, timeout=timeout),
        "remote": _remote_checks(remote_host, runner=remote_runner, timeout=timeout),
    }
    summary = _count_issues(result)
    result["summary"] = summary
    result["ok"] = summary["errors"] == 0
    result["report"] = build_ops_report(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run read-only operational checks for local scheduler, public site, and ECS."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    parser.add_argument("--no-public", action="store_true")
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--no-remote", action="store_true")
    parser.add_argument("--launch-agent", default=str(DEFAULT_LAUNCH_AGENT))
    parser.add_argument("--local-log", action="append", dest="local_logs")
    parser.add_argument("--pre-match-launch-agent", default=str(DEFAULT_PRE_MATCH_LAUNCH_AGENT))
    parser.add_argument("--pre-match-log", action="append", dest="pre_match_logs")
    parser.add_argument("--lineup-audit", default=str(DEFAULT_LINEUP_AUDIT_PATH))
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--format", choices=("json", "summary"), default="json")
    args = parser.parse_args(argv)

    result = run_ops_check(
        root=args.root,
        public_base_url=None if args.no_public else args.public_base_url,
        remote_host=None if args.no_remote else args.remote_host,
        launch_agent_path=args.launch_agent,
        local_log_paths=args.local_logs,
        pre_match_launch_agent_path=args.pre_match_launch_agent,
        pre_match_log_paths=args.pre_match_logs,
        lineup_audit_path=args.lineup_audit,
        timeout=args.timeout,
    )
    if args.format == "summary":
        print(format_ops_report(result["report"]))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
