"""Refresh confirmed 90-minute World Cup results and publish a full snapshot.

The runner is deliberately separate from the odds refresh scheduler.  Its
default mode is a zero-side-effect dry run; ``live=True`` is required before it
loads a secret, fetches openfootball, writes local files, or publishes.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from copy import deepcopy
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable
from uuid import uuid4

from worldcup.collectors.models import MatchResult
from worldcup.collectors.openfootball import parse_openfootball_results
from worldcup.finished_record import build_finished_block
from worldcup.publish import DEFAULT_ENDPOINT, publish_snapshot
from worldcup.publish_outbox import (
    attempt_publish,
    clear_pending_publish,
    load_pending_publish,
    pending_publish_path,
)
from worldcup.refresh_runner import _load_env
from worldcup.results_capture import _load_rows, _write_rows, upsert_results
from worldcup.scheduler import make_run_id
from worldcup.sources.openfootball import TextFetchResult, fetch_openfootball_2026


DEFAULT_BASE_SNAPSHOT_PATH = Path("data/cache/analysis_snapshot.json")
DEFAULT_POSTMATCH_SNAPSHOT_PATH = Path("data/cache/wc2026_postmatch_snapshot.json")
DEFAULT_STATE_PATH = Path("data/cache/wc2026_postmatch_state.json")
DEFAULT_OPENFOOTBALL_CACHE_PATH = Path("data/cache/openfootball_2026.json")
DEFAULT_HISTORY_DIR = Path("data/local/history")
DEFAULT_RESULTS_PATH = Path("data/local/results/wc2026_results.csv")
DEFAULT_FINISHED_STORE_PATH = Path("data/local/finished_record_store.json")
DEFAULT_SECRET_ENV = "INGEST_HMAC_SECRET"
WORLD_CUP_COMPETITION_ID = "fifa_world_cup_2026"


FetchFn = Callable[[], TextFetchResult]
PublishFn = Callable[..., dict[str, Any]]
EnvLoader = Callable[[str | Path], dict[str, str]]
ResultKey = tuple[str, str, str]


class _PostmatchAlreadyRunning(RuntimeError):
    pass


class _PostmatchLockUnavailable(RuntimeError):
    def __init__(self, error_type: str):
        super().__init__(error_type)
        self.error_type = error_type


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _json_file_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")


def _prepared_snapshot_path(output_path: Path, snapshot: dict[str, Any]) -> Path:
    fingerprint = hashlib.sha256(_json_file_bytes(snapshot)).hexdigest()[:20]
    return output_path.with_name(
        f".{output_path.stem}.{fingerprint}.prepared.json"
    )


def _cleanup_orphan_prepared_snapshots(output_path: Path) -> None:
    pattern = f".{output_path.stem}.*.prepared.json"
    for path in output_path.parent.glob(pattern):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _write_bytes_atomic(path, _json_file_bytes(payload))


def _write_rows_atomic(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        _write_rows(rows, temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _is_complete_world_cup_snapshot(snapshot: dict[str, Any]) -> bool:
    matches = snapshot.get("matches")
    if not isinstance(matches, list) or not matches:
        return False
    has_explicit_world_cup_id = False
    competition = snapshot.get("competition")
    if isinstance(competition, dict):
        competition_id = str(competition.get("id") or "").strip()
        if competition_id and competition_id != WORLD_CUP_COMPETITION_ID:
            return False
        has_explicit_world_cup_id = competition_id == WORLD_CUP_COMPETITION_ID
    for match in matches:
        if not isinstance(match, dict):
            return False
        match_competition = match.get("competition")
        if not isinstance(match_competition, dict):
            continue
        competition_id = str(match_competition.get("id") or "").strip()
        if competition_id and competition_id != WORLD_CUP_COMPETITION_ID:
            return False
        if competition_id == WORLD_CUP_COMPETITION_ID:
            has_explicit_world_cup_id = True
    return has_explicit_world_cup_id


def _paths_collide(
    *,
    base_path: Path,
    output_path: Path,
    state_path: Path,
    cache_path: Path,
    results_path: Path,
    store_path: Path,
) -> bool:
    base = base_path.resolve(strict=False)
    resources = [output_path, state_path, cache_path, results_path, store_path]
    write_paths = [
        *(path.resolve(strict=False) for path in resources),
        pending_publish_path(output_path).resolve(strict=False),
        *(_postmatch_lock_path(path).resolve(strict=False) for path in resources),
    ]
    return base in write_paths or len(set(write_paths)) != len(write_paths)


def _postmatch_lock_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_name(f".{snapshot_path.name}.postmatch.lock")


@contextmanager
def _exclusive_postmatch_lock(snapshot_path: Path):
    lock_path = _postmatch_lock_path(snapshot_path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = lock_path.open("a+", encoding="utf-8")
    except OSError as exc:
        raise _PostmatchLockUnavailable(type(exc).__name__) from exc
    with lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise _PostmatchAlreadyRunning from exc
        except OSError as exc:
            raise _PostmatchLockUnavailable(type(exc).__name__) from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


@contextmanager
def _exclusive_postmatch_locks(resource_paths: list[Path]):
    ordered = sorted(
        {path.resolve(strict=False) for path in resource_paths},
        key=str,
    )
    with ExitStack() as stack:
        for path in ordered:
            stack.enter_context(_exclusive_postmatch_lock(path))
        yield


def _result_key_from_result(result: MatchResult) -> ResultKey:
    return (
        result.kickoff_at_utc.date().isoformat(),
        str(result.home_canonical or ""),
        str(result.away_canonical or ""),
    )


def _result_key_from_row(row: dict[str, Any]) -> ResultKey:
    return (
        str(row.get("kickoff_at_utc") or "")[:10],
        str(row.get("home_canonical") or ""),
        str(row.get("away_canonical") or ""),
    )


def _result_key_from_finished(record: dict[str, Any]) -> ResultKey:
    return (
        str(record.get("kickoff_at_utc") or "")[:10],
        str(record.get("home_canonical") or ""),
        str(record.get("away_canonical") or ""),
    )


def _finished_records(block: Any) -> dict[ResultKey, dict[str, Any]]:
    if not isinstance(block, dict):
        return {}
    records: dict[ResultKey, dict[str, Any]] = {}
    for record in block.get("matches") or []:
        if not isinstance(record, dict):
            raise ValueError("invalid_finished_record")
        key = _result_key_from_finished(record)
        if not all(key):
            raise ValueError("invalid_finished_identity")
        if key in records:
            raise ValueError("duplicate_finished_identity")
        records[key] = record
    return records


def _result_scores(results: list[MatchResult]) -> dict[ResultKey, tuple[int, int]]:
    scores: dict[ResultKey, tuple[int, int]] = {}
    for result in results:
        key = _result_key_from_result(result)
        if not all(key):
            raise ValueError("invalid_openfootball_result_identity")
        if key in scores:
            raise ValueError("duplicate_openfootball_result")
        scores[key] = (int(result.home_score), int(result.away_score))
    return scores


def _row_scores(rows: list[dict]) -> dict[ResultKey, tuple[int, int]]:
    scores: dict[ResultKey, tuple[int, int]] = {}
    for row in rows:
        key = _result_key_from_row(row)
        try:
            home = int(row.get("home_score"))
            away = int(row.get("away_score"))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_results_csv") from exc
        if not all(key) or home < 0 or away < 0:
            raise ValueError("invalid_results_csv")
        if key in scores:
            raise ValueError("duplicate_results_identity")
        scores[key] = (home, away)
    return scores


def _finished_scores(
    records: dict[ResultKey, dict[str, Any]],
) -> dict[ResultKey, tuple[int, int]]:
    scores: dict[ResultKey, tuple[int, int]] = {}
    for key, record in records.items():
        score = _finished_score(record)
        if score is None:
            raise ValueError("invalid_finished_score")
        scores[key] = score
    return scores


def _finished_score(record: dict[str, Any]) -> tuple[int, int] | None:
    result = record.get("result")
    if not isinstance(result, dict):
        return None
    home = result.get("home_score")
    away = result.get("away_score")
    if (
        type(home) is not int
        or home < 0
        or type(away) is not int
        or away < 0
    ):
        return None
    return home, away


def _parse_fetch_result(response: object) -> tuple[str, dict[str, Any], list[MatchResult]]:
    status = getattr(response, "status", None)
    if not isinstance(status, int) or not 200 <= status < 300:
        raise ValueError("openfootball_http_error")
    text = getattr(response, "text", None)
    if not isinstance(text, str):
        raise ValueError("invalid_openfootball_response")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_openfootball_json") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("matches"), list):
        raise ValueError("invalid_openfootball_schema")
    if not raw["matches"]:
        raise ValueError("empty_openfootball_matches")
    try:
        results = parse_openfootball_results(raw, require_score_ft=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid_openfootball_schema") from exc
    return text, raw, results


def _source_fixture_regressed(cache_path: Path, raw: dict[str, Any]) -> bool:
    cached = _load_json_object(cache_path)
    if cached is None or not isinstance(cached.get("matches"), list):
        return False
    old_matches = cached["matches"]
    new_matches = raw.get("matches") or []
    if len(new_matches) < len(old_matches):
        return True
    old_numbers = {
        str(match.get("num"))
        for match in old_matches
        if isinstance(match, dict) and match.get("num") is not None
    }
    new_numbers = {
        str(match.get("num"))
        for match in new_matches
        if isinstance(match, dict) and match.get("num") is not None
    }
    return bool(old_numbers and not old_numbers.issubset(new_numbers))


def _source_summary(
    raw: dict[str, Any],
    results: list[MatchResult],
    *,
    added: int,
    updated: int,
    missing_closing_count: int = 0,
) -> dict[str, Any]:
    return {
        "source": "openfootball",
        "score_field": "score.ft",
        "period": "90min",
        "source_match_count": len(raw.get("matches") or []),
        "confirmed_result_count": len(results),
        "added": added,
        "updated": updated,
        "missing_closing_count": int(missing_closing_count),
        "partial_publish": bool(missing_closing_count),
    }


def build_postmatch_snapshot(
    base_snapshot: dict[str, Any],
    finished: dict[str, Any],
    *,
    observed_at: str,
    source_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build a complete competition snapshot without mutating the odds snapshot."""

    snapshot = deepcopy(base_snapshot)
    parent_run = base_snapshot.get("run") if isinstance(base_snapshot.get("run"), dict) else {}
    run = deepcopy(parent_run)
    run.update(
        {
            "schema_version": 1,
            "run_id": make_run_id(observed_at, "postmatch"),
            "mode": "postmatch_results",
            "observed_at": observed_at,
            "parent_run_id": parent_run.get("run_id"),
            "parent_snapshot_at": base_snapshot.get("snapshot_at"),
            "postmatch": deepcopy(source_summary),
        }
    )
    snapshot["snapshot_at"] = observed_at
    snapshot["run"] = run
    snapshot["finished"] = deepcopy(finished)
    return snapshot


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    state = _load_json_object(path)
    if state is None:
        return {"status": "invalid"}
    if state.get("schema_version") != 1 or state.get("status") != "published":
        return {"status": "invalid"}
    if not isinstance(state.get("finished_sha256"), str) or not isinstance(
        state.get("snapshot_sha256"), str
    ):
        return {"status": "invalid"}
    return state


