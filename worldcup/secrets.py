from __future__ import annotations

import argparse
import json
import re
import secrets
from pathlib import Path

_GENERATOR_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MINIMUM_LENGTH = 32


def generate_hmac_secret(num_bytes: int = 32) -> str:
    return secrets.token_hex(num_bytes)


def format_env_assignment(secret: str, name: str = "INGEST_HMAC_SECRET") -> str:
    return f"{name}={secret}"


def validate_hmac_secret(secret: str | None) -> None:
    """Raise ValueError("weak_secret") if secret is missing or too short.

    Minimum: 32 UTF-8 bytes. Does not guarantee entropy.
    """
    if not secret or len(secret.encode("utf-8")) < _MINIMUM_LENGTH:
        raise ValueError("weak_secret")


def _load_env_value(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    result = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            result = value.strip().strip('"').strip("'")
    return result


def check_secret(secret: str | None) -> dict:
    if not secret:
        return {
            "configured": False,
            "minimum_length_ok": False,
            "generator_format_ok": False,
        }
    byte_length = len(secret.encode("utf-8"))
    return {
        "configured": True,
        "minimum_length_ok": byte_length >= _MINIMUM_LENGTH,
        "generator_format_ok": bool(_GENERATOR_PATTERN.match(secret)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate or check INGEST_HMAC_SECRET."
    )
    parser.add_argument("--check", action="store_true",
                        help="Check existing secret strength (no generation).")
    parser.add_argument("--env-file", default=".env",
                        help="Path to .env file (used with --check).")
    parser.add_argument("--secret-env", default="INGEST_HMAC_SECRET",
                        help="Variable name to check (used with --check).")
    parser.add_argument("--bytes", type=int, default=32,
                        help="Bytes of randomness for generation (default: 32).")
    parser.add_argument("--name", default="INGEST_HMAC_SECRET",
                        help="Variable name for generation output.")
    args = parser.parse_args(argv)

    if args.check:
        env_path = Path(args.env_file)
        secret = _load_env_value(env_path, args.secret_env)
        result = check_secret(secret)
        print(json.dumps(result))
        ok = result["configured"] and result["minimum_length_ok"]
        return 0 if ok else 1

    print(format_env_assignment(generate_hmac_secret(args.bytes), args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
