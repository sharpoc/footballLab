import math

from worldcup.engine.handicap import diff_distribution, ev_handicap


def simple_matrix():
    return [[0.5, 0.0], [0.5, 0.0]]


def test_diff_distribution():
    dist = diff_distribution(simple_matrix())
    assert math.isclose(dist[1], 0.5)
    assert math.isclose(dist[0], 0.5)


def test_integer_line_push():
    dist = diff_distribution(simple_matrix())
    assert math.isclose(ev_handicap(dist, line=0.0, odds=2.0), 0.5)


def test_half_line_no_push():
    dist = diff_distribution(simple_matrix())
    assert math.isclose(ev_handicap(dist, line=-0.5, odds=2.0), 0.0)


def test_quarter_line_minus_025():
    dist = diff_distribution(simple_matrix())
    assert math.isclose(ev_handicap(dist, line=-0.25, odds=2.0), 0.25)


def test_quarter_line_minus_075():
    dist = diff_distribution(simple_matrix())
    assert math.isclose(ev_handicap(dist, line=-0.75, odds=2.0), -0.25)