def _published_state(snapshot: dict[str, Any], observed_at: str) -> dict[str, Any]:
    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    return {
        "schema_version": 1,
        "status": "published",
        "run_id": str(run.get("run_id") or ""),
        "parent_run_id": str(run.get("parent_run_id") or ""),
        "snapshot_sha256": _sha256_json(snapshot),
        "finished_sha256": _sha256_json(snapshot.get("finished") or {}),
        "published_at": observed_at,
    }


def _resolve_secret(
    secret: str | None,
    *,
    env_path: str | Path,
    load_env_fn: EnvLoader,
) -> str | None:
    if secret:
        return secret
    return load_env_fn(env_path).get(DEFAULT_SECRET_ENV)


def _retry_pending(
    *,
    snapshot_path: Path,
    state_path: Path,
    pending: dict[str, Any],
    endpoint: str,
    secret: str,
    observed_at: str,
    publish_fn: PublishFn,
) -> dict[str, Any]:
    if pending.get("status") != "pending":
        return {
            "status": "publish_pending_invalid",
            "reason": pending.get("reason"),
        }
    attempted = attempt_publish(
        snapshot_path=snapshot_path,
        endpoint=endpoint,
        secret=secret,
        timestamp=observed_at,
        publish_fn=publish_fn,
        stage=False,
        clear_on_success=False,
    )
    if attempted["status"] == "published":
        actual_pending = attempted.get("pending")
        if not isinstance(actual_pending, dict) or actual_pending.get("status") != "pending":
            return {
                "status": "publish_pending",
                "reason": "published_pending_unavailable",
                "publish": attempted.get("publish"),
                "pending": actual_pending,
            }
        prepared_path = Path(
            str(actual_pending.get("snapshot_path") or snapshot_path)
        )
        snapshot = _load_json_object(prepared_path)
        if snapshot is None:
            return {"status": "error", "reason": "published_snapshot_unreadable"}
        try:
            _write_json_atomic(snapshot_path, snapshot)
        except OSError as exc:
            return {
                "status": "publish_pending",
                "reason": "snapshot_write_failed",
                "error_type": type(exc).__name__,
                "publish": attempted.get("publish"),
                "pending": attempted.get("pending"),
            }
        try:
            _write_json_atomic(state_path, _published_state(snapshot, observed_at))
        except OSError as exc:
            return {
                "status": "publish_pending",
                "reason": "state_write_failed",
                "error_type": type(exc).__name__,
                "publish": attempted.get("publish"),
                "pending": attempted.get("pending"),
            }
        try:
            clear_pending_publish(snapshot_path)
        except OSError as exc:
            return {
                "status": "publish_pending",
                "reason": "pending_clear_failed",
                "error_type": type(exc).__name__,
                "publish": attempted.get("publish"),
                "pending": attempted.get("pending"),
            }
        if prepared_path.resolve(strict=False) != snapshot_path.resolve(strict=False):
            try:
                prepared_path.unlink(missing_ok=True)
            except OSError:
                pass
        return {
            "status": "republished",
            "publish": attempted.get("publish"),
        }
    if attempted["status"] == "pending_invalid":
        return {
            "status": "publish_pending_invalid",
            "reason": attempted.get("reason"),
            "pending": attempted.get("pending"),
        }
    return {
        "status": attempted["status"],
        "publish": attempted.get("publish"),
        "pending": attempted.get("pending"),
    }


