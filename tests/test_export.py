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
            "enrichment_errors": [
                {"source": "site_enrichment", "error": "ValueError: private enrichment detail"}
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
        "finished": {
            "matches": [
                {
                    "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                    "home_team": "Mexico",
                    "away_team": "South Africa",
                    "home_canonical": "mexico",
                    "away_canonical": "south_africa",
                    "stage": "Matchday 1",
                    "group": "Group A",
                    "result": {"home_score": 2, "away_score": 0},
                    "closing_snapshot_at": "2026-06-11T18:45:00+00:00",
                    "closing_signals": [
                        {
                            "market_type": "1X2_90min",
                            "selection": "home",
                            "line": None,
                            "grade": "S",
                            "odds": 1.78,
                            "prediction": {"status": "hit", "label": "命中", "detail": "全场 2-0"},
                        }
                    ],
                }
            ],
            "tally": {"S": {"hit": 1, "miss": 0, "push": 0}},
            "skipped_no_closing": 0,
        },
    }


def test_export_static_site_writes_html_snapshot_and_matches_json():
    with TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "site"

        result = export_static_site(_snapshot(), out_dir)

        assert result["index_path"] == str(out_dir / "index.html")
        assert (out_dir / "index.html").exists()
        assert (out_dir / "api" / "snapshot" / "latest.json").exists()
        assert (out_dir / "api" / "matches.json").exists()
        assert (out_dir / "api" / "finished.json").exists()
        html = (out_dir / "index.html").read_text(encoding="utf-8")
        assert "研究台账" in html
        assert "仅用于研究分析，不构成投注建议。" in html
        assert "Research Ledger" not in html
        assert "Research only, not betting advice." not in html
        assert "stake" not in html.lower()
        assert "bet amount" not in html.lower()
        assert "墨西哥 对 南非" in html
        public_snapshot = json.loads((out_dir / "api" / "snapshot" / "latest.json").read_text())[
            "snapshot"
        ]
        assert public_snapshot["snapshot_at"] == "2026-06-08T00:00:00+00:00"
        assert public_snapshot["matches"][0]["match_label"] == "Mexico vs South Africa"
        assert public_snapshot["data_quality"]["source_error_count"] == 1
        assert public_snapshot["data_quality"]["stale_source_count"] == 1
        assert public_snapshot["data_quality"]["enrichment_error_count"] == 1
        assert public_snapshot["finished"]["summary"]["match_count"] == 1
        assert public_snapshot["finished"]["matches"][0]["score_label"] == "2 - 0"
        assert "run" not in public_snapshot
        assert "run_id" not in json.dumps(public_snapshot)
        assert "quota" not in json.dumps(public_snapshot)
        assert "private-provider" not in json.dumps(public_snapshot)
        assert "TimeoutError: raw upstream detail" not in json.dumps(public_snapshot)
        assert "private enrichment detail" not in json.dumps(public_snapshot)
        assert "Private Team vs Hidden Team" not in json.dumps(public_snapshot)
        assert "Internal fixture mismatch detail" not in json.dumps(public_snapshot)
        assert json.loads((out_dir / "api" / "matches.json").read_text())["matches"][0][
            "match_label"
        ] == "Mexico vs South Africa"
        finished = json.loads((out_dir / "api" / "finished.json").read_text())["finished"]
        assert finished["summary"]["tally"]["S"] == {"hit": 1, "miss": 0, "push": 0}
        assert finished["matches"][0]["match_label"] == "Mexico vs South Africa"


def test_export_static_site_manifest_describes_outputs():
    with TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "site"

        result = export_static_site(_snapshot(), out_dir)
        manifest = json.loads((out_dir / "manifest.json").read_text())

        assert manifest["schema_version"] == 1
        assert manifest["snapshot_at"] == "2026-06-08T00:00:00+00:00"
        assert "run_id" not in manifest
        assert "api/matches.json" in manifest["files"]
        assert "api/finished.json" in manifest["files"]
        assert result["manifest_path"] == str(out_dir / "manifest.json")
