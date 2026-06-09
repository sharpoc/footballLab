import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.postgres_smoke import build_postgres_smoke_dry_run


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {"run_id": "20260608T000000Z-local"},
        "counts": {"matches": 1},
        "matches": [{"home_team": "Mexico", "away_team": "South Africa"}],
    }


def test_postgres_smoke_blocks_when_database_url_missing_without_printing_secret():
    with TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "snapshot.json"
        snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")

        result = build_postgres_smoke_dry_run(
            snapshot_path=snapshot_path,
            endpoint="https://example.com/api/ingest/snapshot",
            env={
                "WORLDCUP_STORE": "postgres",
                "INGEST_HMAC_SECRET": "very-secret-value",
            },
            timestamp="2026-06-08T00:02:00+00:00",
        )

        serialized = json.dumps(result, ensure_ascii=False)
        assert result["status"] == "blocked"
        assert result["checks"]["database_url"]["status"] == "error"
        assert result["checks"]["database_url"]["message"] == "missing_DATABASE_URL"
        assert "very-secret-value" not in serialized


def test_postgres_smoke_ready_summary_is_redacted():
    with TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "snapshot.json"
        snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")

        result = build_postgres_smoke_dry_run(
            snapshot_path=snapshot_path,
            endpoint="https://example.com/api/ingest/snapshot",
            env={
                "WORLDCUP_STORE": "postgres",
                "DATABASE_URL": "postgresql://user:pass@example.invalid/db",
                "INGEST_HMAC_SECRET": "very-secret-value",
            },
            timestamp="2026-06-08T00:02:00+00:00",
        )

        serialized = json.dumps(result, ensure_ascii=False)
        assert result["status"] == "dry_run_ready"
        assert result["checks"]["store"]["store"] == "postgres"
        assert result["checks"]["database_url"]["status"] == "ok"
        assert result["request"]["method"] == "POST"
        assert result["request"]["path"] == "/api/ingest/snapshot"
        assert result["request"]["run_id"] == "20260608T000000Z-local"
        assert result["request"]["body_sha256"]
        assert result["expected_sequence"] == ["stored", "duplicate"]
        assert "very-secret-value" not in serialized
        assert "postgresql://user:pass@example.invalid/db" not in serialized
        assert "X-Worldcup-Signature" not in serialized
        assert '"body"' not in serialized


def test_postgres_smoke_blocks_when_store_is_not_postgres():
    with TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "snapshot.json"
        snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")

        result = build_postgres_smoke_dry_run(
            snapshot_path=snapshot_path,
            endpoint="https://example.com/api/ingest/snapshot",
            env={
                "WORLDCUP_STORE": "sqlite",
                "INGEST_HMAC_SECRET": "very-secret-value",
            },
            timestamp="2026-06-08T00:02:00+00:00",
        )

        assert result["status"] == "blocked"
        assert result["checks"]["store"]["message"] == "expected_postgres"