def _stage_finished_block(
    *,
    rows: list[dict],
    history_dir: Path,
    store_path: Path,
) -> tuple[dict[str, Any], bytes]:
    stage_parent = store_path.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".postmatch-stage-", dir=stage_parent) as temporary:
        root = Path(temporary)
        staged_results = root / "results.csv"
        staged_store = root / "finished_store.json"
        _write_rows(rows, staged_results)
        if store_path.exists():
            staged_store.write_bytes(store_path.read_bytes())
        finished = build_finished_block(history_dir, staged_results, staged_store)
        store_bytes = staged_store.read_bytes() if staged_store.exists() else b"{}"
    return finished, store_bytes


def _run_postmatch_publish_unlocked(
    *,
    live: bool = False,
    now: str | None = None,
    base_snapshot_path: str | Path = DEFAULT_BASE_SNAPSHOT_PATH,
    postmatch_snapshot_path: str | Path = DEFAULT_POSTMATCH_SNAPSHOT_PATH,
    state_path: str | Path = DEFAULT_STATE_PATH,
    openfootball_cache_path: str | Path = DEFAULT_OPENFOOTBALL_CACHE_PATH,
    history_dir: str | Path = DEFAULT_HISTORY_DIR,
    results_path: str | Path = DEFAULT_RESULTS_PATH,
    finished_store_path: str | Path = DEFAULT_FINISHED_STORE_PATH,
    endpoint: str = DEFAULT_ENDPOINT,
    env_path: str | Path = ".env",
    secret: str | None = None,
    fetch_fn: FetchFn = fetch_openfootball_2026,
    publish_fn: PublishFn = publish_snapshot,
    load_env_fn: EnvLoader = _load_env,
) -> dict[str, Any]:
    observed_at = now or _now_utc_iso()
    paths = {
        "base_snapshot": str(base_snapshot_path),
        "postmatch_snapshot": str(postmatch_snapshot_path),
        "state": str(state_path),
        "openfootball_cache": str(openfootball_cache_path),
        "results": str(results_path),
        "finished_store": str(finished_store_path),
    }
    if not live:
        return {
            "status": "dry_run",
            "mode": "postmatch_results",
            "score_field": "score.ft",
            "period": "90min",
            "paths": paths,
        }

    base_path = Path(base_snapshot_path)
    output_path = Path(postmatch_snapshot_path)
    publish_state_path = Path(state_path)
    cache_path = Path(openfootball_cache_path)
    results_file = Path(results_path)
    store_path = Path(finished_store_path)
    history = Path(history_dir)

    if not endpoint.strip() or endpoint.strip() == DEFAULT_ENDPOINT:
        return {"status": "blocked", "reason": "invalid_ingest_endpoint"}
    if _paths_collide(
        base_path=base_path,
        output_path=output_path,
        state_path=publish_state_path,
        cache_path=cache_path,
        results_path=results_file,
        store_path=store_path,
    ):
        return {"status": "blocked", "reason": "postmatch_path_collision"}

    resolved_secret = _resolve_secret(
        secret,
        env_path=env_path,
        load_env_fn=load_env_fn,
    )
    if not resolved_secret:
        return {"status": "blocked", "reason": "missing_ingest_hmac_secret"}
    from worldcup.secrets import validate_hmac_secret
    try:
        validate_hmac_secret(resolved_secret)
    except ValueError:
        return {"status": "blocked", "reason": "weak_ingest_hmac_secret"}

    pending = load_pending_publish(output_path)
    if pending is not None:
        return _retry_pending(
            snapshot_path=output_path,
            state_path=publish_state_path,
            pending=pending,
            endpoint=endpoint,
            secret=resolved_secret,
            observed_at=observed_at,
            publish_fn=publish_fn,
        )

    state = _load_state(publish_state_path)
    if state is not None and state.get("status") == "invalid":
        return {"status": "blocked", "reason": "invalid_postmatch_state"}
    if state is not None:
        published_snapshot = _load_json_object(output_path)
        if (
            published_snapshot is None
            or _sha256_json(published_snapshot) != state.get("snapshot_sha256")
        ):
            return {
                "status": "blocked",
                "reason": "postmatch_state_snapshot_mismatch",
            }
    _cleanup_orphan_prepared_snapshots(output_path)

    base_snapshot = _load_json_object(base_path)
    if base_snapshot is None:
        return {"status": "error", "reason": "base_snapshot_unavailable"}
    if not _is_complete_world_cup_snapshot(base_snapshot):
        return {"status": "blocked", "reason": "base_snapshot_not_world_cup"}

    try:
        fetched_text, raw, strict_results = _parse_fetch_result(fetch_fn())
    except ValueError as exc:
        return {"status": "error", "reason": str(exc)}
    except (OSError, TimeoutError, ConnectionError) as exc:
        return {
            "status": "error",
            "reason": "openfootball_fetch_failed",
            "error_type": type(exc).__name__,
        }

    if cache_path.exists() and _load_json_object(cache_path) is None:
        return {"status": "blocked", "reason": "invalid_openfootball_cache"}
    if _source_fixture_regressed(cache_path, raw):
        return {"status": "blocked", "reason": "openfootball_fixture_regression"}

    previous_snapshot = _load_json_object(output_path)
    if output_path.exists() and previous_snapshot is None:
        return {"status": "blocked", "reason": "invalid_postmatch_snapshot"}
    if previous_snapshot is not None and not _is_complete_world_cup_snapshot(previous_snapshot):
        return {"status": "blocked", "reason": "postmatch_snapshot_not_world_cup"}

    try:
        existing_rows = _load_rows(results_file)
        existing_scores = _row_scores(existing_rows)
    except ValueError as exc:
        return {"status": "blocked", "reason": str(exc)}
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "status": "blocked",
            "reason": "invalid_results_csv",
            "error_type": type(exc).__name__,
        }

    try:
        source_scores = _result_scores(strict_results)
        base_records = _finished_records(base_snapshot.get("finished") or {})
        previous_records = (
            _finished_records(previous_snapshot.get("finished") or {})
            if previous_snapshot is not None
            else {}
        )
        base_scores = _finished_scores(base_records)
        previous_scores = _finished_scores(previous_records)
    except ValueError as exc:
        return {"status": "blocked", "reason": str(exc)}

    reference_conflicts = [
        key
        for key, score in base_scores.items()
        if key in previous_scores and previous_scores[key] != score
    ]
    if reference_conflicts:
        return {
            "status": "blocked",
            "reason": "finished_reference_conflict",
            "mismatches": len(reference_conflicts),
        }

    reference_records = {**base_records, **previous_records}
    reference_scores = {**base_scores, **previous_scores}

    local_score_conflicts = [
        key
        for key, score in existing_scores.items()
        if key in reference_scores and reference_scores[key] != score
    ]
    if local_score_conflicts:
        return {
            "status": "blocked",
            "reason": "local_result_finished_mismatch",
            "mismatches": len(local_score_conflicts),
        }

    baseline_scores = {**reference_scores, **existing_scores}
    missing_source_results = sorted(set(baseline_scores) - set(source_scores))
    if missing_source_results:
        return {
            "status": "blocked",
            "reason": "openfootball_result_regression",
            "missing": len(missing_source_results),
        }
    revised_source_results = [
        key
        for key, score in baseline_scores.items()
        if source_scores.get(key) != score
    ]
    if revised_source_results:
        return {
            "status": "blocked",
            "reason": "score_revision_manual_review_required",
            "updated": len(revised_source_results),
        }

    rows, added, updated = upsert_results(strict_results, existing_rows, observed_at)
    if updated:
        return {
            "status": "blocked",
            "reason": "score_revision_manual_review_required",
            "updated": updated,
        }

    finished, staged_store_bytes = _stage_finished_block(
        rows=rows,
        history_dir=history,
        store_path=store_path,
    )
    try:
        staged_records = _finished_records(finished)
        staged_scores = _finished_scores(staged_records)
    except ValueError as exc:
        return {"status": "blocked", "reason": str(exc)}

    missing_reference = sorted(set(reference_records) - set(staged_records))
    if missing_reference:
        return {
            "status": "blocked",
            "reason": "finished_history_regression",
            "missing": len(missing_reference),
        }

    reference_score_mismatches = [
        key
        for key, score in reference_scores.items()
        if staged_scores.get(key) != score
    ]
    if reference_score_mismatches:
        return {
            "status": "blocked",
            "reason": "finished_score_mismatch_manual_review_required",
            "mismatches": len(reference_score_mismatches),
        }

    score_mismatches = [
        key
        for key, expected in source_scores.items()
        if key in staged_scores and staged_scores[key] != expected
    ]
    if score_mismatches:
        return {
            "status": "blocked",
            "reason": "finished_score_mismatch_manual_review_required",
            "mismatches": len(score_mismatches),
        }

    new_staged_keys = set(staged_records) - set(reference_records)
    unverified_staged = sorted(new_staged_keys - set(source_scores))
    if unverified_staged:
        return {
            "status": "blocked",
            "reason": "unverified_finished_score",
            "unverified": len(unverified_staged),
        }

    unpublished_keys = set(source_scores) - set(reference_records)
    missing_closing = sorted(unpublished_keys - set(staged_records))

    # A valid source/result capture is useful local evidence even when closing
    # history is missing.  Only the public snapshot/publish step is blocked.
    _write_bytes_atomic(cache_path, fetched_text.encode("utf-8"))
    _write_rows_atomic(rows, results_file)
    _write_bytes_atomic(store_path, staged_store_bytes)

    source = _source_summary(
        raw,
        strict_results,
        added=added,
        updated=updated,
        missing_closing_count=len(missing_closing),
    )
    candidate = build_postmatch_snapshot(
        base_snapshot,
        finished,
        observed_at=observed_at,
        source_summary=source,
    )
    finished_sha256 = _sha256_json(candidate.get("finished") or {})
    base_finished_sha256 = _sha256_json(base_snapshot.get("finished") or {})
    parent_run_id = str((base_snapshot.get("run") or {}).get("run_id") or "")
    if (
        state is not None
        and state.get("finished_sha256") == finished_sha256
        and (
            state.get("parent_run_id") == parent_run_id
            or finished_sha256 == base_finished_sha256
        )
    ) or (
        state is None
        and not output_path.exists()
        and finished_sha256 == base_finished_sha256
    ):
        return {
            "status": "unchanged",
            "source": source,
            "finished_sha256": finished_sha256,
        }

    prepared_path = _prepared_snapshot_path(output_path, candidate)
    _write_json_atomic(prepared_path, candidate)
    try:
        attempted = attempt_publish(
            snapshot_path=prepared_path,
            state_path=pending_publish_path(output_path),
            owner_snapshot_path=output_path,
            endpoint=endpoint,
            secret=resolved_secret,
            timestamp=observed_at,
            publish_fn=publish_fn,
            stage=True,
            clear_on_success=False,
        )
    except OSError as exc:
        return {
            "status": "error",
            "reason": "pending_stage_failed",
            "error_type": type(exc).__name__,
            "source": source,
        }
    if attempted["status"] != "published":
        return {
            "status": attempted["status"],
            "source": source,
            "publish": attempted.get("publish"),
            "pending": attempted.get("pending"),
        }

    try:
        _write_json_atomic(output_path, candidate)
    except OSError as exc:
        return {
            "status": "publish_pending",
            "reason": "snapshot_write_failed",
            "error_type": type(exc).__name__,
            "source": source,
            "publish": attempted.get("publish"),
            "pending": attempted.get("pending"),
        }
    try:
        _write_json_atomic(publish_state_path, _published_state(candidate, observed_at))
    except OSError as exc:
        return {
            "status": "publish_pending",
            "reason": "state_write_failed",
            "error_type": type(exc).__name__,
            "source": source,
            "publish": attempted.get("publish"),
            "pending": attempted.get("pending"),
        }
    try:
        clear_pending_publish(output_path)
    except OSError as exc:
        return {
            "status": "publish_pending",
            "reason": "pending_clear_failed",
            "error_type": type(exc).__name__,
            "source": source,
            "publish": attempted.get("publish"),
            "pending": attempted.get("pending"),
        }
    try:
        prepared_path.unlink(missing_ok=True)
    except OSError:
        pass
    return {
        "status": "published",
        "source": source,
        "snapshot_path": str(output_path),
        "publish": attempted.get("publish"),
    }


