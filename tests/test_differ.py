from worldcup.differ import diff_rounds

CFG = {"ev_change": 0.03, "odds_move": 0.05}


def sel(grade, ev, odds):
    return {"grade": grade, "ev": ev, "odds": odds}


def test_grade_change_detected():
    prev = {"m1": {"selections": {"1X2_90min|home": sel("C", 0.01, 2.0)}}}
    curr = {"m1": {"selections": {"1X2_90min|home": sel("S", 0.09, 1.9)}}}
    events = diff_rounds(prev, curr, CFG)
    assert "grade_change" in {e["type"] for e in events}


def test_small_ev_change_ignored():
    prev = {"m1": {"selections": {"1X2_90min|home": sel("B", 0.030, 2.0)}}}
    curr = {"m1": {"selections": {"1X2_90min|home": sel("B", 0.031, 2.0)}}}
    events = diff_rounds(prev, curr, CFG)
    assert all(e["type"] != "ev_change" for e in events)


def test_large_ev_change_detected():
    prev = {"m1": {"selections": {"1X2_90min|home": sel("B", 0.03, 2.0)}}}
    curr = {"m1": {"selections": {"1X2_90min|home": sel("A", 0.07, 2.0)}}}
    events = diff_rounds(prev, curr, CFG)
    assert any(e["type"] == "ev_change" for e in events)


def test_odds_move_detected():
    prev = {"m1": {"selections": {"1X2_90min|home": sel("B", 0.03, 2.00)}}}
    curr = {"m1": {"selections": {"1X2_90min|home": sel("B", 0.03, 2.20)}}}
    events = diff_rounds(prev, curr, CFG)
    assert any(e["type"] == "odds_move" for e in events)


def test_match_added_and_removed():
    prev = {"m1": {"selections": {}}}
    curr = {"m2": {"selections": {}}}
    events = diff_rounds(prev, curr, CFG)
    types = {e["type"] for e in events}
    assert "match_added" in types and "match_removed" in types
