from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS


_GATES = ("sport_catalog", "odds_sample", "team_identity", "result_contract")


def _gate_verified(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("verified") is True
        and bool(str(value.get("fingerprint") or "").strip())
    )


def evaluate_league_acceptance(competition_id: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
    if competition_id not in FORMAL_SINGLE_MATCH_IDS:
        raise ValueError("league_acceptance_competition_not_allowed")
    declared = str(evidence.get("competition_id") or competition_id).strip()
    if declared != competition_id:
        return {"competition_id": competition_id, "state": "blocked", "reason": "acceptance_competition_mismatch"}
    identity = evidence.get("team_identity")
    if isinstance(identity, Mapping) and int(identity.get("unmatched_count") or 0) > 0:
        return {"competition_id": competition_id, "state": "blocked", "reason": "unmatched_team_identity"}
    verified = [_gate_verified(evidence.get(name)) for name in _GATES]
    if not verified[0]:
        state = "disabled_until_live_acceptance"
    elif not verified[1]:
        state = "probing"
    elif not verified[2]:
        state = "odds_sample_verified"
    elif not verified[3]:
        state = "identity_verified"
    else:
        state = "active"
    fingerprints = {
        name: str((evidence.get(name) or {}).get("fingerprint") or "")
        for name in _GATES
        if isinstance(evidence.get(name), Mapping) and (evidence.get(name) or {}).get("fingerprint")
    }
    return {"competition_id": competition_id, "state": state, "reason": None, "fingerprints": fingerprints}


def acceptance_row_is_active(row: Any, competition_id: str) -> bool:
    if not isinstance(row, Mapping) or row.get("competition_id") != competition_id or row.get("state") != "active":
        return False
    fingerprints = row.get("fingerprints")
    return isinstance(fingerprints, Mapping) and all(
        bool(str(fingerprints.get(name) or "").strip()) for name in _GATES
    )


class LeagueAcceptanceStore:
    def __init__(self, path: str | Path = "data/local/leagues/acceptance.json") -> None:
        self.path = Path(path)
        if self.path.name != "acceptance.json" or self.path.parent.name != "leagues" or self.path.parent.parent.name != "local":
            raise ValueError("league_acceptance_path_isolation")
        self.lock_path = self.path.with_suffix(".lock")

    @staticmethod
    def _validate(report: Any) -> dict[str, Any]:
        if not isinstance(report, dict) or report.get("schema_version") != 1:
            raise ValueError("league_acceptance_invalid_report")
        competitions = report.get("competitions")
        if not isinstance(competitions, dict) or not set(competitions).issubset(FORMAL_SINGLE_MATCH_IDS):
            raise ValueError("league_acceptance_invalid_report")
        for competition_id, row in competitions.items():
            if not isinstance(row, dict) or row.get("competition_id") != competition_id:
                raise ValueError("league_acceptance_invalid_report")
            if row.get("state") == "active" and not acceptance_row_is_active(row, competition_id):
                raise ValueError("league_acceptance_invalid_active_evidence")
        return report

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            return self._validate(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("league_acceptance_invalid_report") from exc

    def write(self, report: Mapping[str, Any]) -> str:
        checked = self._validate(dict(report))
        payload = json.dumps(checked, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if self.path.exists() and self.path.read_text(encoding="utf-8") == payload:
                return "unchanged"
            fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as temp:
                    temp.write(payload)
                    temp.flush()
                    os.fsync(temp.fileno())
                os.replace(temp_name, self.path)
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        return "stored"


def acceptance_fingerprint(report: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(report), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
