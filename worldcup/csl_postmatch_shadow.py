"""Build local-only CSL postmatch shadow reports from accepted results."""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory, mkstemp
from typing import Any, Callable
from uuid import uuid4

from worldcup import csl_postmatch_runner
from worldcup.club_rating import ClubResult, load_club_results_csv
from worldcup.csl_eval_data import closing_match, load_snapshots
from worldcup.decision_settlement import (
    settle_match_decision,
    summarize_decision_records,
)


REPORT_SCHEMA_VERSION = 1
SETTLEMENT_CONTRACT = "decision_settlement_v1"
DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_SEASON = "2026"
DEFAULT_MIN_SAMPLE = 50
RESEARCH_NOTICE = "仅用于研究分析，不构成投注建议。"
DEFAULT_SHADOW_REPORT = "data/local/diagnostics/csl_postmatch_shadow.json"
DEFAULT_SHADOW_STATE = "data/local/diagnostics/csl_postmatch_shadow_state.json"
DEFAULT_GATE_OUT = "data/local/diagnostics/csl_pending_gate_latest.json"
ACCEPTED_SOURCE_STATUSES = {"updated", "verified"}

SAFE_DECISION_FIELDS = (
    "schema_version",
    "policy_version",
    "label",
    "market",
    "selection",
    "line",
    "odds",
    "p_hit",
    "p_hit_safe",
    "p_no_loss_safe",
    "evidence_score",
    "uncertainty_penalty",
    "selected_option_id",
    "method",
    "computed_at",
    "odds_latest_at",
    "valid_until",
    "reasons",
    "risks",
)


def project_decision(decision: Any) -> dict[str, Any] | None:
    if not isinstance(decision, dict):
        return None
    return {
        key: deepcopy(decision[key])
        for key in SAFE_DECISION_FIELDS
        if key in decision
    }


def _match_id(result: ClubResult) -> str:
    return (
        f"{result.competition_id}:{result.date}:"
        f"{result.home_canonical}:{result.away_canonical}"
    )


def _result_payload(result: ClubResult) -> dict[str, int]:
    return {
        "home_score": result.home_score,
        "away_score": result.away_score,
    }


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _compact_number(value: Any) -> str:
    number = _finite_float(value)
    if number is None:
        return "missing"
    return f"{number:g}"


def _range_bucket(
    value: Any,
    boundaries: tuple[tuple[float, str], ...],
    final_label: str,
) -> str:
    number = _finite_float(value)
    if number is None:
        return "missing"
    for upper, label in boundaries:
        if number < upper:
            return label
    return final_label


def _odds_bucket(value: Any) -> str:
    return _range_bucket(
        value,
        (
            (1.60, "<1.60"),
            (1.80, "1.60-1.79"),
            (2.00, "1.80-1.99"),
            (2.25, "2.00-2.24"),
        ),
        ">=2.25",
    )


def _probability_bucket(value: Any) -> str:
    return _range_bucket(
        value,
        (
            (0.50, "<0.50"),
            (0.55, "0.50-0.54"),
            (0.60, "0.55-0.59"),
            (0.65, "0.60-0.64"),
        ),
        ">=0.65",
    )


def _evidence_bucket(value: Any) -> str:
    return _range_bucket(
        value,
        (
            (0.50, "<0.50"),
            (0.70, "0.50-0.69"),
            (0.85, "0.70-0.84"),
        ),
        ">=0.85",
    )


def _schema_v2_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current: list[dict[str, Any]] = []
    for row in rows:
        decision = row.get("closing_match_decision")
        if not isinstance(decision, dict):
            continue
        try:
            schema_version = int(decision.get("schema_version") or 1)
        except (TypeError, ValueError):
            continue
        if schema_version == 2:
            current.append(row)
    return current


