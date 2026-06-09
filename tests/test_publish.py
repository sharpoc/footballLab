import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.publish import publish_snapshot


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {
            "run_id": "20260608T000000Z-live",
            "observed_at": "2026-06-08T00:00:00+00:00",
        },
        "counts": {"matches": 1},
        "matches": [{"home_team": "Mexico", "away_team": "South Africa"}],
    }


def test_publish_snapshot_dry_run_never_calls_sender_or_exposes_secret():
    def sender(_request):
        raise AssertionError("dry-run must not send HTTP")

    with TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "snapshot.json"
        snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")

        result = publish_snapshot(
            snapshot_path=snapshot_path,
            endpoint="https://football.celab.xin/api/ingest/snapshot",
            secret="very-secret-value",
            timestamp="2026-06-08T00:01:00+00:00",
            sender=sender,
        )

    serialized = json.dumps(result)
    assert result["status"] == "dry_run"
    assert result["request"]["url"] == "https://football.celab.xin/api/ingest/snapshot"
    assert result["request"]["path"] == "/api/ingest/snapshot"
    assert result["request"]["run_id"] == "20260608T000000Z-live"
    assert result["request"]["body_bytes"] > 0
    assert result["request"]["body_sha256"]
    assert "X-Worldcup-Signature" not in result["request"]["header_names"]
    assert "very-secret-value" not in serialized
    assert "sha256=" not in serialized


def test_publish_snapshot_live_uses_sender_and_returns_redacted_result():
    calls = []

    def sender(request):
        calls.append(request)
        return {
            "http_status": 200,
            "body": json.dumps({"status": "stored", "run_id": "server-run"}),
        }

    with TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "snapshot.json"
        snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")

        result = publish_snapshot(
            snapshot_path=snapshot_path,
            endpoint="https://football.celab.xin/api/ingest/snapshot",
            secret="very-secret-value",
            timestamp="2026-06-08T00:01:00+00:00",
            live=True,
            sender=sender,
        )

    serialized = json.dumps(result)
    assert result["status"] == "sent"
    assert result["http_status"] == 200
    assert result["ingest_status"] == "stored"
    assert result["request"]["body_bytes"] > 0
    assert calls[0]["headers"]["X-Worldcup-Signature"].startswith("sha256=")
    assert "very-secret-value" not in serialized
    assert "sha256=" not in serialized
    assert "body" not in result["request"]
