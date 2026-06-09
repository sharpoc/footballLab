import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.export import export_static_site


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {
            "run_id": "run-1",
            "quota": {"private-provider": {"remaining": 777777, "used": 123456}},
            "source_errors": [
                {"source": "private-provider", "error": "TimeoutError: raw upstream detail"}
            ],
        },
        "counts": {"matches": 1},
        "data_quality": {
            "stale_sources": ["private-provider"],
            "source_errors": [
                {"source": "private-provider", "error": "TimeoutError: raw upstream detail"}
            ],
            "missing_odds": ["Private Team vs Hidden Team"],
            "missing_elo": [],
            "time_mismatches": ["Internal fixture mismatch detail"],
        },
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "stage": "Matchday 1",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "signals": [{"grade": "A"}],
            }
        ],
    }


def test_export_static_site_writes_html_snapshot_and_matches_json():
    with TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "site"

        result = export_static_site(_snapshot(), out_dir)

        assert result["index_path"] == str(out_dir / "index.html")
        assert (out_dir / "index.html").exists()
        assert (out_dir / "api" / "snapshot" / "latest.json").exists()
        assert (out_dir / "api" / "matches.json").exists()
        html = (out_dir / "index.html").read_text(encoding="utf-8")
        assert "Research Ledger" in html
        assert "Research only, not betting advice." in html
        assert "stake" not in html.lower()
        assert "bet amount" not in html.lower()
        assert "Mexico vs South Africa" in html
        public_snapshot = json.loads((out_dir / "api" / "snapshot" / "latest.json").read_text())[
            "snapshot"
        ]
        assert public_snapshot["snapshot_at"] == "2026-06-08T00:00:00+00:00"
        assert public_snapshot["matches"][0]["match_label"] == "Mexico vs South Africa"
        assert public_snapshot["data_quality"]["source_error_count"] == 1
        assert public_snapshot["data_quality"]["stale_source_count"] == 1
        assert "run" not in public_snapshot
        assert "run_id" not in json.dumps(public_snapshot)
        assert "quota" not in json.dumps(public_snapshot)
        assert "private-provider" not in json.dumps(public_snapshot)
        assert "TimeoutError: raw upstream detail" not in json.dumps(public_snapshot)
        assert "Private Team vs Hidden Team" not in json.dumps(public_snapshot)
        assert "Internal fixture mismatch detail" not in json.dumps(public_snapshot)
        assert json.loads((out_dir / "api" / "matches.json").read_text())["matches"][0][
            "match_label"
        ] == "Mexico vs South Africa"


def test_export_static_site_manifest_describes_outputs():
    with TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "site"

        result = export_static_site(_snapshot(), out_dir)
        manifest = json.loads((out_dir / "manifest.json").read_text())

        assert manifest["schema_version"] == 1
        assert manifest["snapshot_at"] == "2026-06-08T00:00:00+00:00"
        assert "run_id" not in manifest
        assert "api/matches.json" in manifest["files"]
        assert result["manifest_path"] == str(out_dir / "manifest.json")