def _group_stats(
    rows: list[dict[str, Any]],
    bucket_for: Callable[[dict[str, Any]], list[str]],
    *,
    min_sample: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for bucket in bucket_for(row):
            grouped[str(bucket)].append(row)

    output: list[dict[str, Any]] = []
    for bucket in sorted(grouped):
        members = grouped[bucket]
        statuses = [str((row.get("settlement") or {}).get("status") or "") for row in members]
        hit = statuses.count("hit")
        miss = statuses.count("miss")
        item = {
            "bucket": bucket,
            "sample": len(members),
            "hit": hit,
            "miss": miss,
            "push": statuses.count("push"),
            "no_pick": statuses.count("no_pick"),
            "invalid": statuses.count("invalid_decision"),
            "hit_rate": hit / (hit + miss) if hit + miss else None,
            "sample_too_small": len(members) < min_sample,
        }
        output.append(item)
    return output


def _decision(row: dict[str, Any]) -> dict[str, Any]:
    decision = row.get("closing_match_decision")
    return decision if isinstance(decision, dict) else {}


def _risk_values(row: dict[str, Any]) -> list[str]:
    risks = _decision(row).get("risks")
    if not isinstance(risks, list):
        return ["none"]
    normalized = sorted({str(value) for value in risks if str(value)})
    return normalized or ["none"]


def _build_breakdowns(
    rows: list[dict[str, Any]],
    *,
    min_sample: int,
) -> dict[str, list[dict[str, Any]]]:
    current = _schema_v2_rows(rows)
    return {
        "market": _group_stats(
            current,
            lambda row: [str(_decision(row).get("market") or "missing")],
            min_sample=min_sample,
        ),
        "selection": _group_stats(
            current,
            lambda row: [str(_decision(row).get("selection") or "missing").lower()],
            min_sample=min_sample,
        ),
        "line": _group_stats(
            current,
            lambda row: [_compact_number(_decision(row).get("line"))],
            min_sample=min_sample,
        ),
        "reference_odds": _group_stats(
            current,
            lambda row: [_odds_bucket(_decision(row).get("odds"))],
            min_sample=min_sample,
        ),
        "p_hit_safe": _group_stats(
            current,
            lambda row: [_probability_bucket(_decision(row).get("p_hit_safe"))],
            min_sample=min_sample,
        ),
        "evidence_score": _group_stats(
            current,
            lambda row: [_evidence_bucket(_decision(row).get("evidence_score"))],
            min_sample=min_sample,
        ),
        "risk_flags": _group_stats(
            current,
            _risk_values,
            min_sample=min_sample,
        ),
        "bookmaker_coverage_risk": _group_stats(
            current,
            lambda row: [
                "thin_market" if "thin_market" in _risk_values(row) else "not_flagged"
            ],
            min_sample=min_sample,
        ),
        "dispersion_risk": _group_stats(
            current,
            lambda row: [
                "severe_dispersion"
                if "severe_dispersion" in _risk_values(row)
                else "not_flagged"
            ],
            min_sample=min_sample,
        ),
    }


def _build_calibration(
    rows: list[dict[str, Any]],
    *,
    min_sample: int,
) -> dict[str, Any]:
    points: list[tuple[float, int]] = []
    grouped: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for row in _schema_v2_rows(rows):
        status = str((row.get("settlement") or {}).get("status") or "")
        if status not in {"hit", "miss"}:
            continue
        probability = _finite_float(_decision(row).get("p_hit_safe"))
        if probability is None or not 0.0 <= probability <= 1.0:
            continue
        actual = 1 if status == "hit" else 0
        point = (probability, actual)
        points.append(point)
        grouped[_probability_bucket(probability)].append(point)

    buckets: list[dict[str, Any]] = []
    for bucket in sorted(grouped):
        members = grouped[bucket]
        hit = sum(actual for _probability, actual in members)
        sample = len(members)
        buckets.append(
            {
                "bucket": bucket,
                "sample": sample,
                "hit": hit,
                "miss": sample - hit,
                "mean_predicted": sum(probability for probability, _actual in members)
                / sample,
                "actual_hit_rate": hit / sample,
            }
        )

    return {
        "sample": len(points),
        "brier_score": (
            sum((probability - actual) ** 2 for probability, actual in points)
            / len(points)
            if points
            else None
        ),
        "sample_too_small": len(points) < min_sample,
        "p_hit_safe": buckets,
    }


def input_fingerprint(
    rows: list[dict[str, Any]],
    competition_id: str,
    season: str,
) -> str:
    payload = {
        "competition_id": competition_id,
        "season": season,
        "settlement_contract": SETTLEMENT_CONTRACT,
        "matches": [
            {
                "match_id": row["match_id"],
                "kickoff_at_utc": row.get("kickoff_at_utc"),
                "result": row["result"],
                "closing_snapshot_at": row.get("closing_snapshot_at"),
                "closing_snapshot_run_id": row.get("closing_snapshot_run_id"),
                "closing_match_decision": row.get("closing_match_decision"),
            }
            for row in sorted(rows, key=lambda item: item["match_id"])
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_shadow_report(
    snapshots: list[dict[str, Any]],
    results: list[ClubResult],
    *,
    generated_at: str,
    competition_id: str = DEFAULT_COMPETITION_ID,
    season: str = DEFAULT_SEASON,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    matched_records: list[dict[str, Any]] = []
    missing_closing = 0
    current_results = sorted(
        (
            result
            for result in results
            if result.competition_id == competition_id and result.season == season
        ),
        key=_match_id,
    )

    for result in current_results:
        selected = closing_match(
            snapshots,
            result.date,
            result.home_canonical,
            result.away_canonical,
            competition_id=competition_id,
        )
        result_payload = _result_payload(result)
        row: dict[str, Any] = {
            "match_id": _match_id(result),
            "competition_id": competition_id,
            "season": season,
            "kickoff_at_utc": None,
            "home_team": result.home_team,
            "away_team": result.away_team,
            "home_canonical": result.home_canonical,
            "away_canonical": result.away_canonical,
            "closing_snapshot_at": None,
            "closing_snapshot_run_id": None,
            "closing_match_decision": None,
            "result": result_payload,
            "settlement": {
                "status": "missing_closing",
                "label": "缺少封盘快照",
                "detail": "",
            },
        }
        if selected is None:
            missing_closing += 1
            rows.append(row)
            continue

        decision = project_decision(selected.entry.get("match_decision"))
        row.update(
            {
                "kickoff_at_utc": selected.entry.get("kickoff_at_utc"),
                "closing_snapshot_at": selected.snapshot_at,
                "closing_snapshot_run_id": selected.snapshot_run_id,
                "closing_match_decision": decision,
                "settlement": settle_match_decision(decision, result_payload),
            }
        )
        rows.append(row)
        matched_records.append(
            {
                "closing_match_decision": decision,
                "result": result_payload,
            }
        )

    canonical = summarize_decision_records(
        matched_records,
        min_sample=min_sample,
        skipped_no_closing=missing_closing,
    )
    warnings = ["sample_too_small"] if canonical["sample"]["sample_too_small"] else []
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "competition_id": competition_id,
        "season": season,
        "generated_at": generated_at,
        "input_fingerprint": input_fingerprint(rows, competition_id, season),
        "settlement_contract": SETTLEMENT_CONTRACT,
        "status": "ok",
        "decision_sample": canonical["sample"],
        "decision_tally": canonical["decision_tally"],
        "decision_coverage": {
            **canonical["coverage"],
            "identity_mismatch_count": 0,
            "result_source_blocked_count": 0,
        },
        "breakdowns": _build_breakdowns(rows, min_sample=min_sample),
        "calibration": _build_calibration(rows, min_sample=min_sample),
        "matches": rows,
        "warnings": warnings,
        "research_notice": RESEARCH_NOTICE,
    }


def _resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _atomic_replace_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _state_base(
    existing: dict[str, Any] | None,
    *,
    competition_id: str,
    season: str,
) -> dict[str, Any]:
    last_success = existing.get("last_success") if isinstance(existing, dict) else None
    return {
        "schema_version": 1,
        "competition_id": competition_id,
        "season": season,
        "last_success": deepcopy(last_success) if isinstance(last_success, dict) else None,
    }


def _attempt(
    attempted_at: str,
    status: str,
    *,
    reason: str | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "attempted_at": attempted_at,
        "status": status,
        "reason": reason,
        "error_type": error_type,
    }


def _write_attempt_state(
    state_path: Path,
    existing: dict[str, Any] | None,
    *,
    competition_id: str,
    season: str,
    attempted_at: str,
    status: str,
    reason: str | None = None,
    error_type: str | None = None,
) -> None:
    state = _state_base(existing, competition_id=competition_id, season=season)
    state["last_attempt"] = _attempt(
        attempted_at,
        status,
        reason=reason,
        error_type=error_type,
    )
    _atomic_replace_bytes(state_path, _json_bytes(state))


def _validate_output_paths(paths: list[Path], inputs: list[Path]) -> None:
    resolved_outputs = [path.resolve(strict=False) for path in paths]
    if len(set(resolved_outputs)) != len(resolved_outputs):
        raise ValueError("shadow_output_path_collision")
    resolved_inputs = {path.resolve(strict=False) for path in inputs}
    if any(path in resolved_inputs for path in resolved_outputs):
        raise ValueError("shadow_output_path_collision")


def _commit_bundle(items: list[tuple[Path, bytes]]) -> None:
    backups: dict[Path, Path | None] = {}
    promoted: list[Path] = []
    try:
        for target, _data in items:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup = target.with_name(f".{target.name}.{uuid4().hex}.bak")
                shutil.copy2(target, backup)
                backups[target] = backup
            else:
                backups[target] = None

        for target, data in items:
            _atomic_replace_bytes(target, data)
            promoted.append(target)
    except BaseException:
        for target in reversed(promoted):
            backup = backups.get(target)
            try:
                if backup is None:
                    target.unlink(missing_ok=True)
                elif backup.exists():
                    os.replace(backup, target)
            except OSError:
                pass
        raise
    finally:
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)


def _safe_summary(report: dict[str, Any], status: str) -> dict[str, Any]:
    sample = report.get("decision_sample") or {}
    coverage = report.get("decision_coverage") or {}
    fingerprint = str(report.get("input_fingerprint") or "")
    return {
        "status": status,
        "competition_id": report.get("competition_id"),
        "results": int(coverage.get("finished_result_count") or 0),
        "closing_available": int(coverage.get("closing_available_count") or 0),
        "decided": int(sample.get("decided") or 0),
        "sample_too_small": bool(sample.get("sample_too_small")),
        "input_fingerprint_prefix": fingerprint[:12],
    }


def _canonical_matches(report: dict[str, Any]) -> set[str]:
    return {
        str(row.get("match_id"))
        for row in report.get("matches") or []
        if isinstance(row, dict) and row.get("closing_snapshot_at") is not None
    }


def _eval_match_ids(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            str(row.get("match_id"))
            for row in csv.DictReader(fh)
            if row.get("match_id")
        }


def run_csl_postmatch_shadow(
    *,
    root: str | Path = ".",
    history: str | Path = csl_postmatch_runner.DEFAULT_HISTORY,
    results: str | Path = csl_postmatch_runner.DEFAULT_RESULTS,
    shadow_report: str | Path = DEFAULT_SHADOW_REPORT,
    state_path: str | Path = DEFAULT_SHADOW_STATE,
    eval_out: str | Path = csl_postmatch_runner.DEFAULT_EVAL_OUT,
    backtest_out: str | Path = csl_postmatch_runner.DEFAULT_REPORT_OUT,
    gate_out: str | Path = DEFAULT_GATE_OUT,
    competition_id: str = DEFAULT_COMPETITION_ID,
    season: str = DEFAULT_SEASON,
    generated_at: str | None = None,
    source_status: str = "verified",
    write: bool = False,
    decision_min_sample: int = DEFAULT_MIN_SAMPLE,
    backtest_min_sample: int = 30,
    warmup_matches: int = 300,
    min_eval_matches: int = 200,
    config: str | Path | None = None,
    postmatch_fn: Callable[..., dict[str, Any]] = csl_postmatch_runner.run_postmatch,
) -> dict[str, Any]:
    if competition_id != DEFAULT_COMPETITION_ID or season != DEFAULT_SEASON:
        raise ValueError("unsupported_csl_shadow_scope")
    root_path = Path(root)
    history_path = _resolve_under_root(root_path, history)
    results_path = _resolve_under_root(root_path, results)
    shadow_path = _resolve_under_root(root_path, shadow_report)
    state = _resolve_under_root(root_path, state_path)
    eval_path = _resolve_under_root(root_path, eval_out)
    backtest_path = _resolve_under_root(root_path, backtest_out)
    gate_path = _resolve_under_root(root_path, gate_out)
    config_path = _resolve_under_root(root_path, config) if config is not None else None
    output_paths = [eval_path, backtest_path, gate_path, shadow_path, state]
    input_paths = [history_path, results_path]
    if config_path is not None:
        input_paths.append(config_path)
    _validate_output_paths(output_paths, input_paths)

    observed = generated_at or _utc_now_iso()
    existing_state = _read_json(state)
    if source_status not in ACCEPTED_SOURCE_STATUSES:
        if write:
            _write_attempt_state(
                state,
                existing_state,
                competition_id=competition_id,
                season=season,
                attempted_at=observed,
                status="blocked",
                reason="result_source_not_accepted",
            )
        return {
            "status": "blocked",
            "reason": "result_source_not_accepted",
            "competition_id": competition_id,
        }

    snapshots = load_snapshots(history_path)
    result_rows = load_club_results_csv(results_path, competition_id)
    report = build_shadow_report(
        snapshots,
        result_rows,
        generated_at=observed,
        competition_id=competition_id,
        season=season,
        min_sample=decision_min_sample,
    )
    if not write:
        return _safe_summary(report, "dry_run_ready")

    report_bytes = _json_bytes(report)
    success = (
        existing_state.get("last_success")
        if isinstance(existing_state, dict)
        and isinstance(existing_state.get("last_success"), dict)
        else None
    )
    existing_report_bytes: bytes | None = None
    try:
        existing_report_bytes = shadow_path.read_bytes()
    except OSError:
        pass
    existing_report = _read_json(shadow_path)
    if (
        isinstance(success, dict)
        and success.get("input_fingerprint") == report["input_fingerprint"]
        and existing_report_bytes is not None
        and success.get("canonical_report_sha256")
        == _bytes_sha256(existing_report_bytes)
        and isinstance(existing_report, dict)
        and existing_report.get("input_fingerprint") == report["input_fingerprint"]
    ):
        _write_attempt_state(
            state,
            existing_state,
            competition_id=competition_id,
            season=season,
            attempted_at=observed,
            status="unchanged",
        )
        return _safe_summary(report, "unchanged")

    phase = "generation"
    try:
        shadow_path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".csl-postmatch-shadow-", dir=shadow_path.parent) as tmp:
            staging = Path(tmp)
            staged_eval = staging / "csl_2026_eval.csv"
            staged_backtest = staging / "csl_2026_report.json"
            staged_gate = staging / "csl_pending_gate_latest.json"
            staged_shadow = staging / "csl_postmatch_shadow.json"
            postmatch_summary = postmatch_fn(
                root=root_path,
                history=history_path,
                results=results_path,
                eval_out=staged_eval,
                report_out=staged_backtest,
                gate_out=staged_gate,
                competition_id=competition_id,
                generated_at=observed,
                min_sample=backtest_min_sample,
                warmup_matches=warmup_matches,
                min_eval_matches=min_eval_matches,
                config=config_path,
            )
            staged_shadow.write_bytes(report_bytes)

            phase = "validation"
            for staged_path in (
                staged_eval,
                staged_backtest,
                staged_gate,
                staged_shadow,
            ):
                if not staged_path.is_file() or staged_path.stat().st_size <= 0:
                    raise ValueError("shadow_staged_artifact_missing")
            json.loads(staged_backtest.read_text(encoding="utf-8"))
            json.loads(staged_gate.read_text(encoding="utf-8"))
            staged_report = json.loads(staged_shadow.read_text(encoding="utf-8"))
            if staged_report.get("input_fingerprint") != report["input_fingerprint"]:
                raise ValueError("shadow_fingerprint_mismatch")
            if int(postmatch_summary.get("joined") or 0) != int(
                report["decision_coverage"]["closing_available_count"]
            ):
                raise ValueError("shadow_joined_count_mismatch")
            if _eval_match_ids(staged_eval) != _canonical_matches(report):
                raise ValueError("shadow_eval_identity_mismatch")
            if any(
                row.get("season") != season
                for row in report.get("matches") or []
                if isinstance(row, dict)
            ):
                raise ValueError("shadow_season_mismatch")

            state_payload = {
                "schema_version": 1,
                "competition_id": competition_id,
                "season": season,
                "last_success": {
                    "input_fingerprint": report["input_fingerprint"],
                    "succeeded_at": observed,
                    "decision_count": int(
                        report["decision_sample"].get("decision_count") or 0
                    ),
                    "decided": int(report["decision_sample"].get("decided") or 0),
                    "canonical_report_sha256": _bytes_sha256(report_bytes),
                },
                "last_attempt": _attempt(observed, "stored"),
            }
            phase = "commit"
            _commit_bundle(
                [
                    (eval_path, staged_eval.read_bytes()),
                    (backtest_path, staged_backtest.read_bytes()),
                    (gate_path, staged_gate.read_bytes()),
                    (shadow_path, report_bytes),
                    (state, _json_bytes(state_payload)),
                ]
            )
    except Exception as exc:
        reason = {
            "generation": "shadow_generation_failed",
            "validation": "shadow_validation_failed",
            "commit": "shadow_commit_failed",
        }[phase]
        try:
            _write_attempt_state(
                state,
                existing_state,
                competition_id=competition_id,
                season=season,
                attempted_at=observed,
                status="error",
                reason=reason,
                error_type=type(exc).__name__,
            )
        except Exception:
            pass
        raise

    return _safe_summary(report, "stored")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a local-only CSL postmatch shadow report. Defaults to dry-run.",
        allow_abbrev=False,
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--history", default=csl_postmatch_runner.DEFAULT_HISTORY)
    parser.add_argument("--results", default=csl_postmatch_runner.DEFAULT_RESULTS)
    parser.add_argument("--shadow-report", default=DEFAULT_SHADOW_REPORT)
    parser.add_argument("--state-path", default=DEFAULT_SHADOW_STATE)
    parser.add_argument("--eval-out", default=csl_postmatch_runner.DEFAULT_EVAL_OUT)
    parser.add_argument("--backtest-out", default=csl_postmatch_runner.DEFAULT_REPORT_OUT)
    parser.add_argument("--gate-out", default=DEFAULT_GATE_OUT)
    parser.add_argument(
        "--competition",
        "--competition-id",
        dest="competition_id",
        default=DEFAULT_COMPETITION_ID,
    )
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--source-status", default="verified")
    parser.add_argument("--decision-min-sample", type=int, default=DEFAULT_MIN_SAMPLE)
    parser.add_argument("--backtest-min-sample", type=int, default=30)
    parser.add_argument("--warmup-matches", type=int, default=300)
    parser.add_argument("--min-eval-matches", type=int, default=200)
    parser.add_argument("--config", default=None)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = run_csl_postmatch_shadow(
            root=args.root,
            history=args.history,
            results=args.results,
            shadow_report=args.shadow_report,
            state_path=args.state_path,
            eval_out=args.eval_out,
            backtest_out=args.backtest_out,
            gate_out=args.gate_out,
            competition_id=args.competition_id,
            season=args.season,
            generated_at=args.generated_at,
            source_status=args.source_status,
            write=args.write,
            decision_min_sample=args.decision_min_sample,
            backtest_min_sample=args.backtest_min_sample,
            warmup_matches=args.warmup_matches,
            min_eval_matches=args.min_eval_matches,
            config=args.config,
        )
    except Exception as exc:
        result = {
            "status": "error",
            "reason": "csl_postmatch_shadow_failed",
            "error_type": type(exc).__name__,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"dry_run_ready", "stored", "unchanged"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
