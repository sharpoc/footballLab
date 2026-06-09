from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.preview import build_preview_html, write_preview


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {"run_id": "20260608T000000Z-live"},
        "counts": {"fixtures": 104, "matches": 1},
        "data_quality": {
            "stale_sources": ["theoddsapi"],
            "source_errors": [{"source": "theoddsapi", "error": "TimeoutError"}],
            "missing_odds": [],
            "missing_elo": [],
            "time_mismatches": ["Brazil vs Haiti"],
        },
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "stage": "Matchday 1",
                "group": "Group A",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "signals": [{"grade": "A"}, {"grade": "B"}],
            }
        ],
    }


def test_build_preview_html_contains_disclaimer_quality_and_match_rows():
    html = build_preview_html(_snapshot())

    assert "世界杯足彩分析站" in html
    assert "研究分析工具，不构成投注建议" in html
    assert "20260608T000000Z-live" in html
    assert "theoddsapi" in html
    assert "Brazil vs Haiti" in html
    assert "Mexico vs South Africa" in html
    assert "Matchday 1" in html
    assert "下注金额" not in html
    assert "stake" not in html.lower()
    assert "bet amount" not in html.lower()


def test_write_preview_creates_parent_directory_and_file():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "nested" / "preview.html"

        write_preview(_snapshot(), out)

        assert out.exists()
        assert "Mexico vs South Africa" in out.read_text(encoding="utf-8")
