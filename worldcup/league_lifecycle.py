from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_closing import LeagueClosingStore, select_league_closings
from worldcup.league_postmatch import build_league_postmatch
from worldcup.league_results import parse_verified_league_results
from worldcup.league_statistics import build_league_statistics
from worldcup.league_team_identity import accepted_league_team_identity_registry


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def run_league_lifecycle(
    *,
    root: str | Path,
    competition_ids: Iterable[str],
    write: bool = False,
) -> dict[str, Any]:
    root_path = Path(root)
    competition_ids = list(competition_ids)
    registry = accepted_league_team_identity_registry()
    competitions: dict[str, dict[str, Any]] = {}
    postmatch_blocks: list[dict[str, Any]] = []
    pending_writes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for competition_id in competition_ids:
        if competition_id not in FORMAL_SINGLE_MATCH_IDS:
            competitions[str(competition_id)] = {"status": "blocked", "reason": "competition_not_allowed"}
            continue
        partition = root_path / "data/local/leagues" / competition_id
        history_dir = partition / "history"
        scores_path = root_path / "data/cache/leagues" / competition_id / "scores.json"
        evidence_path = partition / "result_contract_evidence.json"
        if not history_dir.exists() or not scores_path.exists() or not evidence_path.exists():
            missing = []
            if not history_dir.exists():
                missing.append("history")
            if not scores_path.exists():
                missing.append("scores")
            if not evidence_path.exists():
                missing.append("result_contract_evidence")
            competitions[competition_id] = {
                "status": "blocked", "reason": "lifecycle_inputs_missing", "missing": missing,
            }
            continue
        try:
            snapshots = [
                _read_json(path) for path in sorted(history_dir.glob("*.json")) if path.is_file()
            ]
            if not snapshots:
                competitions[competition_id] = {
                    "status": "blocked", "reason": "lifecycle_inputs_missing", "missing": ["history"],
                }
                continue
            closing = select_league_closings(snapshots, competition_id)
            results = parse_verified_league_results(
                _read_json(scores_path),
                competition_id,
                result_contract_evidence=_read_json(evidence_path),
                identity_registry=registry,
            )
            postmatch = build_league_postmatch(closing, results, competition_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            competitions[competition_id] = {"status": "error", "reason": type(exc).__name__}
            continue
        postmatch_blocks.append(postmatch)
        pending_writes.append((competition_id, closing, postmatch))
        competitions[competition_id] = {
            "status": "ready",
            "closing_count": len(closing["closings"]),
            "result_count": len(results["results"]),
            "pending_result_count": len(results["pending"]),
            "decision_tally": postmatch["decision_tally"],
            "decision_coverage": postmatch["decision_coverage"],
        }
    ready_blocks = {block["competition_id"]: block for block in postmatch_blocks}
    stored_blocks: dict[str, dict[str, Any]] = {}
    for competition_id in competition_ids:
        postmatch_path = root_path / "data/local/leagues" / competition_id / "postmatch.json"
        if postmatch_path.exists():
            try:
                existing = _read_json(postmatch_path)
                if isinstance(existing, dict):
                    stored_blocks[competition_id] = existing
            except (OSError, json.JSONDecodeError):
                pass
    statistics = build_league_statistics(ready_blocks.values())
    stored_count = 0
    if write:
        for competition_id, closing, postmatch in pending_writes:
            partition = root_path / "data/local/leagues" / competition_id
            closing_path = partition / "closing.json"
            postmatch_path = partition / "postmatch.json"
            old_closing = closing_path.read_bytes() if closing_path.exists() else None
            old_postmatch = postmatch_path.read_bytes() if postmatch_path.exists() else None
            try:
                LeagueClosingStore(closing_path).commit(closing)
                _write_json_atomic(postmatch_path, postmatch)
            except (OSError, ValueError) as exc:
                for path, previous in ((closing_path, old_closing), (postmatch_path, old_postmatch)):
                    if previous is None:
                        path.unlink(missing_ok=True)
                    else:
                        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".rollback", dir=path.parent)
                        try:
                            with os.fdopen(fd, "wb") as stream:
                                stream.write(previous); stream.flush(); os.fsync(stream.fileno())
                            os.replace(temp_name, path)
                        finally:
                            if os.path.exists(temp_name): os.unlink(temp_name)
                competitions[competition_id] = {"status": "error", "reason": type(exc).__name__}
            else:
                competitions[competition_id]["status"] = "stored"
                stored_blocks[competition_id] = postmatch
                stored_count += 1
        statistics = build_league_statistics(stored_blocks.values())
        if stored_blocks:
            _write_json_atomic(root_path / "data/local/leagues/statistics.json", statistics)
    status = "stored" if write and stored_count else "dry_run" if not write else "blocked"
    return {"status": status, "competitions": competitions, "statistics": statistics}
