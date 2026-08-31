from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
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
DEFAULT_SOURCE_FAILURE_THRESHOLD = 3
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
_COMMON_PAYLOAD_FIELDS = frozenset(
    {
        "competition_id",
        "event_id",
        "home_team",
        "away_team",
        "kickoff_at_utc",
        "source_fingerprint",
    }
)
_SENSITIVE_DISPLAY_WORD = re.compile(
    r"\b(amount|authorization|cookie|edge|ev|header|legacy|raw|secret|token|uid)\b"
)


def _required_text(value: Any, error: str = "league_lineup_notification_event_invalid") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(error)
    return value.strip()


def _safe_display_text(value: Any) -> str:
    text = _required_text(value)
    if len(text) > 160 or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError("league_lineup_notification_event_invalid")
    normalized = re.sub(r"[_-]+", " ", text).casefold()
    if (
        "金额" in normalized
        or re.search(r"\bprovider\s+response\b", normalized)
        or _SENSITIVE_DISPLAY_WORD.search(normalized)
    ):
        raise ValueError("league_lineup_notification_event_invalid")
    return text


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


def _event_fingerprint(event_type: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {
            "event_type": event_type,
            "payload": payload,
        },
        ensure_ascii=False,
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


def _safe_pick(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 2:
        raise ValueError("league_lineup_notification_event_invalid")
    label = value.get("label")
    if label == "NO_CLEAN_MARKET":
        if any(
            value.get(field) is not None
            for field in ("market", "selection", "line", "p_hit_safe", "odds")
        ):
            raise ValueError("league_lineup_notification_event_invalid")
        return {"schema_version": 2, "label": "NO_CLEAN_MARKET"}
    if label != "MATCH_PICK":
        raise ValueError("league_lineup_notification_event_invalid")

    market = value.get("market")
    selection = value.get("selection")
    allowed_selections = {
        "1X2": {"home", "draw", "away"},
        "DNB": {"home", "away"},
        "AH": {"home", "away"},
        "OU": {"over", "under"},
    }
    if (
        not isinstance(market, str)
        or not isinstance(selection, str)
        or market not in allowed_selections
        or selection not in allowed_selections[market]
    ):
        raise ValueError("league_lineup_notification_event_invalid")

    raw_line = value.get("line")
    line = _safe_float(raw_line)
    if market == "1X2":
        if raw_line is not None:
            raise ValueError("league_lineup_notification_event_invalid")
        line = None
    elif line is None:
        raise ValueError("league_lineup_notification_event_invalid")
    elif market == "DNB" and abs(line) > 1e-12:
        raise ValueError("league_lineup_notification_event_invalid")
    elif market == "AH" and abs(line) <= 1e-12:
        raise ValueError("league_lineup_notification_event_invalid")
    elif market == "OU" and line <= 0:
        raise ValueError("league_lineup_notification_event_invalid")
    if market == "DNB":
        line = 0.0

    probability = _safe_float(value.get("p_hit_safe"))
    odds = _safe_float(value.get("odds"))
    if probability is None or not 0.0 <= probability <= 1.0 or odds is None or odds <= 1.0:
        raise ValueError("league_lineup_notification_event_invalid")
    return {
        "schema_version": 2,
        "label": "MATCH_PICK",
        "market": market,
        "selection": selection,
        "line": line,
        "p_hit_safe": probability,
        "odds": odds,
    }


def _pick_identity(value: dict[str, Any]) -> tuple[Any, ...] | None:
    if value["label"] == "NO_CLEAN_MARKET":
        return None
    return value["market"], value["selection"], value["line"]


def _pick_label(value: dict[str, Any]) -> str:
    if value["label"] == "NO_CLEAN_MARKET":
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
        "event_id": _safe_display_text(event_id),
        "home_team": _safe_display_text(home_team),
        "away_team": _safe_display_text(away_team),
        "kickoff_at_utc": _utc_text(kickoff_at_utc),
        "source_fingerprint": _source_hash(source_fingerprint),
    }


def _validate_common_payload(value: Mapping[str, Any]) -> dict[str, str]:
    competition_id = value.get("competition_id")
    if competition_id not in FORMAL_SINGLE_MATCH_IDS or not _valid_hash(value.get("source_fingerprint")):
        raise ValueError("league_lineup_notification_event_invalid")
    return {
        "competition_id": competition_id,
        "event_id": _safe_display_text(value.get("event_id")),
        "home_team": _safe_display_text(value.get("home_team")),
        "away_team": _safe_display_text(value.get("away_team")),
        "kickoff_at_utc": _utc_text(value.get("kickoff_at_utc")),
        "source_fingerprint": value["source_fingerprint"],
    }


def _build_event(
    *,
    event_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    event = {
        "schema_version": 1,
        "event_type": event_type,
        "event_fingerprint": _event_fingerprint(event_type, payload),
        "payload": dict(payload),
    }
    return _validate_event(event)


def _base_lines(payload: Mapping[str, Any]) -> list[str]:
    competition_name = get_competition(payload["competition_id"]).name
    return [
        f"联赛：{competition_name}",
        f"比赛：{payload['home_team']} vs {payload['away_team']}",
        f"开球：{_beijing_text(payload['kickoff_at_utc'])}",
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
    payload = {
        **common,
        "confirmed_at": confirmed_at_utc,
        "previous_decision": before,
        "current_decision": after,
    }
    return _build_event(
        event_type=event_type,
        payload=payload,
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
    extra_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    common = _safe_common(
        competition_id=competition_id,
        event_id=event_id,
        home_team=home_team,
        away_team=away_team,
        kickoff_at_utc=kickoff_at_utc,
        source_fingerprint=source_fingerprint,
    )
    return _build_event(
        event_type=event_type,
        payload={**common, **dict(extra_payload or {})},
    )


def build_missing_lineup_event(**kwargs: Any) -> dict[str, Any]:
    return _build_degraded_event(
        event_type="missing_confirmed",
        **kwargs,
    )


def build_quota_blocked_event(**kwargs: Any) -> dict[str, Any]:
    return _build_degraded_event(
        event_type="quota_blocked",
        **kwargs,
    )


def build_source_failure_event(
    *,
    failure_count: int,
    failure_threshold: int = DEFAULT_SOURCE_FAILURE_THRESHOLD,
    error_details: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    del error_details
    if (
        isinstance(failure_count, bool)
        or not isinstance(failure_count, int)
        or isinstance(failure_threshold, bool)
        or not isinstance(failure_threshold, int)
        or failure_threshold < 1
        or failure_count < failure_threshold
    ):
        raise ValueError("league_lineup_notification_event_invalid")
    return _build_degraded_event(
        event_type="sustained_source_failure",
        extra_payload={"failure_threshold": failure_threshold},
        **kwargs,
    )


def build_source_recovery_event(*, error_details: Any = None, **kwargs: Any) -> dict[str, Any]:
    del error_details
    return _build_degraded_event(
        event_type="source_recovery",
        **kwargs,
    )


def _validate_projected_pick(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("league_lineup_notification_event_invalid")
    expected = (
        {"schema_version", "label"}
        if value.get("label") == "NO_CLEAN_MARKET"
        else {"schema_version", "label", "market", "selection", "line", "p_hit_safe", "odds"}
    )
    if set(value) != expected:
        raise ValueError("league_lineup_notification_event_invalid")
    return _safe_pick(value)


def _validate_event(value: Any) -> dict[str, Any]:
    expected = {"schema_version", "event_type", "event_fingerprint", "payload"}
    if not isinstance(value, Mapping) or set(value) != expected or value.get("schema_version") != 1:
        raise ValueError("league_lineup_notification_event_invalid")
    event_type = value.get("event_type")
    payload = value.get("payload")
    if event_type not in _EVENT_TYPES or not isinstance(payload, Mapping):
        raise ValueError("league_lineup_notification_event_invalid")
    payload_expected = set(_COMMON_PAYLOAD_FIELDS)
    if event_type in {"published_refresh_changed", "published_refresh_unchanged"}:
        payload_expected.update({"confirmed_at", "previous_decision", "current_decision"})
    elif event_type == "sustained_source_failure":
        payload_expected.add("failure_threshold")
    if set(payload) != payload_expected:
        raise ValueError("league_lineup_notification_event_invalid")
    common = _validate_common_payload(payload)
    normalized_payload: dict[str, Any] = dict(common)
    if event_type in {"published_refresh_changed", "published_refresh_unchanged"}:
        confirmed_at = _utc_text(payload.get("confirmed_at"))
        if _aware_datetime(confirmed_at) >= _aware_datetime(common["kickoff_at_utc"]):
            raise ValueError("league_lineup_notification_event_invalid")
        before = _validate_projected_pick(payload.get("previous_decision"))
        after = _validate_projected_pick(payload.get("current_decision"))
        expected_type = (
            "published_refresh_changed"
            if _pick_identity(before) != _pick_identity(after)
            else "published_refresh_unchanged"
        )
        if event_type != expected_type:
            raise ValueError("league_lineup_notification_event_invalid")
        normalized_payload.update(
            {
                "confirmed_at": confirmed_at,
                "previous_decision": before,
                "current_decision": after,
            }
        )
    elif event_type == "sustained_source_failure":
        failure_threshold = payload.get("failure_threshold")
        if (
            isinstance(failure_threshold, bool)
            or not isinstance(failure_threshold, int)
            or failure_threshold < 1
        ):
            raise ValueError("league_lineup_notification_event_invalid")
        normalized_payload["failure_threshold"] = failure_threshold
    fingerprint = value.get("event_fingerprint")
    expected_fingerprint = _event_fingerprint(event_type, normalized_payload)
    if not _valid_hash(fingerprint) or fingerprint != expected_fingerprint:
        raise ValueError("league_lineup_notification_event_invalid")
    return {
        "schema_version": 1,
        "event_type": event_type,
        "event_fingerprint": fingerprint,
        "payload": normalized_payload,
    }


def render_notification_event(value: Mapping[str, Any]) -> dict[str, str]:
    event = _validate_event(value)
    event_type = event["event_type"]
    payload = event["payload"]
    competition_name = get_competition(payload["competition_id"]).name
    lines = _base_lines(payload)
    if event_type in {"published_refresh_changed", "published_refresh_unchanged"}:
        before = payload["previous_decision"]
        after = payload["current_decision"]
        lines.append("已获取正式首发")
        lines.append(f"双方首发已确认：{_beijing_text(payload['confirmed_at'])}")
        if event_type == "published_refresh_changed":
            lines.append(f"本场首选：{_pick_label(before)} → {_pick_label(after)}")
        else:
            lines.append("首发后复核：方向未变")
            lines.append(f"本场首选：{_pick_label(after)}")
        probability = None if after["label"] == "NO_CLEAN_MARKET" else after["p_hit_safe"]
        odds = None if after["label"] == "NO_CLEAN_MARKET" else after["odds"]
        lines.append(f"新安全概率：{format_probability(probability)}")
        lines.append(f"新参考赔率：{'—' if odds is None else f'{odds:.2f}'}")
        summary = f"{competition_name}首发后本场首选已更新"
    else:
        messages = {
            "missing_confirmed": (
                "尚未获取正式首发；未完成首发后复核。"
                "旧推荐及赔率有效性未验证，本提醒不展示旧概率或赔率，"
                "不代表最新推荐结果。"
            ),
            "quota_blocked": "首发已保存，赔率刷新被额度保护阻断，保留原推荐",
            "sustained_source_failure": (
                f"首发数据源连续失败（{payload.get('failure_threshold')} 次），保留原推荐"
            ),
            "source_recovery": "首发数据源已恢复，将继续按规则跟踪确认首发",
        }
        lines.append(messages[event_type])
        summary = f"{competition_name}首发跟踪提醒"
    return {"summary": summary, "content": "\n".join([*lines, "", DISCLAIMER])}


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
            rendered = render_notification_event(pending_event)
            try:
                notification_result = notifier(
                    rendered["content"],
                    summary=rendered["summary"],
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
