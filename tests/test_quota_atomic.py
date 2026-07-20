"""Tests for quota.py atomic write and cross-process locking."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.quota import (
    _lock_path,
    load_quota_ledger,
    save_quota_ledger,
    update_quota_from_headers,
)


def test_save_quota_ledger_atomic_write():
    """Atomic write: target file is either fully written or untouched."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "quota.json"
        original = {"providers": {"old": {"remaining": 100}}}
        path.write_text(json.dumps(original), encoding="utf-8")

        save_quota_ledger(path, {"providers": {"new": {"remaining": 200}}})

        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["providers"]["new"]["remaining"] == 200
        assert "old" not in loaded["providers"]


def test_save_quota_ledger_no_temp_file_left_on_success():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "quota.json"
        save_quota_ledger(path, {"providers": {}})
        files = list(Path(tmp).iterdir())
        names = [f.name for f in files]
        assert "quota.json" in names
        assert not any(n.endswith(".tmp") for n in names)


def test_save_quota_ledger_no_temp_file_left_on_failure():
    """If write fails, no temp file is left behind."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "quota.json"
        path.write_text("{}", encoding="utf-8")
        try:
            save_quota_ledger(path, {"providers": {"bad": object()}})
        except TypeError:
            pass
        files = list(Path(tmp).iterdir())
        names = [f.name for f in files]
        assert not any(n.endswith(".tmp") for n in names)
        assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_lock_path_is_stable():
    path = Path("/some/dir/quota.json")
    assert _lock_path(path) == Path("/some/dir/quota.json.lock")


_WORKER_SCRIPT = """\
import sys
sys.path.insert(0, '.')
from worldcup.quota import update_quota_from_headers
update_quota_from_headers(
    sys.argv[1],
    sys.argv[2],
    {"x-requests-remaining": sys.argv[3], "x-requests-used": "1"},
    observed_at="2026-07-20T00:00:00+00:00",
)
"""


def test_concurrent_updates_preserve_all_providers():
    """Two processes updating different providers must both be preserved."""
    with TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "quota.json")

        procs = []
        for provider, remaining in [("provider_a", "100"), ("provider_b", "200")]:
            p = subprocess.Popen(
                [sys.executable, "-c", _WORKER_SCRIPT, path, provider, remaining],
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            procs.append(p)

        for p in procs:
            p.wait()
            assert p.returncode == 0

        ledger = load_quota_ledger(path)
        assert "provider_a" in ledger["providers"], "provider_a lost in concurrent write"
        assert "provider_b" in ledger["providers"], "provider_b lost in concurrent write"
        assert ledger["providers"]["provider_a"]["remaining"] == 100
        assert ledger["providers"]["provider_b"]["remaining"] == 200


def test_concurrent_updates_json_always_valid():
    """After many concurrent updates, file is always valid JSON."""
    with TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "quota.json")

        procs = []
        for i in range(8):
            p = subprocess.Popen(
                [sys.executable, "-c", _WORKER_SCRIPT, path, f"provider_{i}", str(i * 10)],
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            procs.append(p)

        for p in procs:
            p.wait()
            assert p.returncode == 0

        content = Path(path).read_text(encoding="utf-8")
        ledger = json.loads(content)
        assert len(ledger["providers"]) == 8


def test_existing_tests_still_pass_update_quota_from_headers():
    """Regression: existing behavior preserved with new implementation."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "quota.json"

        entry = update_quota_from_headers(
            path,
            "theoddsapi",
            {
                "x-requests-used": "3",
                "x-requests-remaining": "497",
                "x-requests-last": "3",
            },
            observed_at="2026-06-08T00:00:00+00:00",
        )

        assert entry["used"] == 3
        assert entry["remaining"] == 497
        assert entry["last"] == 3
        assert load_quota_ledger(path)["providers"]["theoddsapi"] == entry


def test_lock_file_created_and_independent():
    """Lock file exists after update and is separate from data file."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "quota.json"
        update_quota_from_headers(
            path, "test", {"x-requests-remaining": "10"},
            observed_at="2026-07-20T00:00:00+00:00",
        )
        lock = _lock_path(path)
        assert lock.exists()
        assert lock.stat().st_size == 0
        assert json.loads(path.read_text(encoding="utf-8"))["providers"]["test"]["remaining"] == 10
