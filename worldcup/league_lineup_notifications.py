from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS, get_competition
from worldcup.ledger import format_match_decision_market_label, format_probability
from worldcup.notifications import send_wxpusher_notification


DISCLAIMER = "仅供研究分析，不构成投注建议。"
STATE_RELATIVE_PATH = Path("data/local/leagues/lineup_notification_state.json")
_BEIJING = ZoneInfo("Asia/Shanghai")
_EVENT_TYPES = frozenset(
    {
        "published_refresh_changed",
        "published_refresh_unchanged",
        "missing_confirmed",
        "quota_blocked",
        "sustained_source_failure",
        "source_recovery",
    }
)
_FORBIDDEN_TEXT = (
    "authorization",
    "cookie",
    "edge",
    "header",
    "legacy",
    "provider_response",
    "raw",
    "secret",
    "token",
    "uid",
)


def _required_text(value: Any, error: str = "league_lineup_notification_event_invalid") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(error)
    return value.strip()


def _aware_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("league_lineup_notification_event_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("league_lineup_notification_event_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("league_lineup_notification_event_invalid")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: Any) -> str:
    return _aware_datetime(value).isoformat()


def _beijing_text(value: Any) -> str:
    return _aware_datetime(value).astimezone(_BEIJING).strftime("%Y-%m-%d %H:%M（北京时间）")


