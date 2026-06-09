from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.preview import build_preview_html, write_preview


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {
            "run_id": "20260608T000000Z-live",
            "quota": {"theoddsapi": {"remaining": 494, "used": 6}},
            "stale_sources": ["theoddsapi"],
            "source_errors": [],
        },
        "counts": {"fixtures": 104, "matches": 1, "odds_events": 1},
        "data_quality": {
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
                "model": {"combined_1x2": {"home": 0.61, "draw": 0.23, "away": 0.16}},
                "market": {"1x2": {"market_probs": {"home": 0.57, "draw": 0.25, "away": 0.18}}},
                "signals": [
                    {
                        "market_type": "1X2_90min",
                        "selection": "home",
                        "grade": "A",
                        "ev": 0.052,
                        "edge": 0.041,
                        "status": "OK",
                    }
                ],
            }
        ],
    }


def test_build_preview_html_renders_research_ledger_surface():
    html = build_preview_html(_snapshot())

    assert "World Cup 2026" in html
    assert "Research Ledger" in html
    assert "Research only, not betting advice." in html
    assert "研究分析工具，不构成投注建议" in html
    assert "Mexico vs South Africa" in html
    assert "1X2 - Home" in html
    assert "+4.1%" in html
    assert "Model probability is above the devigged market probability." in html
    assert "Methodology" in html
    assert "Source Health" in html
    assert "Caveats" in html
    assert "下注金额" not in html
    assert "stake" not in html.lower()
    assert "bet amount" not in html.lower()
    assert "bankroll" not in html.lower()


def test_write_preview_creates_parent_directory_and_file():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "nested" / "preview.html"

        write_preview(_snapshot(), out)

        assert out.exists()
        assert "Research Ledger" in out.read_text(encoding="utf-8")


def test_build_preview_html_does_not_expose_raw_operational_details():
    snapshot = _snapshot()
    snapshot["run"] = {
        "run_id": "internal-run-abc-999",
        "quota": {"ultra-private-feed": {"remaining": 777777, "used": 123456}},
        "stale_sources": ["ultra-private-feed"],
        "source_errors": [
            {"source": "ultra-private-feed", "error": "TimeoutError: secret-ish upstream detail"}
        ],
    }
    snapshot["data_quality"] = {
        "source_errors": [
            {"source": "ultra-private-feed", "error": "TimeoutError: secret-ish upstream detail"}
        ],
        "missing_odds": ["Private Team vs Hidden Team"],
        "missing_elo": [],
        "time_mismatches": ["Internal fixture mismatch detail"],
    }

    html = build_preview_html(snapshot)

    assert "Source Health" in html
    assert "Data quality: ATTENTION" in html
    assert "Odds feed: Needs attention" in html
    assert "Fixtures: Available" in html
    assert "Elo ratings: Available" in html
    assert "Input checks: Needs attention" in html
    assert "Missing odds: 1" in html
    assert "Time checks: 1" in html
    assert "internal-run-abc-999" not in html
    assert "777777" not in html
    assert "123456" not in html
    assert "ultra-private-feed" not in html
    assert "TimeoutError: secret-ish upstream detail" not in html
    assert "Private Team vs Hidden Team" not in html
    assert "Internal fixture mismatch detail" not in html


def test_build_preview_html_escapes_dynamic_values():
    snapshot = _snapshot()
    snapshot["matches"][0]["home_team"] = 'Mexico <script>alert("x")</script>'
    snapshot["matches"][0]["away_team"] = 'South Africa" data-break="1'
    snapshot["matches"][0]["stage"] = '<img src=x onerror="alert(1)">'
    snapshot["matches"][0]["group"] = 'Group A"><script>alert(2)</script>'
    snapshot["run"] = {
        "run_id": "run-x",
        "source_errors": [
            {"source": 'feed"><script>alert(3)</script>', "error": '<script>alert(4)</script>'}
        ],
    }

    html = build_preview_html(snapshot)
    lower_html = html.lower()

    assert "<img" not in lower_html
    assert 'Mexico <script>alert("x")</script>' not in html
    assert 'Group A"><script>alert(2)</script>' not in html
    assert 'feed"><script>alert(3)</script>' not in html
    assert "<script>alert(4)</script>" not in html
    assert 'data-break="1' not in html
    assert 'onerror="alert(1)"' not in html
    assert "Mexico &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert "South Africa&quot; data-break=&quot;1" in html


def test_build_preview_html_renders_empty_signal_state():
    snapshot = _snapshot()
    snapshot["matches"][0]["signals"] = []

    html = build_preview_html(snapshot)

    assert "No research signals" in html


def test_build_preview_html_includes_filter_dom_accessibility_contract():
    html = build_preview_html(_snapshot())

    assert 'data-filter="strong"' in html
    assert 'id="ledger-search"' in html
    assert 'aria-pressed="true"' in html
    assert 'aria-pressed="false"' in html
    assert "<caption>Research signal ledger</caption>" in html
    assert '<th scope="col">Matchup</th>' in html
