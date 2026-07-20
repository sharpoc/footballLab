from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

# Explicit allowlist: test filename → set of optional package names.
# Only these specific (file, missing package) pairs produce SKIP.
# Any other import failure is a hard FAIL.
_OPTIONAL_DEPS: dict[str, set[str]] = {
    "test_fastapi_app.py": {"fastapi"},
}


def _is_allowed_skip(path: Path, exc: ModuleNotFoundError) -> bool:
    allowed = _OPTIONAL_DEPS.get(path.name)
    if allowed is None:
        return False
    missing = exc.name
    if missing is None:
        return False
    top_level = missing.split(".")[0]
    return top_level in allowed


def _load(path: Path):
    name = path.with_suffix("").as_posix().replace("/", ".")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    root = Path(__file__).resolve().parent
    project_root = root.parent
    sys.path.insert(0, str(project_root))

    passed = 0
    failures = 0
    skipped_modules: list[str] = []
    failed_modules: list[tuple[str, str]] = []

    for path in sorted(root.rglob("test_*.py")):
        rel = str(path.relative_to(root))
        try:
            module = _load(path)
        except ModuleNotFoundError as exc:
            if _is_allowed_skip(path, exc):
                skipped_modules.append(f"{rel} (optional: {exc.name})")
                print(f"SKIP {rel}: optional dependency '{exc.name}' not installed")
                continue
            failed_modules.append((rel, f"ModuleNotFoundError: {exc}"))
            failures += 1
            print(f"FAIL {rel}: module load failed: {exc}")
            continue
        except Exception as exc:
            failed_modules.append((rel, f"{type(exc).__name__}: {exc}"))
            failures += 1
            print(f"FAIL {rel}: module load failed: {exc}")
            continue

        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_"):
                continue
            try:
                fn()
            except Exception as exc:
                failures += 1
                print(f"FAIL {rel}::{name}: {exc}")
            else:
                passed += 1
                print(f"PASS {rel}::{name}")

    # Summary
    total = passed + failures
    print(f"\n{passed}/{total} tests passed", end="")
    if skipped_modules:
        print(f", {len(skipped_modules)} module(s) skipped", end="")
    print()
    if failed_modules:
        print("Failed modules:")
        for mod, reason in failed_modules:
            print(f"  {mod}: {reason}")
    if skipped_modules:
        print("Skipped modules:")
        for desc in skipped_modules:
            print(f"  {desc}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
