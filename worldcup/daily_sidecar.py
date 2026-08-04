from __future__ import annotations

import argparse
import fcntl
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from worldcup.daily_competitions import daily_competition_catalog, resolve_provider_catalog
from worldcup.daily_odds_refresh import plan_daily_odds_refresh
from worldcup.daily_odds_store import DailyOddsSnapshotWriter
from worldcup.daily_odds_state import DailyOddsState
from worldcup.quota import load_quota_ledger
from worldcup.refresh_runner import _load_env
from worldcup.scheduled_refresh import run_daily_odds_refresh
from worldcup.sources.theoddsapi import fetch_events_for_sport, fetch_odds_for_sport
from worldcup.sources.theoddsapi_sports import fetch_sports_catalog
from worldcup.theoddsapi_keys import KeySlotSelection, choose_key_slot

DEFAULT_DATA_DIR = Path("data/cache/daily_odds")
DEFAULT_PRODUCTION_DATA_DIR = Path("/var/lib/worldcup/daily_odds")
DEFAULT_DAILY_BUDGET_CREDITS = 85


class SidecarProviderError(RuntimeError):
    pass


@dataclass
class _Provider:
    api_key: str
    provider: str
    data_dir: Path
    quota_path: Path
    quota_by_sport: dict[str, int | None]
    calls: int = 0

    def _record(self, sport_key: str, result: Any) -> Any:
        self.calls += 1
        entry = getattr(result, "quota_entry", None)
        if isinstance(entry, Mapping):
            remaining = entry.get("remaining")
            self.quota_by_sport[sport_key] = remaining if isinstance(remaining, int) else None
        return getattr(result, "json_body", result)

    def sports(self) -> list[dict[str, Any]]:
        try:
            result = fetch_sports_catalog(self.api_key)
        except Exception as exc:
            raise SidecarProviderError(type(exc).__name__) from exc
        body = self._record("__catalog__", result)
        return body if isinstance(body, list) else []

    def events(self, sport_key: str) -> Any:
        try:
            result = fetch_events_for_sport(
                api_key=self.api_key,
                sport_key=sport_key,
                quota_path=self.quota_path,
                quota_provider=self.provider,
            )
        except Exception as exc:
            raise SidecarProviderError(type(exc).__name__) from exc
        return self._record(sport_key, result)

    def odds(self, sport_key: str, markets: tuple[str, ...]) -> Any:
        try:
            result = fetch_odds_for_sport(
                api_key=self.api_key,
                sport_key=sport_key,
                markets=markets,
                quota_path=self.quota_path,
                quota_provider=self.provider,
            )
        except Exception as exc:
            raise SidecarProviderError(type(exc).__name__) from exc
        return self._record(sport_key, result)


def default_data_dir() -> Path:
    configured = os.environ.get("WORLDCUP_DAILY_ODDS_DATA_DIR", "").strip()
    return Path(configured) if configured else DEFAULT_DATA_DIR


def _paths(data_dir: str | Path) -> tuple[Path, Path, Path]:
    root = Path(data_dir)
    return root, root / "daily_odds_snapshot.json", root / "daily_odds_state.json"


def _safe_env_value(value: str | None) -> str:
    return "present" if value else "absent"


def _provider_from_factory(
    factory: Callable[..., Any],
    *,
    selected: KeySlotSelection,
    data_dir: Path,
    quota_path: Path,
) -> tuple[Callable[[], Any], Callable[[str], Any], Callable[[str, tuple[str, ...]], Any], dict[str, int | None], Any]:
    produced = factory(
        api_key=selected.api_key,
        provider=selected.provider,
        data_dir=data_dir,
        quota_path=quota_path,
    )
    if isinstance(produced, _Provider):
        return produced.sports, produced.events, produced.odds, produced.quota_by_sport, produced
    if isinstance(produced, Mapping):
        sports_value = produced.get("sports", [])
        events_value = produced.get("events", {})
        odds_value = produced.get("odds", {})
        quota = dict(produced.get("quota") or {})

        def sports() -> Any:
            value = sports_value() if callable(sports_value) else sports_value
            return value

        def events(sport_key: str) -> Any:
            value = events_value.get(sport_key, []) if isinstance(events_value, Mapping) else []
            return value() if callable(value) else value

        def odds(sport_key: str, markets: tuple[str, ...]) -> Any:
            value = odds_value.get(sport_key, []) if isinstance(odds_value, Mapping) else []
            return value(sport_key, markets) if callable(value) else value

        class _Injected:
            calls = 0

        return sports, events, odds, quota, _Injected()
    raise TypeError("daily_sidecar_invalid_provider")


def _default_factory(*, api_key: str, provider: str, data_dir: Path, quota_path: Path) -> _Provider:
    return _Provider(api_key, provider, data_dir, quota_path, {})


def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SidecarProviderError("daily_sidecar_locked")
    return handle


