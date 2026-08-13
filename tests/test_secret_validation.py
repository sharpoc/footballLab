"""Tests for validate_hmac_secret hard-fail at configuration boundaries.

Architecture:
- Central validate_hmac_secret: unit tests for edge cases.
- Entry points (http_app, fastapi_app, ingest, publish): validate before service start.
- Scheduled publishers (scheduled_publish, csl, postmatch): live=True fails immediately
  with stubs proving no refresh/publish/network side effect was called.
- Readiness: weak_secret error without leakage.
- verify_ingest_request: bottom-layer still accepts short explicit secret parameter.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


# === Central validate_hmac_secret ===

def test_validate_missing_raises():
    from worldcup.secrets import validate_hmac_secret
    try:
        validate_hmac_secret(None)
        assert False, "Should raise"
    except ValueError as e:
        assert str(e) == "weak_secret"


def test_validate_empty_raises():
    from worldcup.secrets import validate_hmac_secret
    try:
        validate_hmac_secret("")
        assert False, "Should raise"
    except ValueError as e:
        assert str(e) == "weak_secret"


def test_validate_31_ascii_bytes_raises():
    from worldcup.secrets import validate_hmac_secret
    try:
        validate_hmac_secret("a" * 31)
        assert False, "Should raise"
    except ValueError as e:
        assert str(e) == "weak_secret"


def test_validate_32_ascii_bytes_passes():
    from worldcup.secrets import validate_hmac_secret
    validate_hmac_secret("a" * 32)


def test_validate_64_lowercase_hex_passes():
    from worldcup.secrets import validate_hmac_secret
    validate_hmac_secret("ab" * 32)


def test_validate_utf8_multibyte_boundary():
    """4 UTF-8 bytes per char x 8 = 32 bytes, passes minimum."""
    from worldcup.secrets import validate_hmac_secret
    secret = "\U0001f600" * 8
    assert len(secret.encode("utf-8")) == 32
    validate_hmac_secret(secret)


def test_validate_utf8_multibyte_below():
    """7 x 4-byte chars = 28 bytes + 3 ASCII = 31 bytes, fails."""
    from worldcup.secrets import validate_hmac_secret
    secret = "\U0001f600" * 7 + "abc"
    assert len(secret.encode("utf-8")) == 31
    try:
        validate_hmac_secret(secret)
        assert False, "Should raise"
    except ValueError:
        pass


def test_validate_error_does_not_contain_secret():
    from worldcup.secrets import validate_hmac_secret
    test_secret = "short_distinctive_val"
    try:
        validate_hmac_secret(test_secret)
    except ValueError as e:
        assert test_secret not in str(e)
        assert "21" not in str(e)


# === Consistency: validate uses same minimum as check_secret ===

def test_validate_consistent_with_check_secret():
    from worldcup.secrets import validate_hmac_secret, check_secret
    result_31 = check_secret("a" * 31)
    assert result_31["minimum_length_ok"] is False
    try:
        validate_hmac_secret("a" * 31)
        assert False
    except ValueError:
        pass
    result_32 = check_secret("a" * 32)
    assert result_32["minimum_length_ok"] is True
    validate_hmac_secret("a" * 32)


# === Entry points: http_app ===

def test_http_app_rejects_short_secret():
    from worldcup.http_app import main as http_main
    env_path = _write_env("INGEST_HMAC_SECRET=short\n")
    try:
        http_main(["--env", env_path, "--port", "0"])
        assert False, "Should SystemExit"
    except SystemExit as e:
        assert e.code
        assert "short" not in str(e.code)
    finally:
        os.unlink(env_path)


def test_http_app_accepts_valid_secret():
    """Valid secret passes config check (verify via direct validation)."""
    from worldcup.refresh_runner import _load_env
    from worldcup.secrets import validate_hmac_secret
    env_path = _write_env(f"INGEST_HMAC_SECRET={'a' * 64}\n")
    try:
        secret = _load_env(env_path).get("INGEST_HMAC_SECRET")
        validate_hmac_secret(secret)
    finally:
        os.unlink(env_path)


# === Entry points: fastapi_app ===

def test_fastapi_load_secret_rejects_short():
    try:
        from worldcup.fastapi_app import load_secret
    except ImportError:
        return  # fastapi not installed, skip
    env_path = _write_env("INGEST_HMAC_SECRET=tiny\n")
    try:
        load_secret(env_path=env_path)
        assert False, "Should SystemExit"
    except SystemExit as e:
        assert "tiny" not in str(e.code)
    finally:
        os.unlink(env_path)


# === Entry points: ingest CLI ===

def test_ingest_cli_rejects_short():
    from worldcup.ingest import main as ingest_main
    env_path = _write_env("INGEST_HMAC_SECRET=x\n")
    try:
        ingest_main(["--env", env_path, "--snapshot-path", "/dev/null"])
        assert False, "Should SystemExit"
    except SystemExit as e:
        assert e.code
    finally:
        os.unlink(env_path)


# === Entry points: publish CLI ===

def test_publish_cli_rejects_short():
    from worldcup.publish import main as publish_main
    env_path = _write_env("INGEST_HMAC_SECRET=ab\n")
    try:
        publish_main(["--env", env_path, "--snapshot-path", "/dev/null"])
        assert False, "Should SystemExit"
    except SystemExit as e:
        assert e.code
    finally:
        os.unlink(env_path)


# === Entry points: scheduled_publish (fail-fast before refresh) ===

def test_scheduled_publish_rejects_weak_secret():
    """live=True + weak secret raises immediately; refresh_fn never called."""
    from worldcup.scheduled_publish import run_scheduled_publish
    env_path = _write_env("INGEST_HMAC_SECRET=short\nTHE_ODDS_API_KEY=x\n")

    call_log = []

    def exploding_refresh(**kwargs):
        call_log.append("refresh")
        raise RuntimeError("refresh should not be called")

    try:
        run_scheduled_publish(
            env_path=env_path,
            endpoint="https://example.invalid/api/ingest/snapshot",
            secret=None,
            now="2026-07-20T00:00:00+00:00",
            force=False,
            live=True,
            refresh_fn=exploding_refresh,
        )
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert str(e) == "weak_ingest_hmac_secret"
    finally:
        os.unlink(env_path)
    assert call_log == [], "refresh_fn must not be called when secret is weak"


def test_scheduled_publish_missing_secret_raises():
    """live=True + missing secret raises immediately."""
    from worldcup.scheduled_publish import run_scheduled_publish
    env_path = _write_env("THE_ODDS_API_KEY=x\n")

    def exploding_refresh(**kwargs):
        raise RuntimeError("refresh should not be called")

    try:
        run_scheduled_publish(
            env_path=env_path,
            endpoint="https://example.invalid/api/ingest/snapshot",
            secret=None,
            now="2026-07-20T00:00:00+00:00",
            force=False,
            live=True,
            refresh_fn=exploding_refresh,
        )
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "missing" in str(e).lower() or "INGEST_HMAC_SECRET" in str(e)
    finally:
        os.unlink(env_path)


def test_scheduled_publish_dry_run_no_secret_needed():
    """live=False does not require or validate secret."""
    from worldcup.scheduled_publish import run_scheduled_publish
    env_path = _write_env("")  # no secret at all

    result = run_scheduled_publish(
        env_path=env_path,
        endpoint="https://example.invalid/api/ingest/snapshot",
        secret=None,
        now="2026-07-20T00:00:00+00:00",
        force=False,
        live=False,
    )
    # dry-run returns a report (status=skipped from scheduler)
    assert result["status"] in ("skipped", "dry_run", "not_due")
    os.unlink(env_path)


# === Entry points: csl_scheduled_publish (fail-fast before refresh) ===

def test_csl_scheduled_publish_blocks_weak_secret_when_refresh_due():
    """A due live refresh blocks a weak secret before refresh is called."""
    from worldcup.csl_scheduled_publish import run_csl_scheduled_publish
    env_path = _write_env("INGEST_HMAC_SECRET=tiny\nTHE_ODDS_API_KEY=x\n")

    call_log = []

    def exploding_refresh(**kwargs):
        call_log.append("refresh")
        raise RuntimeError("refresh should not be called")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        result = run_csl_scheduled_publish(
            env_path=env_path,
            cache_dir=root / "cache",
            quota_path=root / "quota.json",
            snapshot_path=root / "snapshot.json",
            diagnostics_snapshot_path=root / "diagnostics" / "snapshot.json",
            endpoint="https://example.invalid/api/ingest/snapshot",
            now="2026-07-20T00:00:00+00:00",
            force=False,
            live=True,
            refresh_fn=exploding_refresh,
        )
    assert result["status"] == "blocked"
    assert result["reason"] == "weak_ingest_hmac_secret"
    assert call_log == [], "refresh_fn must not be called when secret is weak"
    os.unlink(env_path)


def test_csl_scheduled_publish_dry_run_no_secret_needed():
    """live=False does not require secret."""
    from worldcup.csl_scheduled_publish import run_csl_scheduled_publish
    env_path = _write_env("")

    result = run_csl_scheduled_publish(
        env_path=env_path,
        endpoint="https://example.invalid/api/ingest/snapshot",
        now="2026-07-20T00:00:00+00:00",
        force=False,
        live=False,
    )
    assert result["status"] == "dry_run"
    os.unlink(env_path)


# === Entry points: postmatch_publish ===

def test_postmatch_publish_blocks_weak_secret():
    from worldcup.postmatch_publish import _run_postmatch_publish_unlocked
    env_path = _write_env("INGEST_HMAC_SECRET=x\n")
    result = _run_postmatch_publish_unlocked(
        env_path=env_path,
        endpoint="https://real.example.invalid/api/ingest/snapshot",
        now="2026-07-20T00:00:00+00:00",
        live=True,
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "weak_ingest_hmac_secret"
    os.unlink(env_path)


def test_postmatch_publish_dry_run_no_secret_needed():
    from worldcup.postmatch_publish import _run_postmatch_publish_unlocked
    env_path = _write_env("")
    result = _run_postmatch_publish_unlocked(
        env_path=env_path,
        endpoint="https://real.example.invalid/api/ingest/snapshot",
        now="2026-07-20T00:00:00+00:00",
        live=False,
    )
    assert result["status"] == "dry_run"
    os.unlink(env_path)


# === Readiness: weak secret is error ===

def test_readiness_weak_secret_is_error():
    from worldcup.readiness import run_readiness_checks
    td = tempfile.mkdtemp()
    Path(td, ".env").write_text("INGEST_HMAC_SECRET=short\nTHE_ODDS_API_KEY=x\n")
    Path(td, ".env.example").write_text(
        "API_FOOTBALL_KEY=\nTHE_ODDS_API_KEY=\nTHE_ODDS_API_KEY_PRIMARY=\n"
        "THE_ODDS_API_KEY_SECONDARY=\nTHE_ODDS_API_KEY_TERTIARY=\n"
        "ODDS_API_IO_KEY=\nODDSPAPI_KEY=\nINGEST_HMAC_SECRET=\n"
        "WORLDCUP_STORE=\nDATABASE_URL=\n"
    )
    Path(td, ".gitignore").write_text(".env\ndata/cache/\ndata/local/\ndata/probe/\n")
    result = run_readiness_checks(td)
    hmac_check = result["checks"].get("env_INGEST_HMAC_SECRET")
    assert hmac_check["status"] == "error"
    assert hmac_check["message"] == "weak_secret"
    assert "short" not in json.dumps(hmac_check)


def test_readiness_valid_secret_is_ok():
    from worldcup.readiness import run_readiness_checks
    td = tempfile.mkdtemp()
    Path(td, ".env").write_text(f"INGEST_HMAC_SECRET={'f' * 64}\nTHE_ODDS_API_KEY=x\nWORLDCUP_STORE=sqlite\n")
    Path(td, ".env.example").write_text(
        "API_FOOTBALL_KEY=\nTHE_ODDS_API_KEY=\nTHE_ODDS_API_KEY_PRIMARY=\n"
        "THE_ODDS_API_KEY_SECONDARY=\nTHE_ODDS_API_KEY_TERTIARY=\n"
        "ODDS_API_IO_KEY=\nODDSPAPI_KEY=\nINGEST_HMAC_SECRET=\n"
        "WORLDCUP_STORE=\nDATABASE_URL=\n"
    )
    Path(td, ".gitignore").write_text(".env\ndata/cache/\ndata/local/\ndata/probe/\n")
    result = run_readiness_checks(td)
    hmac_check = result["checks"].get("env_INGEST_HMAC_SECRET")
    assert hmac_check["status"] == "ok"


# === verify_ingest_request contract unchanged ===

def test_verify_ingest_request_still_accepts_short_secret_parameter():
    """Bottom-layer API must NOT enforce length — only config boundaries do."""
    from worldcup.ingest_server import verify_ingest_request
    from worldcup.ingest import canonical_json

    snapshot = {"hello": "world"}
    snapshot_id = hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()
    body = json.dumps({"run_id": "r1", "snapshot_id": snapshot_id, "snapshot": snapshot})
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    result = verify_ingest_request(
        method="POST",
        path="/api/ingest/snapshot",
        headers={
            "X-Worldcup-Timestamp": "2026-06-08T00:00:00+00:00",
            "X-Worldcup-Run-Id": "r1",
            "X-Worldcup-Snapshot-Id": snapshot_id,
            "X-Worldcup-Body-SHA256": body_hash,
            "X-Worldcup-Signature": "sha256=wrong",
            "X-Worldcup-Idempotency-Key": f"r1:{snapshot_id}",
        },
        body=body,
        secret="x",  # intentionally short
        now="2026-06-08T00:00:00+00:00",
    )
    # Should reject on signature mismatch, NOT on length
    assert result.ok is False
    assert result.reason == "signature_mismatch"


# === Helpers ===

def _write_env(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".env")
    os.write(fd, content.encode("utf-8"))
    os.close(fd)
    return path


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
