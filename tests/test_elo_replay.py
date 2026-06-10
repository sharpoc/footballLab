import math

from worldcup.elo_replay import goal_index, k_factor, update_pair


def test_k_factor_by_tournament_class():
    assert k_factor("FIFA World Cup") == 60.0
    assert k_factor("Copa América") == 50.0
    assert k_factor("UEFA Euro") == 50.0
    assert k_factor("FIFA World Cup qualification") == 40.0
    assert k_factor("UEFA Euro qualification") == 40.0
    assert k_factor("UEFA Nations League") == 40.0
    assert k_factor("CONCACAF Nations League") == 40.0
    assert k_factor("Friendly") == 20.0
    assert k_factor("King's Cup") == 30.0


def test_goal_index_margins():
    assert goal_index(0) == 1.0
    assert goal_index(1) == 1.0
    assert goal_index(-1) == 1.0
    assert goal_index(2) == 1.5
    assert goal_index(-2) == 1.5
    assert math.isclose(goal_index(3), (11 + 3) / 8)
    assert math.isclose(goal_index(-5), (11 + 5) / 8)


def test_update_pair_friendly_home_win_uses_home_advantage():
    from worldcup.engine.elo import expected_score

    rh, ra = update_pair(1500.0, 1500.0, 1, 0, k=20.0, neutral=False)
    delta = 20.0 * 1.0 * (1.0 - expected_score(100.0))
    assert math.isclose(rh, 1500.0 + delta)
    assert math.isclose(ra, 1500.0 - delta)


def test_update_pair_neutral_draw_between_equals_is_noop():
    rh, ra = update_pair(1600.0, 1600.0, 1, 1, k=40.0, neutral=True)
    assert math.isclose(rh, 1600.0)
    assert math.isclose(ra, 1600.0)


def test_update_pair_is_zero_sum():
    rh, ra = update_pair(1700.0, 1450.0, 0, 3, k=50.0, neutral=True)
    assert math.isclose((rh + ra), 1700.0 + 1450.0)
    assert rh < 1700.0 and ra > 1450.0


def test_load_results_skips_na_scores_and_parses_neutral():
    import tempfile

    from worldcup.elo_replay import load_results

    content = (
        "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n"
        "2024-01-01,Alpha,Beta,2,1,Friendly,Town,Alpha,FALSE\n"
        "2026-06-11,Alpha,Beta,NA,NA,FIFA World Cup,Town,Gamma,TRUE\n"
        "2024-02-01,Alpha,Gamma,0,0,FIFA World Cup qualification,Town,Gamma,TRUE\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write(content)
        path = fh.name
    matches = load_results(path)
    assert len(matches) == 2
    assert matches[0].home_team == "Alpha" and matches[0].home_score == 2
    assert matches[0].neutral is False
    assert matches[1].neutral is True


def test_replay_orders_by_date_and_tracks_pre_match_ratings():
    from worldcup.elo_replay import ReplayMatch, replay, update_pair

    later = ReplayMatch("2024-02-01", "Alpha", "Beta", 1, 1, "Friendly", True)
    earlier = ReplayMatch("2024-01-01", "Alpha", "Beta", 3, 0, "Friendly", True)
    replayed, ratings = replay([later, earlier])

    first_match, rh1, ra1 = replayed[0]
    assert first_match.date == "2024-01-01"
    assert rh1 == 1500.0 and ra1 == 1500.0

    expected_rh, expected_ra = update_pair(1500.0, 1500.0, 3, 0, k=20.0, neutral=True)
    second_match, rh2, ra2 = replayed[1]
    assert second_match.date == "2024-02-01"
    assert math.isclose(rh2, expected_rh)
    assert math.isclose(ra2, expected_ra)
    assert set(ratings) == {"Alpha", "Beta"}


def test_replay_smoke_on_probe_dataset():
    from pathlib import Path as _Path

    probe = _Path(__file__).resolve().parent.parent / "data" / "probe" / "intl_results_martj42.csv"
    if not probe.exists():
        return
    from worldcup.elo_replay import load_results, replay

    matches = load_results(probe)
    assert len(matches) > 40000
    replayed, ratings = replay(matches)
    assert len(replayed) == len(matches)
    assert len(ratings) > 250
