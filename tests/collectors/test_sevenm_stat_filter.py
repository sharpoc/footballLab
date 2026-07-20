"""Tests for Stat_Arr allowlist in parse_sevenm_fixture_result_rows.

Only Stat=4 (normalized integer) + valid score should produce finished rows.
All other Stat values must be fail-closed skipped.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from worldcup.collectors.csl_result_sources import parse_sevenm_fixture_result_rows


def _fixture_js(stat_arr: str, scores_arr: str, count: int | None = None) -> str:
    """Build minimal fixture JS with given Stat_Arr and Scores_Arr."""
    if count is None:
        count = len(stat_arr.split(","))
    ids = ",".join(str(7000 + i) for i in range(count))
    runs = ",".join(["17"] * count)
    times = ",".join(['"2026,07,05,19,35,00"'] * count)
    teams_a = ",".join(['"辽宁铁人"'] * count)
    teams_b = ",".join(['"浙江队"'] * count)
    return f"""
    var Tmp_bh_Arr = [ {ids} ];
    var Run_Arr = [ {runs} ];
    var Time_Arr = [ {times} ];
    var Scores_Arr = [ {scores_arr} ];
    var TeamA_Arr = [ {teams_a} ];
    var TeamB_Arr = [ {teams_b} ];
    var Stat_Arr = [ {stat_arr} ];
    """


def _parse(js: str) -> list[dict[str, str]]:
    return parse_sevenm_fixture_result_rows(
        js, season="2026", source_url="https://test.invalid"
    )


# --- Accept: Stat=4 + valid score ---

def test_stat_4_with_valid_score_accepted():
    rows = _parse(_fixture_js('4', '"1-2(0-1)"', count=1))
    assert len(rows) == 1
    assert rows[0]["status"] == "finished"
    assert rows[0]["home_score"] == "1"
    assert rows[0]["away_score"] == "2"


def test_stat_4_integer_with_valid_score_accepted():
    """Stat value as JS integer (not quoted string)."""
    rows = _parse(_fixture_js('4', '"3-0(2-0)"', count=1))
    assert len(rows) == 1
    assert rows[0]["status"] == "finished"


# --- Reject: Stat=4 but no valid score ---

def test_stat_4_no_score_rejected():
    rows = _parse(_fixture_js('4', '"VS"', count=1))
    assert len(rows) == 0


# --- Reject: Stat=17 with score (in-progress with temp score) ---

def test_stat_17_with_score_rejected():
    rows = _parse(_fixture_js('17', '"1-0(1-0)"', count=1))
    assert len(rows) == 0


# --- Reject: Stat=13 with score (postponed with residual score) ---

def test_stat_13_with_score_rejected():
    rows = _parse(_fixture_js('13', '"2-1(1-0)"', count=1))
    assert len(rows) == 0


# --- Reject: unknown stat value with valid score ---

def test_unknown_stat_value_with_score_rejected():
    rows = _parse(_fixture_js('99', '"2-0(1-0)"', count=1))
    assert len(rows) == 0


def test_stat_0_with_score_rejected():
    rows = _parse(_fixture_js('0', '"1-1(0-0)"', count=1))
    assert len(rows) == 0


def test_stat_1_with_score_rejected():
    """Stat=1 (possibly in-progress) must be rejected."""
    rows = _parse(_fixture_js('1', '"1-0(1-0)"', count=1))
    assert len(rows) == 0


# --- Reject: empty/null/missing Stat ---

def test_stat_empty_string_with_score_rejected():
    rows = _parse(_fixture_js('""', '"2-1(1-0)"', count=1))
    assert len(rows) == 0


def test_stat_none_with_score_rejected():
    """Stat as empty string (parsed from JS empty value)."""
    js = """
    var Tmp_bh_Arr = [ 7001 ];
    var Run_Arr = [ 17 ];
    var Time_Arr = [ "2026,07,05,19,35,00" ];
    var Scores_Arr = [ "1-0(0-0)" ];
    var TeamA_Arr = [ "辽宁铁人" ];
    var TeamB_Arr = [ "浙江队" ];
    var Stat_Arr = [ "" ];
    """
    rows = _parse(js)
    assert len(rows) == 0


# --- Multi-row: only Stat=4 + score rows pass ---

def test_multi_row_only_stat_4_with_score_pass():
    js = _fixture_js(
        '4, 17, 13, 4, 99',
        '"3-1(2-0)", "1-0(1-0)", "VS", "0-2(0-1)", "4-4(2-2)"',
        count=5,
    )
    rows = _parse(js)
    assert len(rows) == 2
    assert rows[0]["home_score"] == "3"
    assert rows[0]["away_score"] == "1"
    assert rows[1]["home_score"] == "0"
    assert rows[1]["away_score"] == "2"


# --- Existing probe sample regression ---

def test_existing_2026_probe_output_unchanged():
    """2026 probe has 120x Stat=4 with scores + 120x Stat=17 with VS.
    Parser should still return exactly 120 finished rows."""
    probe_path = Path(__file__).resolve().parent.parent.parent / "data" / "probe" / "csl_results_sevenm_2026_fixture.js"
    if not probe_path.exists():
        return  # skip if probe not available
    source = probe_path.read_text(encoding="utf-8")
    rows = parse_sevenm_fixture_result_rows(
        source, season="2026", source_url="probe"
    )
    assert len(rows) == 120
    assert all(r["status"] == "finished" for r in rows)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import inspect

    failures = 0
    count = 0
    for name, fn in sorted(inspect.getmembers(sys.modules[__name__], inspect.isfunction)):
        if not name.startswith("test_"):
            continue
        count += 1
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"PASS {name}")
    print(f"\n{count - failures}/{count} passed")
    raise SystemExit(1 if failures else 0)
