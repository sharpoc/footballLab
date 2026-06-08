from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def _parse_scalar(value: str):
    value = value.strip()
    if value == "":
        return {}
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value.strip('"').strip("'")


def _minimal_yaml_load(text: str) -> dict:
    """Parse the small two-level settings.yaml used by this project.

    PyYAML is preferred when present, but the MVP engine should still run in a
    bare Python environment for local verification.
    """
    root: dict = {}
    current: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" "):
            key, _, value = line.partition(":")
            if value.strip():
                root[key.strip()] = _parse_scalar(value)
                current = None
            else:
                current = {}
                root[key.strip()] = current
            continue
        if current is None:
            raise ValueError(f"Nested setting without parent: {raw_line!r}")
        key, _, value = line.strip().partition(":")
        current[key.strip()] = _parse_scalar(value)
    return root


@lru_cache(maxsize=None)
def load_config(path: str | None = None) -> dict:
    p = Path(path) if path else _DEFAULT
    text = p.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except Exception:
        return _minimal_yaml_load(text)
    return yaml.safe_load(text)