def _valid_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _source_hash(value: Any) -> str:
    source = _required_text(value)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _event_fingerprint(event_type: str, competition_id: str, event_id: str, source_hash: str) -> str:
    encoded = json.dumps(
        {
            "competition_id": competition_id,
            "event_id": event_id,
            "event_type": event_type,
            "source_fingerprint": source_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _safe_pick(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("label") == "NO_CLEAN_MARKET":
        return None
    market = value.get("market")
    selection = value.get("selection")
    if not isinstance(market, str) or not market or not isinstance(selection, str) or not selection:
        return None
    return {
        "market": market,
        "selection": selection,
        "line": _safe_float(value.get("line")),
        "p_hit_safe": _safe_float(value.get("p_hit_safe")),
        "odds": _safe_float(value.get("odds")),
    }


def _pick_identity(value: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if value is None:
        return None
    return value["market"], value["selection"], value["line"]


def _pick_label(value: dict[str, Any] | None) -> str:
    if value is None:
        return "暂无可靠首选"
    return format_match_decision_market_label(value)


def _safe_common(
    *,
    competition_id: Any,
    event_id: Any,
    home_team: Any,
    away_team: Any,
    kickoff_at_utc: Any,
    source_fingerprint: Any,
) -> dict[str, str]:
    if competition_id not in FORMAL_SINGLE_MATCH_IDS:
        raise ValueError("league_lineup_notification_event_invalid")
    return {
        "competition_id": competition_id,
        "event_id": _required_text(event_id),
        "home_team": _required_text(home_team),
        "away_team": _required_text(away_team),
        "kickoff_at_utc": _utc_text(kickoff_at_utc),
        "source_fingerprint": _source_hash(source_fingerprint),
    }


def _validate_common_payload(value: Mapping[str, Any]) -> dict[str, str]:
    competition_id = value.get("competition_id")
    if competition_id not in FORMAL_SINGLE_MATCH_IDS or not _valid_hash(value.get("source_fingerprint")):
        raise ValueError("league_lineup_notification_event_invalid")
    return {
        "competition_id": competition_id,
        "event_id": _required_text(value.get("event_id")),
        "home_team": _required_text(value.get("home_team")),
        "away_team": _required_text(value.get("away_team")),
        "kickoff_at_utc": _utc_text(value.get("kickoff_at_utc")),
        "source_fingerprint": value["source_fingerprint"],
    }


def _build_event(
    *,
    event_type: str,
    common: Mapping[str, str],
    summary: str,
    lines: list[str],
) -> dict[str, Any]:
    content = "\n".join([*lines, "", DISCLAIMER])
    event = {
        "schema_version": 1,
        "event_type": event_type,
        "event_fingerprint": _event_fingerprint(
            event_type,
            common["competition_id"],
            common["event_id"],
            common["source_fingerprint"],
        ),
        "summary": summary,
        "content": content,
        "payload": dict(common),
    }
    return _validate_event(event)


def _base_lines(common: Mapping[str, str]) -> list[str]:
    competition_name = get_competition(common["competition_id"]).name
    return [
        f"联赛：{competition_name}",
        f"比赛：{common['home_team']} vs {common['away_team']}",
        f"开球：{_beijing_text(common['kickoff_at_utc'])}",
    ]


def build_published_refresh_event(
    *,
    competition_id: str,
    event_id: str,
    home_team: str,
    away_team: str,
    kickoff_at_utc: str,
    lineup_fingerprint: str,
    confirmed_at: str,
    publish_status: str | None,
    previous_decision: Mapping[str, Any] | None,
    current_decision: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if publish_status not in {"stored", "duplicate"}:
        return None
    if not _valid_hash(lineup_fingerprint):
        raise ValueError("league_lineup_notification_event_invalid")
    common = _safe_common(
        competition_id=competition_id,
        event_id=event_id,
        home_team=home_team,
        away_team=away_team,
        kickoff_at_utc=kickoff_at_utc,
        source_fingerprint=lineup_fingerprint,
    )
    confirmed_at_utc = _utc_text(confirmed_at)
    if _aware_datetime(confirmed_at_utc) >= _aware_datetime(common["kickoff_at_utc"]):
        raise ValueError("league_lineup_notification_event_invalid")
    before = _safe_pick(previous_decision)
    after = _safe_pick(current_decision)
    changed = _pick_identity(before) != _pick_identity(after)
    event_type = "published_refresh_changed" if changed else "published_refresh_unchanged"
    competition_name = get_competition(competition_id).name
    lines = [
        *_base_lines(common),
        f"双方首发已确认：{_beijing_text(confirmed_at_utc)}",
    ]
    if changed:
        lines.append(f"本场首选：{_pick_label(before)} → {_pick_label(after)}")
    else:
        lines.append("首发后复核：方向未变")
        lines.append(f"本场首选：{_pick_label(after)}")
    probability = None if after is None else after["p_hit_safe"]
    odds = None if after is None else after["odds"]
    lines.append(f"新安全概率：{format_probability(probability)}")
    lines.append(f"新参考赔率：{'—' if odds is None else f'{odds:.2f}'}")
    return _build_event(
        event_type=event_type,
        common=common,
        summary=f"{competition_name}首发后本场首选已更新",
        lines=lines,
    )


def _build_degraded_event(
    *,
    event_type: str,
    competition_id: str,
    event_id: str,
    home_team: str,
    away_team: str,
    kickoff_at_utc: str,
    source_fingerprint: str,
    message: str,
) -> dict[str, Any]:
    common = _safe_common(
        competition_id=competition_id,
        event_id=event_id,
        home_team=home_team,
        away_team=away_team,
        kickoff_at_utc=kickoff_at_utc,
        source_fingerprint=source_fingerprint,
    )
    competition_name = get_competition(competition_id).name
    return _build_event(
        event_type=event_type,
        common=common,
        summary=f"{competition_name}首发跟踪提醒",
        lines=[*_base_lines(common), message],
    )


def build_missing_lineup_event(**kwargs: Any) -> dict[str, Any]:
    return _build_degraded_event(
        event_type="missing_confirmed",
        message="首发未确认，保留原推荐",
        **kwargs,
    )


def build_quota_blocked_event(**kwargs: Any) -> dict[str, Any]:
    return _build_degraded_event(
        event_type="quota_blocked",
        message="首发已保存，赔率刷新被额度保护阻断，保留原推荐",
        **kwargs,
    )


def build_source_failure_event(
    *,
    failure_count: int,
    error_details: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    del error_details
    if isinstance(failure_count, bool) or not isinstance(failure_count, int) or failure_count < 1:
        raise ValueError("league_lineup_notification_event_invalid")
    return _build_degraded_event(
        event_type="sustained_source_failure",
        message=f"首发数据源连续失败（{failure_count} 次），保留原推荐",
        **kwargs,
    )


def build_source_recovery_event(*, error_details: Any = None, **kwargs: Any) -> dict[str, Any]:
    del error_details
    return _build_degraded_event(
        event_type="source_recovery",
        message="首发数据源已恢复，将继续按规则跟踪确认首发",
        **kwargs,
    )


def _validate_event(value: Any) -> dict[str, Any]:
    expected = {"schema_version", "event_type", "event_fingerprint", "summary", "content", "payload"}
    if not isinstance(value, Mapping) or set(value) != expected or value.get("schema_version") != 1:
        raise ValueError("league_lineup_notification_event_invalid")
    event_type = value.get("event_type")
    payload = value.get("payload")
    payload_expected = {
        "competition_id",
        "event_id",
        "home_team",
        "away_team",
        "kickoff_at_utc",
        "source_fingerprint",
    }
    if event_type not in _EVENT_TYPES or not isinstance(payload, Mapping) or set(payload) != payload_expected:
        raise ValueError("league_lineup_notification_event_invalid")
    common = _validate_common_payload(payload)
    summary = _required_text(value.get("summary"))
    content = _required_text(value.get("content"))
    lowered = f"{summary}\n{content}".casefold()
    if DISCLAIMER not in content or any(forbidden in lowered for forbidden in _FORBIDDEN_TEXT):
        raise ValueError("league_lineup_notification_event_invalid")
    fingerprint = value.get("event_fingerprint")
    expected_fingerprint = _event_fingerprint(
        event_type,
        common["competition_id"],
        common["event_id"],
        common["source_fingerprint"],
    )
    if not _valid_hash(fingerprint) or fingerprint != expected_fingerprint:
        raise ValueError("league_lineup_notification_event_invalid")
    return {
        "schema_version": 1,
        "event_type": event_type,
        "event_fingerprint": fingerprint,
        "summary": summary,
        "content": content,
        "payload": common,
    }


def _empty_state() -> dict[str, Any]:
    return {"schema_version": 1, "pending": {}, "sent": {}}


def _validate_state(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schema_version", "pending", "sent"}
        or value.get("schema_version") != 1
        or not isinstance(value.get("pending"), Mapping)
        or not isinstance(value.get("sent"), Mapping)
    ):
        raise ValueError("league_lineup_notification_state_invalid")
    pending: dict[str, dict[str, Any]] = {}
    for fingerprint, event in value["pending"].items():
        try:
            checked = _validate_event(event)
        except ValueError as exc:
            raise ValueError("league_lineup_notification_state_invalid") from exc
        if fingerprint != checked["event_fingerprint"]:
            raise ValueError("league_lineup_notification_state_invalid")
        pending[fingerprint] = checked
    sent: dict[str, dict[str, str]] = {}
    for fingerprint, receipt in value["sent"].items():
        if (
            not _valid_hash(fingerprint)
            or not isinstance(receipt, Mapping)
            or set(receipt) != {"event_type", "sent_at"}
            or receipt.get("event_type") not in _EVENT_TYPES
        ):
            raise ValueError("league_lineup_notification_state_invalid")
        try:
            sent_at = _utc_text(receipt.get("sent_at"))
        except ValueError as exc:
            raise ValueError("league_lineup_notification_state_invalid") from exc
        sent[fingerprint] = {"event_type": receipt["event_type"], "sent_at": sent_at}
    if set(pending).intersection(sent):
        raise ValueError("league_lineup_notification_state_invalid")
    return {"schema_version": 1, "pending": pending, "sent": sent}


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("league_lineup_notification_state_invalid") from exc
    return _validate_state(value)


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class LeagueLineupNotificationOutbox:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.path = self.root / STATE_RELATIVE_PATH
        self.lock_path = self.path.with_suffix(".lock")

    def deliver(
        self,
        event: Mapping[str, Any] | None,
        *,
        notify: bool = False,
        notifier: Callable[..., Mapping[str, Any]] = send_wxpusher_notification,
    ) -> dict[str, Any]:
        if event is None:
            return {"status": "skipped", "event_fingerprint": None}
        checked = _validate_event(event)
        fingerprint = checked["event_fingerprint"]
        result = {"event_fingerprint": fingerprint}
        if not notify:
            return {"status": "dry_run", **result}

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = _read_state(self.path)
            if fingerprint in state["sent"]:
                return {"status": "already_sent", **result}
            state["pending"].setdefault(fingerprint, checked)
            _atomic_write(self.path, state)
            pending_event = state["pending"][fingerprint]
            try:
                notification_result = notifier(
                    pending_event["content"],
                    summary=pending_event["summary"],
                )
            except Exception:
                return {"status": "failed", **result}
            if not isinstance(notification_result, Mapping) or notification_result.get("status") != "sent":
                return {"status": "failed", **result}
            del state["pending"][fingerprint]
            state["sent"][fingerprint] = {
                "event_type": checked["event_type"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            _atomic_write(self.path, state)
            return {"status": "sent", **result}

    def retry_pending(
        self,
        *,
        notify: bool = False,
        notifier: Callable[..., Mapping[str, Any]] = send_wxpusher_notification,
    ) -> dict[str, Any]:
        if not notify:
            state = _read_state(self.path)
            return {"status": "dry_run", "pending": len(state["pending"])}

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            pending = list(_read_state(self.path)["pending"].values())

        sent = 0
        failed = 0
        for event in pending:
            result = self.deliver(event, notify=True, notifier=notifier)
            if result["status"] in {"sent", "already_sent"}:
                sent += 1
            else:
                failed += 1
        return {"status": "complete", "sent": sent, "failed": failed}
