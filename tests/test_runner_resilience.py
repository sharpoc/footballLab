"""Self-tests for tests/run_tests.py runner resilience.

Uses synthetic test modules in tempfiles to verify:
- Normal module PASS
- Assertion failure FAIL
- SyntaxError FAIL (not crash)
- Missing non-optional dependency FAIL
- Allowed optional dependency skip → SKIP
- Multi-module: failure in one doesn't block others
- Exit code semantics
- Summary accuracy
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

_RUNNER = Path(__file__).resolve().parent / "run_tests.py"
_PYTHON = sys.executable


def _run_runner_on(test_dir: str) -> dict:
    """Run the test runner pointing at a custom test directory, return parsed output."""
    # We invoke run_tests.py via subprocess with a patched root
    # The runner discovers test_*.py under its own parent; we'll invoke a wrapper
    wrapper = dedent(f"""\
        import sys, importlib.util
        sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
        spec = importlib.util.spec_from_file_location("run_tests", {str(_RUNNER)!r})
        mod = importlib.util.module_from_spec(spec)
        # Monkey-patch the root to our temp dir
        import pathlib
        _orig_file = mod.__file__
        mod.__file__ = str(pathlib.Path({test_dir!r}) / "run_tests.py")
        spec.loader.exec_module(mod)
    """)
    # Actually simpler: just run the runner as-is but from the temp dir
    # We'll create a minimal runner copy that points at the temp dir
    runner_copy = Path(test_dir) / "run_tests.py"
    runner_copy.write_text(_RUNNER.read_text())

    result = subprocess.run(
        [_PYTHON, str(runner_copy)],
        capture_output=True,
        text=True,
        cwd=test_dir,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
        timeout=30,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _make_test_dir(*files: tuple[str, str]) -> str:
    """Create a temp dir with given (filename, content) pairs."""
    td = tempfile.mkdtemp(prefix="runner_test_")
    for name, content in files:
        p = Path(td) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return td


# --- Test: normal module passes ---

def test_normal_module_passes():
    td = _make_test_dir(("test_ok.py", dedent("""\
        def test_one():
            assert 1 + 1 == 2
        def test_two():
            assert True
    """)))
    r = _run_runner_on(td)
    assert r["returncode"] == 0, f"Expected 0, got {r['returncode']}\n{r['stdout']}\n{r['stderr']}"
    assert "PASS" in r["stdout"]
    assert "2" in r["stdout"]  # 2 tests


# --- Test: assertion failure counts as FAIL ---

def test_assertion_failure_is_fail():
    td = _make_test_dir(("test_bad.py", dedent("""\
        def test_fail():
            assert False, "intentional"
    """)))
    r = _run_runner_on(td)
    assert r["returncode"] == 1
    assert "FAIL" in r["stdout"]


# --- Test: SyntaxError in module should FAIL that module, not crash runner ---

def test_syntax_error_does_not_crash_runner():
    # CRITICAL: test_aaa_syntax.py sorts BEFORE test_zzz_ok.py
    # This proves the runner continues past the SyntaxError, not that it ran ok first.
    td = _make_test_dir(
        ("test_aaa_syntax.py", "def broken(\n"),
        ("test_zzz_ok.py", "def test_fine(): pass\n"),
    )
    r = _run_runner_on(td)
    # test_zzz_ok must still run AFTER the syntax error module
    assert "PASS test_zzz_ok.py::test_fine" in r["stdout"], (
        f"Runner did not continue past SyntaxError:\n{r['stdout']}\n{r['stderr']}"
    )
    # The syntax module should be reported as FAIL
    assert "FAIL test_aaa_syntax.py" in r["stdout"], (
        f"SyntaxError not reported as FAIL:\n{r['stdout']}"
    )
    assert r["returncode"] == 1


# --- Test: missing non-optional import should FAIL, not SKIP ---

def test_missing_nonoptional_import_is_fail():
    td = _make_test_dir(("test_missing.py", dedent("""\
        import nonexistent_package_xyz_12345
        def test_x():
            pass
    """)))
    r = _run_runner_on(td)
    assert r["returncode"] == 1
    # Should say FAIL or show error, not silently skip
    out = r["stdout"] + r["stderr"]
    assert "FAIL" in out or "error" in out.lower() or "Error" in out


# --- Test: allowed optional dependency produces SKIP ---

def test_allowed_optional_skip():
    td = _make_test_dir(("test_fastapi_app.py", dedent("""\
        # optional_deps: fastapi
        import fastapi
        def test_x():
            pass
    """)))
    r = _run_runner_on(td)
    # With the new runner, this should SKIP (exit 0) not FAIL
    out = r["stdout"]
    assert "SKIP" in out or "skip" in out, f"Expected SKIP: {out}\n{r['stderr']}"
    assert r["returncode"] == 0, f"Only skips should exit 0: {out}"


# --- Test: multi-module, failure in one doesn't block rest ---

def test_multi_module_continues_after_failure():
    td = _make_test_dir(
        ("test_a_first.py", "import nonexistent_xyz_999\ndef test_a(): pass\n"),
        ("test_b_second.py", "def test_b(): assert True\n"),
    )
    r = _run_runner_on(td)
    # test_b should still run
    assert "test_b" in r["stdout"] or "PASS" in r["stdout"], (
        f"Runner stopped at first failure: {r['stdout']}\n{r['stderr']}"
    )


# --- Test: exit code 0 only when no real failures ---

def test_exit_zero_with_only_skips():
    td = _make_test_dir(
        ("test_ok.py", "def test_fine(): pass\n"),
        ("test_fastapi_app.py", "# optional_deps: fastapi\nimport fastapi\ndef test_x(): pass\n"),
    )
    r = _run_runner_on(td)
    assert r["returncode"] == 0, f"Expected 0 with only pass+skip: {r['stdout']}\n{r['stderr']}"


# --- Test: allowlist file with WRONG dependency name must FAIL ---

def test_allowlist_file_wrong_dep_is_fail():
    """test_fastapi_app.py importing 'not_fastapi' is NOT in allowlist → FAIL."""
    td = _make_test_dir(("test_fastapi_app.py", dedent("""\
        import not_fastapi
        def test_x():
            pass
    """)))
    r = _run_runner_on(td)
    assert r["returncode"] == 1, f"Expected FAIL: {r['stdout']}"
    assert "FAIL" in r["stdout"]
    assert "SKIP" not in r["stdout"]


# --- Test: missing internal project module must FAIL ---

def test_missing_internal_module_is_fail():
    """Importing worldcup.nonexistent must FAIL even if file is in allowlist."""
    td = _make_test_dir(("test_internal.py", dedent("""\
        import worldcup.nonexistent_submodule_xyz
        def test_x():
            pass
    """)))
    r = _run_runner_on(td)
    assert r["returncode"] == 1, f"Expected FAIL for internal module: {r['stdout']}"
    assert "FAIL" in r["stdout"]
    assert "SKIP" not in r["stdout"]


# --- Test: plain ImportError (not ModuleNotFoundError) must FAIL ---

def test_plain_import_error_is_fail():
    td = _make_test_dir(("test_aaa_imp.py", dedent("""\
        raise ImportError("broken import")
        def test_x():
            pass
    """)),
        ("test_zzz_after.py", "def test_after(): pass\n"),
    )
    r = _run_runner_on(td)
    assert r["returncode"] == 1
    assert "FAIL test_aaa_imp.py" in r["stdout"]
    # Runner must continue past ImportError
    assert "PASS test_zzz_after.py::test_after" in r["stdout"], (
        f"Runner did not continue: {r['stdout']}"
    )


# --- Test: top-level RuntimeError must FAIL and continue ---

def test_toplevel_runtime_error_is_fail():
    td = _make_test_dir(
        ("test_aaa_boom.py", 'raise RuntimeError("module init exploded")\n'),
        ("test_zzz_ok.py", "def test_ok(): pass\n"),
    )
    r = _run_runner_on(td)
    assert r["returncode"] == 1
    assert "FAIL test_aaa_boom.py" in r["stdout"]
    assert "PASS test_zzz_ok.py::test_ok" in r["stdout"], (
        f"Runner did not continue past RuntimeError: {r['stdout']}"
    )


# --- Test: summary counts are accurate ---

def test_summary_counts_accurate():
    td = _make_test_dir(
        ("test_aaa_fail.py", "def test_x(): assert False\n"),
        ("test_bbb_pass.py", "def test_a(): pass\ndef test_b(): pass\n"),
        ("test_fastapi_app.py", "import fastapi\ndef test_x(): pass\n"),
    )
    r = _run_runner_on(td)
    out = r["stdout"]
    # 2 pass (test_a, test_b), 1 fail (test_x), 1 skip (fastapi module)
    assert "2/3 tests passed" in out, f"Wrong count: {out}"
    assert "1 module(s) skipped" in out, f"Skip count missing: {out}"
    assert r["returncode"] == 1


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import inspect as _inspect

    _failures = 0
    _count = 0
    for _name, _fn in sorted(_inspect.getmembers(sys.modules[__name__], _inspect.isfunction)):
        if not _name.startswith("test_"):
            continue
        _count += 1
        try:
            _fn()
        except Exception as _exc:
            _failures += 1
            print(f"FAIL {_name}: {_exc}")
        else:
            print(f"PASS {_name}")
    print(f"\n{_count - _failures}/{_count} passed")
    raise SystemExit(1 if _failures else 0)
