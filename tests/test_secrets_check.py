"""Tests for worldcup.secrets --check mode.

Covers: backward compatibility, check mode semantics, exit codes,
output safety (no secret leakage), edge cases.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

_PYTHON = sys.executable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MODULE = "worldcup.secrets"


def _run(args: list[str], env_override: dict[str, str] | None = None) -> dict:
    """Run python3 -m worldcup.secrets with given args."""
    full_env = {**os.environ, "PYTHONPATH": str(_PROJECT_ROOT)}
    if env_override:
        full_env.update(env_override)
    result = subprocess.run(
        [_PYTHON, "-m", _MODULE, *args],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
        env=full_env,
        timeout=10,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _write_env(content: str) -> str:
    """Write a temp .env file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".env", prefix="secret_check_")
    os.write(fd, content.encode("utf-8"))
    os.close(fd)
    return path


# === Backward compatibility ===

def test_default_generate_still_outputs_64_hex():
    """Default command (no --check) must still produce VARNAME=64hex."""
    r = _run([])
    assert r["returncode"] == 0
    line = r["stdout"].strip()
    assert line.startswith("INGEST_HMAC_SECRET=")
    value = line.split("=", 1)[1]
    assert len(value) == 64
    int(value, 16)  # valid hex


def test_generate_with_custom_bytes():
    r = _run(["--bytes", "16"])
    assert r["returncode"] == 0
    value = r["stdout"].strip().split("=", 1)[1]
    assert len(value) == 32


# === Red-light: --check not supported in old CLI ===

def test_check_mode_exists():
    """--check must be recognized (red-light for old CLI)."""
    env_path = _write_env("INGEST_HMAC_SECRET=aabbccdd" * 8 + "\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        # Must not error out with "unrecognized arguments"
        assert "unrecognized" not in r["stderr"], f"--check not recognized: {r['stderr']}"
        assert r["returncode"] in (0, 1)
    finally:
        os.unlink(env_path)


# === Check mode: configured + minimum_length_ok + generator_format_ok ===

def test_check_64_lowercase_hex_all_pass():
    secret = "a1b2c3d4e5f6" * 5 + "a1b2"  # 64 lowercase hex chars
    env_path = _write_env(f"INGEST_HMAC_SECRET={secret}\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] == 0
        data = json.loads(r["stdout"])
        assert data["configured"] is True
        assert data["minimum_length_ok"] is True
        assert data["generator_format_ok"] is True
    finally:
        os.unlink(env_path)


def test_check_exactly_32_bytes_passes_minimum():
    secret = "x" * 32  # 32 bytes, not hex
    env_path = _write_env(f"INGEST_HMAC_SECRET={secret}\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] == 0
        data = json.loads(r["stdout"])
        assert data["configured"] is True
        assert data["minimum_length_ok"] is True
        assert data["generator_format_ok"] is False  # not 64 lowercase hex
    finally:
        os.unlink(env_path)


def test_check_short_secret_fails():
    secret = "abc123"  # < 32
    env_path = _write_env(f"INGEST_HMAC_SECRET={secret}\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] != 0
        data = json.loads(r["stdout"])
        assert data["configured"] is True
        assert data["minimum_length_ok"] is False
    finally:
        os.unlink(env_path)


def test_check_missing_secret_fails():
    env_path = _write_env("THE_ODDS_API_KEY=something\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] != 0
        data = json.loads(r["stdout"])
        assert data["configured"] is False
        assert data["minimum_length_ok"] is False
    finally:
        os.unlink(env_path)


def test_check_empty_value_fails():
    env_path = _write_env("INGEST_HMAC_SECRET=\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] != 0
        data = json.loads(r["stdout"])
        assert data["configured"] is False
    finally:
        os.unlink(env_path)


def test_check_missing_file_fails():
    r = _run(["--check", "--env-file", "/nonexistent/path/.env"])
    assert r["returncode"] != 0
    data = json.loads(r["stdout"])
    assert data["configured"] is False


def test_check_uppercase_hex_64_passes_minimum_but_not_generator_format():
    secret = "A1B2C3D4E5F6" * 5 + "A1B2"  # 64 uppercase hex
    env_path = _write_env(f"INGEST_HMAC_SECRET={secret}\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] == 0  # minimum length ok
        data = json.loads(r["stdout"])
        assert data["configured"] is True
        assert data["minimum_length_ok"] is True
        assert data["generator_format_ok"] is False  # generator outputs lowercase
    finally:
        os.unlink(env_path)


def test_check_long_non_hex_passes_minimum():
    secret = "this-is-not-hex-but-long-enough-for-32-bytes!!"  # >32
    env_path = _write_env(f"INGEST_HMAC_SECRET={secret}\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] == 0
        data = json.loads(r["stdout"])
        assert data["minimum_length_ok"] is True
        assert data["generator_format_ok"] is False
    finally:
        os.unlink(env_path)


def test_check_quoted_value_stripped():
    """Quotes around value should be stripped (matching _load_env behavior)."""
    secret = "a" * 64
    env_path = _write_env(f'INGEST_HMAC_SECRET="{secret}"\n')
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] == 0
        data = json.loads(r["stdout"])
        assert data["configured"] is True
        assert data["minimum_length_ok"] is True
        assert data["generator_format_ok"] is True
    finally:
        os.unlink(env_path)


def test_check_duplicate_key_uses_last():
    """Duplicate keys: last value wins (matching _load_env dict overwrite)."""
    short = "ab"
    long_hex = "f" * 64
    env_path = _write_env(f"INGEST_HMAC_SECRET={short}\nINGEST_HMAC_SECRET={long_hex}\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] == 0
        data = json.loads(r["stdout"])
        assert data["minimum_length_ok"] is True
        assert data["generator_format_ok"] is True
    finally:
        os.unlink(env_path)


# === Output safety: no secret leakage ===

def test_check_output_does_not_contain_secret():
    secret = "deadbeef" * 8  # 64 chars, distinctive
    env_path = _write_env(f"INGEST_HMAC_SECRET={secret}\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        combined = r["stdout"] + r["stderr"]
        assert secret not in combined
        # No length number beyond field names
        assert "64" not in combined or "generator_format_ok" in combined
    finally:
        os.unlink(env_path)


def test_check_short_output_does_not_contain_secret():
    secret = "shortval"
    env_path = _write_env(f"INGEST_HMAC_SECRET={secret}\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        combined = r["stdout"] + r["stderr"]
        assert secret not in combined
    finally:
        os.unlink(env_path)


# === Exit code semantics ===

def test_exit_code_zero_when_minimum_ok():
    """Exit 0 when configured + minimum_length_ok, regardless of generator_format."""
    secret = "Z" * 40  # >32 but not hex
    env_path = _write_env(f"INGEST_HMAC_SECRET={secret}\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] == 0
    finally:
        os.unlink(env_path)


def test_exit_code_nonzero_when_too_short():
    env_path = _write_env("INGEST_HMAC_SECRET=short\n")
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] == 1
    finally:
        os.unlink(env_path)


def test_exit_code_nonzero_when_missing():
    env_path = _write_env("")
    try:
        r = _run(["--check", "--env-file", env_path])
        assert r["returncode"] == 1
    finally:
        os.unlink(env_path)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import inspect

    failures = 0
    count = 0
    for name, fn in sorted(inspect.getmembers(sys.modules[__name__], inspect.isfunction)):
        if not name.startswith("test_"):
            continue
        count += 1
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"PASS {name}")
    print(f"\n{count - failures}/{count} passed")
    raise SystemExit(1 if failures else 0)
