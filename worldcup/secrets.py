from __future__ import annotations

import argparse
import secrets


def generate_hmac_secret(num_bytes: int = 32) -> str:
    return secrets.token_hex(num_bytes)


def format_env_assignment(secret: str, name: str = "INGEST_HMAC_SECRET") -> str:
    return f"{name}={secret}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a local HMAC secret value without writing .env."
    )
    parser.add_argument("--bytes", type=int, default=32)
    parser.add_argument("--name", default="INGEST_HMAC_SECRET")
    args = parser.parse_args(argv)

    print(format_env_assignment(generate_hmac_secret(args.bytes), args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