def run_postmatch_publish(
    *,
    live: bool = False,
    now: str | None = None,
    base_snapshot_path: str | Path = DEFAULT_BASE_SNAPSHOT_PATH,
    postmatch_snapshot_path: str | Path = DEFAULT_POSTMATCH_SNAPSHOT_PATH,
    state_path: str | Path = DEFAULT_STATE_PATH,
    openfootball_cache_path: str | Path = DEFAULT_OPENFOOTBALL_CACHE_PATH,
    history_dir: str | Path = DEFAULT_HISTORY_DIR,
    results_path: str | Path = DEFAULT_RESULTS_PATH,
    finished_store_path: str | Path = DEFAULT_FINISHED_STORE_PATH,
    endpoint: str = DEFAULT_ENDPOINT,
    env_path: str | Path = ".env",
    secret: str | None = None,
    fetch_fn: FetchFn = fetch_openfootball_2026,
    publish_fn: PublishFn = publish_snapshot,
    load_env_fn: EnvLoader = _load_env,
) -> dict[str, Any]:
    arguments = {
        "live": live,
        "now": now,
        "base_snapshot_path": base_snapshot_path,
        "postmatch_snapshot_path": postmatch_snapshot_path,
        "state_path": state_path,
        "openfootball_cache_path": openfootball_cache_path,
        "history_dir": history_dir,
        "results_path": results_path,
        "finished_store_path": finished_store_path,
        "endpoint": endpoint,
        "env_path": env_path,
        "secret": secret,
        "fetch_fn": fetch_fn,
        "publish_fn": publish_fn,
        "load_env_fn": load_env_fn,
    }
    if not live:
        return _run_postmatch_publish_unlocked(**arguments)

    output_path = Path(postmatch_snapshot_path)
    if not endpoint.strip() or endpoint.strip() == DEFAULT_ENDPOINT:
        return {"status": "blocked", "reason": "invalid_ingest_endpoint"}
    if _paths_collide(
        base_path=Path(base_snapshot_path),
        output_path=output_path,
        state_path=Path(state_path),
        cache_path=Path(openfootball_cache_path),
        results_path=Path(results_path),
        store_path=Path(finished_store_path),
    ):
        return {"status": "blocked", "reason": "postmatch_path_collision"}

    observed_at = now or _now_utc_iso()
    try:
        make_run_id(observed_at, "postmatch")
    except (TypeError, ValueError):
        return {"status": "error", "reason": "invalid_observed_at"}
    arguments["now"] = observed_at

    try:
        with _exclusive_postmatch_locks(
            [
                output_path,
                Path(state_path),
                Path(openfootball_cache_path),
                Path(results_path),
                Path(finished_store_path),
            ]
        ):
            return _run_postmatch_publish_unlocked(**arguments)
    except _PostmatchAlreadyRunning:
        return {"status": "blocked", "reason": "postmatch_already_running"}
    except _PostmatchLockUnavailable as exc:
        return {
            "status": "blocked",
            "reason": "postmatch_lock_unavailable",
            "error_type": exc.error_type,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh strict openfootball score.ft results and publish a complete "
            "World Cup snapshot. Defaults to a zero-side-effect dry run."
        )
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--now", default=None)
    parser.add_argument("--base-snapshot", default=str(DEFAULT_BASE_SNAPSHOT_PATH))
    parser.add_argument("--out", default=str(DEFAULT_POSTMATCH_SNAPSHOT_PATH))
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--openfootball-cache", default=str(DEFAULT_OPENFOOTBALL_CACHE_PATH))
    parser.add_argument("--history", default=str(DEFAULT_HISTORY_DIR))
    parser.add_argument("--results", default=str(DEFAULT_RESULTS_PATH))
    parser.add_argument("--finished-store", default=str(DEFAULT_FINISHED_STORE_PATH))
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--env", default=".env")
    args = parser.parse_args(argv)

    result = run_postmatch_publish(
        live=args.live,
        now=args.now,
        base_snapshot_path=args.base_snapshot,
        postmatch_snapshot_path=args.out,
        state_path=args.state,
        openfootball_cache_path=args.openfootball_cache,
        history_dir=args.history,
        results_path=args.results,
        finished_store_path=args.finished_store,
        endpoint=args.endpoint,
        env_path=args.env,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") not in {"error", "blocked", "publish_pending_invalid"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
