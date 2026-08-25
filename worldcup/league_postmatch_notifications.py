"""Durable, safe notification intents for six-league postmatch settlement."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS, get_competition
from worldcup.league_statistics import crossed_evaluation_thresholds


DISCLAIMER = "仅供研究分析，不构成投注建议。"
_EVENT_TYPES = frozenset({"daily_settlement", "evaluation_threshold"})
_TALLY_FIELDS = ("hit", "miss", "push", "no_pick")
_OPTIONAL_COMPETITION_FIELDS = ("newly_settled", "missing_closing", "decided")
_ALLOWED_COMPETITION_FIELDS = frozenset((*_TALLY_FIELDS, *_OPTIONAL_COMPETITION_FIELDS))
_THRESHOLDS = (20, 50, 100)


def _valid_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("league_postmatch_notification_event_invalid")
    return value


def _settlement_date(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("league_postmatch_notification_event_invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("league_postmatch_notification_event_invalid") from exc
    if parsed.isoformat() != value:
        raise ValueError("league_postmatch_notification_event_invalid")
    return value


def _utc_text(value: Any, error: str) -> str:
    if not isinstance(value, str):
        raise ValueError(error)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(error) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(error)
    return parsed.astimezone(timezone.utc).isoformat()


def _event_fingerprint(event_type: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"event_type": event_type, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_competitions(value: Any) -> dict[str, dict[str, int]]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("league_postmatch_notification_event_invalid")
    safe: dict[str, dict[str, int]] = {}
    for competition_id, raw_counts in value.items():
        if competition_id not in FORMAL_SINGLE_MATCH_IDS or not isinstance(raw_counts, Mapping):
            raise ValueError("league_postmatch_notification_event_invalid")
        if not set(raw_counts).issubset(_ALLOWED_COMPETITION_FIELDS) or not set(_TALLY_FIELDS).issubset(raw_counts):
            raise ValueError("league_postmatch_notification_event_invalid")
        safe[competition_id] = {
            field: _nonnegative_int(raw_counts[field])
            for field in sorted(raw_counts)
        }
    return {competition_id: safe[competition_id] for competition_id in sorted(safe)}


def _build_event(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    event = {
        "schema_version": 1,
        "event_type": event_type,
        "event_fingerprint": _event_fingerprint(event_type, payload),
        "payload": dict(payload),
    }
    return _validate_event(event)


def build_daily_settlement_event(
    *,
    settlement_date: str,
    newly_settled: int,
    competitions: Mapping[str, Mapping[str, int]],
    aggregate_fingerprint: str,
) -> dict[str, Any] | None:
    """Build a deterministic daily summary only when formal settlements were added."""
    count = _nonnegative_int(newly_settled)
    if count == 0:
        return None
    if not _valid_hash(aggregate_fingerprint):
        raise ValueError("league_postmatch_notification_event_invalid")
    payload = {
        "settlement_date": _settlement_date(settlement_date),
        "newly_settled": count,
        "competitions": _safe_competitions(competitions),
        "aggregate_fingerprint": aggregate_fingerprint,
    }
    return _build_event("daily_settlement", payload)


def build_threshold_events(
    *,
    previous_decided: int,
    current_decided: int,
    sent_thresholds: Collection[int],
    aggregate_fingerprint: str,
) -> list[dict[str, Any]]:
    """Build deterministic, unsent 20/50/100 offline-review milestone intents."""
    if not _valid_hash(aggregate_fingerprint):
        raise ValueError("league_postmatch_notification_event_invalid")
    checked_sent: set[int] = set()
    for threshold in sent_thresholds:
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold not in _THRESHOLDS:
            raise ValueError("league_postmatch_notification_event_invalid")
        checked_sent.add(threshold)
    return [
        _build_event(
            "evaluation_threshold",
            {"threshold": threshold, "aggregate_fingerprint": aggregate_fingerprint},
        )
        for threshold in crossed_evaluation_thresholds(
            previous_decided,
            current_decided,
            checked_sent,
            thresholds=_THRESHOLDS,
        )
    ]


def _validate_event(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schema_version", "event_type", "event_fingerprint", "payload"}
        or value.get("schema_version") != 1
        or value.get("event_type") not in _EVENT_TYPES
        or not isinstance(value.get("payload"), Mapping)
    ):
        raise ValueError("league_postmatch_notification_event_invalid")
    event_type = value["event_type"]
    raw_payload = value["payload"]
    if event_type == "daily_settlement":
        if set(raw_payload) != {"settlement_date", "newly_settled", "competitions", "aggregate_fingerprint"}:
            raise ValueError("league_postmatch_notification_event_invalid")
        payload: dict[str, Any] = {
            "settlement_date": _settlement_date(raw_payload.get("settlement_date")),
            "newly_settled": _nonnegative_int(raw_payload.get("newly_settled")),
            "competitions": _safe_competitions(raw_payload.get("competitions")),
            "aggregate_fingerprint": raw_payload.get("aggregate_fingerprint"),
        }
        if payload["newly_settled"] == 0 or not _valid_hash(payload["aggregate_fingerprint"]):
            raise ValueError("league_postmatch_notification_event_invalid")
    else:
        if set(raw_payload) != {"threshold", "aggregate_fingerprint"}:
            raise ValueError("league_postmatch_notification_event_invalid")
        threshold = raw_payload.get("threshold")
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold not in _THRESHOLDS:
            raise ValueError("league_postmatch_notification_event_invalid")
        aggregate_fingerprint = raw_payload.get("aggregate_fingerprint")
        if not _valid_hash(aggregate_fingerprint):
            raise ValueError("league_postmatch_notification_event_invalid")
        payload = {"threshold": threshold, "aggregate_fingerprint": aggregate_fingerprint}
    fingerprint = value.get("event_fingerprint")
    if not _valid_hash(fingerprint) or fingerprint != _event_fingerprint(event_type, payload):
        raise ValueError("league_postmatch_notification_event_invalid")
    return {
        "schema_version": 1,
        "event_type": event_type,
        "event_fingerprint": fingerprint,
        "payload": payload,
    }


def render_postmatch_notification(event: Mapping[str, Any]) -> dict[str, str]:
    """Render only review-safe league-level evidence, never provider data or stake signals."""
    checked = _validate_event(event)
    payload = checked["payload"]
    if checked["event_type"] == "daily_settlement":
        lines = [
            f"六联赛赛后结算摘要（{payload['settlement_date']}）",
            f"本轮新增正式结算：{payload['newly_settled']} 场",
        ]
        for competition_id, counts in payload["competitions"].items():
            details = "｜".join(f"{label} {counts[field]}" for field, label in (
                ("hit", "命中"), ("miss", "未命中"), ("push", "走水"), ("no_pick", "无首选"),
            ))
            extra = []
            if "newly_settled" in counts:
                extra.append(f"新增结算 {counts['newly_settled']}")
            if "missing_closing" in counts:
                extra.append(f"缺少 closing {counts['missing_closing']}")
            if "decided" in counts:
                extra.append(f"累计 decided {counts['decided']}")
            suffix = f"｜{'｜'.join(extra)}" if extra else ""
            lines.append(f"{get_competition(competition_id).name}：{details}{suffix}")
        summary = "六联赛赛后结算摘要"
    else:
        threshold = payload["threshold"]
        messages = {
            20: "仅作样本健康检查，不形成模型调整结论。",
            50: "可进行离线候选回测观察，公开策略保持不变。",
            100: "需完成留出集、市场基准与逐联赛样本审计后，才可讨论优化方案。",
        }
        lines = [f"六联赛正式 decided 样本达到 {threshold} 场", messages[threshold]]
        summary = f"六联赛 {threshold} 场样本里程碑"
    return {"summary": summary, "content": "\n".join([*lines, "", DISCLAIMER])}


def _empty_state() -> dict[str, Any]:
    return {"schema_version": 1, "pending": {}, "sent": {}}


def _receipt(event: Mapping[str, Any], sent_at: str) -> dict[str, Any]:
    receipt = {"event_type": event["event_type"], "sent_at": sent_at}
    if event["event_type"] == "evaluation_threshold":
        receipt["threshold"] = event["payload"]["threshold"]
    return receipt


def _validate_state(value: Any) -> dict[str, Any]:
    error = "league_postmatch_notification_state_invalid"
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schema_version", "pending", "sent"}
        or value.get("schema_version") != 1
        or not isinstance(value.get("pending"), Mapping)
        or not isinstance(value.get("sent"), Mapping)
    ):
        raise ValueError(error)
    pending: dict[str, dict[str, Any]] = {}
    for fingerprint, event in value["pending"].items():
        try:
            checked = _validate_event(event)
        except ValueError as exc:
            raise ValueError(error) from exc
        if fingerprint != checked["event_fingerprint"]:
            raise ValueError(error)
        pending[fingerprint] = checked
    sent: dict[str, dict[str, Any]] = {}
    for fingerprint, receipt in value["sent"].items():
        if not _valid_hash(fingerprint) or not isinstance(receipt, Mapping):
            raise ValueError(error)
        event_type = receipt.get("event_type")
        expected = {"event_type", "sent_at"}
        if event_type == "evaluation_threshold":
            expected.add("threshold")
        if event_type not in _EVENT_TYPES or set(receipt) != expected:
            raise ValueError(error)
        try:
            sent_at = _utc_text(receipt.get("sent_at"), error)
        except ValueError as exc:
            raise ValueError(error) from exc
        normalized = {"event_type": event_type, "sent_at": sent_at}
        if event_type == "evaluation_threshold":
            threshold = receipt.get("threshold")
            if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold not in _THRESHOLDS:
                raise ValueError(error)
            normalized["threshold"] = threshold
        sent[fingerprint] = normalized
    if set(pending).intersection(sent):
        raise ValueError(error)
    return {"schema_version": 1, "pending": pending, "sent": sent}


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("league_postmatch_notification_state_invalid") from exc
    return _validate_state(value)


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
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


class LeaguePostmatchNotificationOutbox:
    """File-backed notification intent log; sender output is deliberately never retained."""

    def __init__(self, path: str | Path, notifier: Callable[..., Mapping[str, Any]]) -> None:
        self.path = Path(path)
        self.notifier = notifier
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def deliver(self, event: Mapping[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        checked = _validate_event(event)
        fingerprint = checked["event_fingerprint"]
        result = {"event_fingerprint": fingerprint}
        if dry_run:
            render_postmatch_notification(checked)
            return {"status": "dry_run", **result}

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = _read_state(self.path)
            if fingerprint in state["sent"]:
                return {"status": "already_sent", **result}
            state["pending"].setdefault(fingerprint, checked)
            _atomic_write(self.path, state)
            rendered = render_postmatch_notification(state["pending"][fingerprint])
            try:
                sender_result = self.notifier(rendered["content"], summary=rendered["summary"])
            except Exception:
                return {"status": "failed", **result}
            if not isinstance(sender_result, Mapping) or sender_result.get("status") != "sent":
                return {"status": "failed", **result}
            del state["pending"][fingerprint]
            state["sent"][fingerprint] = _receipt(
                checked,
                datetime.now(timezone.utc).isoformat(),
            )
            _atomic_write(self.path, state)
            return {"status": "sent", **result}

    def retry_pending(self, *, dry_run: bool = False) -> dict[str, Any]:
        state = _read_state(self.path)
        if dry_run:
            return {"status": "dry_run", "pending": len(state["pending"])}
        sent = 0
        failed = 0
        for event in list(state["pending"].values()):
            result = self.deliver(event)
            if result["status"] in {"sent", "already_sent"}:
                sent += 1
            else:
                failed += 1
        return {"status": "complete", "sent": sent, "failed": failed}

    def sent_thresholds(self) -> set[int]:
        """Expose durable threshold receipts for the later runner without rebuilding events."""
        state = _read_state(self.path)
        return {
            receipt["threshold"]
            for receipt in state["sent"].values()
            if receipt["event_type"] == "evaluation_threshold"
        }