def run_production_sidecar(
    *,
    live: bool = False,
    now: str | None = None,
    env_path: str | Path = ".env",
    data_dir: str | Path | None = None,
    daily_budget_credits: int = DEFAULT_DAILY_BUDGET_CREDITS,
    provider_factory: Callable[..., Any] = _default_factory,
) -> dict[str, Any]:
    root, snapshot_path, state_path = _paths(data_dir or default_data_dir())
    budget = int(daily_budget_credits)
    if budget < 0 or budget > DEFAULT_DAILY_BUDGET_CREDITS:
        return {
            "status": "blocked",
            "reason": "daily_budget_out_of_range",
            "daily_budget_credits": budget,
            "data_dir": str(root),
            "provider_calls": 0,
        }
    if not live:
        return {
            "status": "dry_run",
            "reason": "live_not_enabled",
            "daily_budget_credits": budget,
            "data_dir": str(root),
            "snapshot_path": str(snapshot_path),
            "state_path": str(state_path),
            "provider_calls": 0,
            "credentials": "not_read",
        }

    env = _load_env(env_path)
    ledger = load_quota_ledger(root / "quota.json")
    selected = choose_key_slot(env, ledger.get("providers") or {})
    if selected is None:
        return {
            "status": "blocked",
            "reason": "provider_credentials_or_quota_unavailable",
            "daily_budget_credits": budget,
            "data_dir": str(root),
            "provider_calls": 0,
            "credentials": {
                "primary": _safe_env_value(env.get("THE_ODDS_API_KEY_PRIMARY") or env.get("THE_ODDS_API_KEY")),
                "secondary": _safe_env_value(env.get("THE_ODDS_API_KEY_SECONDARY")),
                "tertiary": _safe_env_value(env.get("THE_ODDS_API_KEY_TERTIARY")),
            },
        }

    lock_handle = _lock(root / "daily_odds_refresh.lock")
    try:
        sports_fetcher, events_fetcher, odds_fetcher, quota_map, provider = _provider_from_factory(
            provider_factory,
            selected=selected,
            data_dir=root,
            quota_path=root / "quota.json",
        )
        result = run_daily_odds_refresh(
            enabled=True,
            live=True,
            now=now or datetime.now(timezone.utc).isoformat(),
            cache_dir=root,
            snapshot_path=snapshot_path,
            state_path=state_path,
            sports_fetcher=sports_fetcher,
            events_fetcher=events_fetcher,
            odds_fetcher=odds_fetcher,
            snapshot_writer=DailyOddsSnapshotWriter(snapshot_path),
            state=DailyOddsState(state_path),
            quota_remaining_by_key=quota_map,
            daily_budget_credits=budget,
        )
        provider_calls = int(getattr(provider, "calls", 0))
        refresh = result.get("refresh") if isinstance(result, Mapping) else None
        if isinstance(refresh, Mapping) and int(refresh.get("estimated_credits") or 0) > budget:
            return {
                "status": "blocked",
                "reason": "daily_budget_exceeded",
                "provider": selected.provider,
                "provider_slot": selected.slot,
                "provider_calls": provider_calls,
                "daily_budget_credits": budget,
                "data_dir": str(root),
            }
        return {
            **dict(result),
            "provider": selected.provider,
            "provider_slot": selected.slot,
            "provider_calls": provider_calls,
            "daily_budget_credits": budget,
            "data_dir": str(root),
            "snapshot_path": str(snapshot_path),
            "state_path": str(state_path),
            "credentials": {
                "primary": _safe_env_value(env.get("THE_ODDS_API_KEY_PRIMARY") or env.get("THE_ODDS_API_KEY")),
                "secondary": _safe_env_value(env.get("THE_ODDS_API_KEY_SECONDARY")),
                "tertiary": _safe_env_value(env.get("THE_ODDS_API_KEY_TERTIARY")),
            },
        }
    except SidecarProviderError as exc:
        return {
            "status": "blocked",
            "reason": str(exc),
            "provider": selected.provider,
            "provider_slot": selected.slot,
            "provider_calls": int(getattr(locals().get("provider"), "calls", 0)),
            "daily_budget_credits": budget,
            "data_dir": str(root),
        }
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def main(
    argv: list[str] | None = None,
    *,
    provider_factory: Callable[..., Any] = _default_factory,
) -> int:
    parser = argparse.ArgumentParser(description="Run the production daily odds sidecar; dry-run is the default.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument("--daily-budget-credits", type=int, default=DEFAULT_DAILY_BUDGET_CREDITS)
    args = parser.parse_args(argv)
    result = run_production_sidecar(
        live=bool(args.live),
        now=args.now,
        env_path=args.env,
        data_dir=args.data_dir,
        daily_budget_credits=args.daily_budget_credits,
        provider_factory=provider_factory,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") not in {"blocked", "failed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
