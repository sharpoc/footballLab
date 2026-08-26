import hashlib
import json

from worldcup.league_postmatch import build_league_postmatch
from worldcup.league_statistics import build_league_statistics, crossed_evaluation_thresholds


def _block(competition_id: str, event_id: str, *, selection: str = "home") -> dict:
    row = {
        "competition_id": competition_id, "source_event_id": event_id,
        "kickoff_at_utc": "2026-08-24T18:00:00+00:00",
        "home_team": "Home FC", "away_team": "Away FC",
        "home_canonical": "home_fc", "away_canonical": "away_fc",
        "home_score": 2, "away_score": 0,
        "captured_at": "2026-08-24T20:00:00+00:00",
        "result_scope": "football_90min", "source_fingerprint": "a" * 64,
    }
    core = {"schema_version": 1, "competition_id": competition_id, "results": [row]}
    encoded = json.dumps(core, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    receipt = {**core, "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}
    closing = {
        "schema_version": 1, "competition_id": competition_id,
        "closings": {event_id: {
            "competition_id": competition_id, "source_event_id": event_id,
            "kickoff_at_utc": "2026-08-24T18:00:00+00:00",
            "home_team": "Home FC", "away_team": "Away FC",
            "home_canonical": "home_fc", "away_canonical": "away_fc",
            "closing_snapshot_at": "2026-08-24T17:59:00+00:00",
            "closing_match_decision": {
                "schema_version": 2, "label": "MATCH_PICK", "market": "1X2", "selection": selection,
            },
        }},
    }
    return build_league_postmatch(closing, receipt, competition_id)


def test_statistics_exclude_csl_and_legacy_from_six_league_aggregate():
    report = build_league_statistics([
        _block("epl_2026_27", "epl-1"),
        _block("serie_a_2026_27", "serie-a-1"),
    ])
    assert report["aggregate"]["decision_tally"] == {"hit": 2, "miss": 0, "push": 0, "no_pick": 0}
    assert report["aggregate"]["decision_sample"]["hit_rate"] == 1.0
    assert "csl_2026" not in report["competitions"]


def test_thresholds_report_every_crossed_unsent_boundary_once():
    """Dropping a crossed unsent boundary would suppress the required offline review signal."""
    assert crossed_evaluation_thresholds(19, 101, {50}) == [20, 100]
    assert crossed_evaluation_thresholds(101, 101, set()) == []


def test_statistics_recompute_from_valid_records_and_exclude_tampered_partition():
    """Caller-supplied tallies or an embedded cross-league record must not enter formal aggregate results."""
    valid = _block("epl_2026_27", "epl-1")
    stale_totals = json.loads(json.dumps(valid))
    stale_totals["decision_tally"] = {"hit": 999, "miss": 0, "push": 0, "no_pick": 0}
    stale_totals["decision_coverage"]["finished_result_count"] = 999
    cross_league = _block("serie_a_2026_27", "serie-a-1")
    cross_league["matches"][0]["competition_id"] = "laliga_2026_27"

    report = build_league_statistics([stale_totals, cross_league])

    assert report["aggregate"]["decision_tally"] == {"hit": 1, "miss": 0, "push": 0, "no_pick": 0}
    assert report["aggregate"]["decision_coverage"]["finished_result_count"] == 1
    assert report["excluded_competitions"] == {"serie_a_2026_27": "postmatch_invalid"}


def test_statistics_never_readds_a_competition_after_the_second_duplicate_block():
    """A third duplicate must not undo the second block's fail-closed exclusion."""
    blocks = [
        _block("epl_2026_27", "epl-1"),
        _block("epl_2026_27", "epl-2"),
        _block("epl_2026_27", "epl-3"),
    ]

    forward = build_league_statistics(blocks)
    reverse = build_league_statistics(list(reversed(blocks)))

    for report in (forward, reverse):
        assert report["competitions"] == {}
        assert report["excluded_competitions"] == {"epl_2026_27": "postmatch_duplicate"}
        assert report["aggregate"]["decision_tally"] == {"hit": 0, "miss": 0, "push": 0, "no_pick": 0}
