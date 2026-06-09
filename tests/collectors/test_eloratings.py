from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases


def test_parse_elo_ratings_uses_rank_code_and_rating_columns():
    ratings = parse_elo_ratings("1\t1\tES\t2155\n2\t2\tAR\t2114\n")
    assert ratings["ES"].rank == 1
    assert ratings["ES"].rating == 2155
    assert ratings["AR"].rating == 2114


def test_parse_elo_team_aliases_reads_all_name_columns():
    aliases = parse_elo_team_aliases("CZ\tCzechia\nUS\tUnited States\tUSA\n")
    assert aliases["Czechia"] == "CZ"
    assert aliases["USA"] == "US"
