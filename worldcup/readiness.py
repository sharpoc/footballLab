from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from worldcup.store_factory import normalize_store_kind

REQUIRED_ENV_EXAMPLE_NAMES = {
    "API_FOOTBALL_KEY",
    "THE_ODDS_API_KEY",
    "THE_ODDS_API_KEY_PRIMARY",
    "THE_ODDS_API_KEY_SECONDARY",
    "THE_ODDS_API_KEY_TERTIARY",
    "ODDS_API_IO_KEY",
    "ODDSPAPI_KEY",
    "INGEST_HMAC_SECRET",
    "WORLDCUP_STORE",
    "DATABASE_URL",
}


def _read_env_entries(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    entries: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        entries[name.strip()] = value.strip()
    return entries


def _read_env_names(path: Path) -> set[str]:
    return set(_read_env_entries(path))


def _ignore_patterns(root: Path) -> set[str]:
    path = root / ".gitignore"
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def _check_file(root: Path, key: str, relative: str, required: bool = True) -> tuple[str, dict[str, Any]]:
    path = root / relative
    if path.exists():
        return key, {"status": "ok", "path": relative}
    return key, {
        "status": "error" if required else "warn",
        "path": relative,
        "message": "missing",
    }


def _load_json_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, "invalid_json"
    except OSError:
        return None, "unreadable"
    if not isinstance(data, dict):
        return None, "not_object"
    return data, None


def _check_snapshot(root: Path) -> tuple[str, dict[str, Any]]:
    relative = "data/cache/analysis_snapshot.json"
    path = root / relative
    if not path.exists():
        return "cache_snapshot", {"status": "error", "path": relative, "message": "missing"}

    data, error = _load_json_file(path)
    if error is not None:
        return "cache_snapshot", {"status": "error", "path": relative, "message": error}

    matches = data.get("matches")
    if not isinstance(matches, list) or not matches:
        return "cache_snapshot", {"status": "error", "path": relative, "message": "no_matches"}
    return "cache_snapshot", {"status": "ok", "path": relative, "matches": len(matches)}


def _check_quota(root: Path) -> tuple[str, dict[str, Any]]:
    relative = "data/cache/quota.json"
    path = root / relative
    if not path.exists():
        return "cache_quota", {"status": "warn", "path": relative, "message": "missing"}
    _, error = _load_json_file(path)
    if error is not None:
        return "cache_quota", {"status": "warn", "path": relative, "message": error}
    return "cache_quota", {"status": "ok", "path": relative}


def _check_html(root: Path, key: str, relative: str, required: bool) -> tuple[str, dict[str, Any]]:
    path = root / relative
    if not path.exists():
        return key, {
            "status": "error" if required else "warn",
            "path": relative,
            "message": "missing",
        }
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        return key, {"status": "error", "path": relative, "message": "unreadable"}
    if "仅用于研究分析，不构成投注建议" not in html:
        return key, {"status": "error", "path": relative, "message": "missing_disclaimer"}
    return key, {"status": "ok", "path": relative}


def _check_env(env_path: Path, name: str) -> tuple[str, dict[str, Any]]:
    entries = _read_env_entries(env_path)
    key = f"env_{name}"
    if name not in entries:
        return key, {"status": "error", "name": name, "message": "missing"}
    if name == "INGEST_HMAC_SECRET":
        value = entries[name].strip('"').strip("'")
        from worldcup.secrets import validate_hmac_secret
        try:
            validate_hmac_secret(value)
        except ValueError:
            return key, {"status": "error", "name": name, "message": "weak_secret"}
    return key, {"status": "ok", "name": name}


def _check_env_example(root: Path) -> tuple[str, dict[str, Any]]:
    relative = ".env.example"
    path = root / relative
    if not path.exists():
        return "env_example", {"status": "error", "path": relative, "message": "missing"}

    entries = _read_env_entries(path)
    names_with_values = sorted(name for name, value in entries.items() if value)
    if names_with_values:
        return "env_example", {
            "status": "error",
            "path": relative,
            "message": "contains_values",
            "names": names_with_values,
        }

    missing = sorted(REQUIRED_ENV_EXAMPLE_NAMES - set(entries))
    if missing:
        return "env_example", {
            "status": "error",
            "path": relative,
            "message": "missing_names",
            "names": missing,
        }

    patterns = _ignore_patterns(root)
    if ".env.*" in patterns and "!.env.example" not in patterns:
        return "env_example", {
            "status": "error",
            "path": relative,
            "message": "ignored_by_env_wildcard",
        }

    return "env_example", {"status": "ok", "path": relative, "names": sorted(entries)}


def _check_store_env(env_path: Path) -> tuple[str, dict[str, Any]]:
    entries = _read_env_entries(env_path)
    store = normalize_store_kind(entries.get("WORLDCUP_STORE"))
    if store not in {"sqlite", "postgres"}:
        return "env_store", {
            "status": "error",
            "name": "WORLDCUP_STORE",
            "message": "unsupported_store",
            "store": store,
        }
    if store == "postgres" and "DATABASE_URL" not in entries:
        return "env_store", {
            "status": "error",
            "name": "DATABASE_URL",
            "message": "missing_DATABASE_URL",
            "store": store,
        }
    return "env_store", {"status": "ok", "name": "WORLDCUP_STORE", "store": store}


def _check_ignore(root: Path, key: str, pattern: str) -> tuple[str, dict[str, Any]]:
    patterns = _ignore_patterns(root)
    if pattern in patterns:
        return key, {"status": "ok", "pattern": pattern}
    return key, {"status": "error", "pattern": pattern, "message": "not_ignored"}


PROFILES: dict[str, list[str]] = {
    "full": [
        "env_THE_ODDS_API_KEY",
        "env_INGEST_HMAC_SECRET",
        "env_example",
        "env_store",
        "cache_snapshot",
        "cache_quota",
        "cache_preview",
        "static_site_index",
        "ignored_env",
        "ignored_data_cache",
        "ignored_data_local",
        "ignored_data_probe",
    ],
    "server": [
        "env_INGEST_HMAC_SECRET",
        "env_store",
    ],
    "publisher": [
        "env_INGEST_HMAC_SECRET",
        "env_THE_ODDS_API_KEY",
        "cache_snapshot",
        "cache_quota",
    ],
}


def run_readiness_checks(
    root: str | Path = ".",
    *,
    profile: str = "full",
    env_path: str | Path | None = None,
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile}")

    project_root = Path(root)
    resolved_env_path = Path(env_path) if env_path is not None else project_root / ".env"

    all_checks = [
        _check_env(resolved_env_path, "THE_ODDS_API_KEY"),
        _check_env(resolved_env_path, "INGEST_HMAC_SECRET"),
        _check_env_example(project_root),
        _check_store_env(resolved_env_path),
        _check_snapshot(project_root),
        _check_quota(project_root),
        _check_html(project_root, "cache_preview", "data/cache/preview.html", required=False),
        _check_html(project_root, "static_site_index", "data/cache/site/index.html", required=False),
        _check_ignore(project_root, "ignored_env", ".env"),
        _check_ignore(project_root, "ignored_data_cache", "data/cache/"),
        _check_ignore(project_root, "ignored_data_local", "data/local/"),
        _check_ignore(project_root, "ignored_data_probe", "data/probe/"),
    ]

    allowed = set(PROFILES[profile])
    checks: dict[str, dict[str, Any]] = {}
    for key, check in all_checks:
        if key in allowed:
            checks[key] = check

    errors = sum(1 for check in checks.values() if check["status"] == "error")
    warnings = sum(1 for check in checks.values() if check["status"] == "warn")
    return {
        "ok": errors == 0,
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "checks": len(checks),
        },
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local readiness checks without contacting the network.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--profile", choices=list(PROFILES.keys()), default="full")
    parser.add_argument("--env-path", default=None, help="Full path to .env file (overrides root/.env)")
    args = parser.parse_args(argv)

    result = run_readiness_checks(args.root, profile=args.profile, env_path=args.env_path)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
