import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.export import export_static_site


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {"run_id": "run-1"},
        "counts": {"matches": 1},
        "data_quality": {"stale_sources": [], "source_errors": []},
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
        assert manifest["run_id"] == "run-1"
        assert "api/matches.json" in manifest["files"]
        assert result["manifest_path"] == str(out_dir / "manifest.json")
