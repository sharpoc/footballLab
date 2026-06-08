from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path


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
    failures = 0
    count = 0
    for path in sorted(root.rglob("test_*.py")):
        module = _load(path)
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_"):
                continue
            count += 1
            try:
                fn()
            except Exception as exc:
                failures += 1
                print(f"FAIL {path.relative_to(root)}::{name}: {exc}")
            else:
                print(f"PASS {path.relative_to(root)}::{name}")
    print(f"{count - failures}/{count} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
