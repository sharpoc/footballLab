import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.decision_settlement import settle_match_decision
from worldcup.finished_record import build_finished_block


def _closing_snapshot(
    at: str,
    *,
    competition_id: str = "fifa_world_cup_2026",
    decision: dict | None = None,
) -> dict:
    competition_name = "2026 世界杯" if competition_id == "fifa_world_cup_2026" else "中超 2026"
    if decision is None:
        decision = {
            "schema_version": 2,
            "label": "MATCH_PICK",
            "market": "1X2",
            "selection": "home",
            "line": None,
            "odds": 1.78,
            "p_hit_safe": 0.61,
            "p_no_loss_safe": 0.61,
        }
    return {
        "snapshot_at": at,
        "competition": {"id": competition_id, "name": competition_name},
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "competition": {"id": competition_id, "name": competition_name},
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "stage": "Matchday 1",
                "group": "Group A",
                "match_decision": decision,
                # Legacy payload proves the new record path never freezes grades.
                "signals": [{"grade": "S", "selection": "away", "sentinel": "must-not-leak"}],
            }
        ],
    }


def _write_results(path: Path, rows: list[dict]) -> None:
    columns = [
        "kickoff_at_utc",
        "home_team",
        "away_team",
        "home_canonical",
        "away_canonical",
        "home_score",
        "away_score",
        "captured_at",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


MEXICO_ROW = {
    "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
    "home_team": "Mexico",
    "away_team": "South Africa",
    "home_canonical": "mexico",
    "away_canonical": "south_africa",
    "home_score": "2",
    "away_score": "0",
    "captured_at": "2026-06-12T01:00:00+00:00",
}


def _build(root: Path, snapshot: dict) -> dict:
    history = root / "history"
    history.mkdir()
    (history / "snapshot_20260611T180000Z-live.json").write_text(json.dumps(snapshot))
    results = root / "results.csv"
    _write_results(results, [MEXICO_ROW])
    return build_finished_block(history, results, root / "store.json")


def test_build_finished_block_freezes_only_match_pick_and_settles_it():
    with TemporaryDirectory() as tmp:
        block = _build(Path(tmp), _closing_snapshot("2026-06-11T18:00:00+00:00"))

        assert block["schema_version"] == 2
        assert len(block["matches"]) == 1
        record = block["matches"][0]
        assert record["result"] == {"home_score": 2, "away_score": 0}
        assert record["closing_snapshot_at"] == "2026-06-11T18:00:00+00:00"
        assert record["closing_match_decision"]["label"] == "MATCH_PICK"
        assert record["closing_match_decision_result"] == {
            "status": "hit",
            "label": "命中",
            "detail": "全场 2-0",
            "settlement_class": "full_win",
        }
        assert "closing_signals" not in record
        assert "must-not-leak" not in json.dumps(block)
        assert block["decision_tally"] == {"hit": 1, "miss": 0, "push": 0, "no_pick": 0}


def test_explicit_no_pick_is_not_confused_with_missing_decision():
    no_pick = {"schema_version": 2, "label": "NO_CLEAN_MARKET"}
    with TemporaryDirectory() as tmp:
        block = _build(
            Path(tmp),
            _closing_snapshot("2026-06-11T18:00:00+00:00", decision=no_pick),
        )

        assert block["decision_tally"] == {"hit": 0, "miss": 0, "push": 0, "no_pick": 1}
        assert block["decision_coverage"]["missing_decision_count"] == 0

    missing = settle_match_decision(None, {"home_score": 2, "away_score": 0})
    assert missing["status"] == "missing_decision"


def test_quarter_handicap_preserves_half_win_and_half_loss_classes():
    result = {"home_score": 1, "away_score": 1}
    half_win = settle_match_decision(
        {
            "schema_version": 2,
            "label": "MATCH_PICK",
            "market": "AH",
            "selection": "home",
            "line": 0.25,
        },
        result,
    )
    half_loss = settle_match_decision(
        {
            "schema_version": 2,
            "label": "MATCH_PICK",
            "market": "AH",
            "selection": "home",
            "line": -0.25,
        },
        result,
    )

    assert (half_win["status"], half_win["settlement_class"]) == ("hit", "half_win")
    assert (half_loss["status"], half_loss["settlement_class"]) == ("miss", "half_loss")


def test_unknown_decision_label_is_invalid_instead_of_becoming_a_fake_hit():
    settled = settle_match_decision(
        {
            "schema_version": 2,
            "label": "TOTALLY_UNKNOWN",
            "market": "1X2",
            "selection": "home",
        },
        {"home_score": 2, "away_score": 0},
    )

    assert settled["status"] == "invalid_decision"


def test_build_finished_block_is_incremental_via_store():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        history.mkdir()
        closing = history / "snapshot_20260611T180000Z-live.json"
        closing.write_text(json.dumps(_closing_snapshot("2026-06-11T18:00:00+00:00")))
        results = root / "results.csv"
        _write_results(results, [MEXICO_ROW])
        store = root / "store.json"

        first = build_finished_block(history, results, store)
        closing.unlink()
        second = build_finished_block(history, results, store)

        assert len(second["matches"]) == 1
        assert second["decision_tally"] == first["decision_tally"]


def test_legacy_store_remains_readable_but_public_block_strips_grades():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        results = root / "results.csv"
        _write_results(results, [MEXICO_ROW])
        store = root / "store.json"
        legacy_record = {
            "kickoff_at_utc": MEXICO_ROW["kickoff_at_utc"],
            "home_team": MEXICO_ROW["home_team"],
            "away_team": MEXICO_ROW["away_team"],
            "home_canonical": MEXICO_ROW["home_canonical"],
            "away_canonical": MEXICO_ROW["away_canonical"],
            "result": {"home_score": 2, "away_score": 0},
            "closing_signals": [{"grade": "S", "sentinel": "legacy-grade"}],
            "closing_match_decision": {
                "schema_version": 1,
                "label": "HIGH_CONFIDENCE_LEAN",
                "market": "DNB",
                "selection": "home",
                "line": 0.0,
            },
        }
        store.write_text(json.dumps({"legacy": legacy_record}), encoding="utf-8")

        block = build_finished_block(root / "history", results, store)

        assert len(block["matches"]) == 1
        assert "closing_signals" not in block["matches"][0]
        assert block["matches"][0]["closing_match_decision_result"]["status"] == "hit"
        # No destructive migration: the persisted legacy payload is still present.
        assert "legacy-grade" in store.read_text(encoding="utf-8")


def test_finished_block_filters_mixed_history_by_competition():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        history.mkdir()
        (history / "snapshot_20260611T180000Z-csl.json").write_text(
            json.dumps(
                _closing_snapshot(
                    "2026-06-11T18:00:00+00:00",
                    competition_id="csl_2026",
                    decision={"label": "MATCH_PICK", "market": "1X2", "selection": "away"},
                )
            )
        )
        (history / "snapshot_20260611T175000Z-wc.json").write_text(
            json.dumps(_closing_snapshot("2026-06-11T17:50:00+00:00"))
        )
        results = root / "results.csv"
        _write_results(results, [MEXICO_ROW])

        block = build_finished_block(history, results, root / "store.json")

        assert block["matches"][0]["competition_id"] == "fifa_world_cup_2026"
        assert block["matches"][0]["closing_match_decision"]["selection"] == "home"


def test_build_finished_block_counts_missing_closing_without_fake_no_pick():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        history.mkdir()
        results = root / "results.csv"
        _write_results(results, [MEXICO_ROW])

        block = build_finished_block(history, results, root / "store.json")

        assert block["matches"] == []
        assert block["skipped_no_closing"] == 1
        assert block["decision_tally"] == {"hit": 0, "miss": 0, "push": 0, "no_pick": 0}
        assert block["decision_coverage"]["missing_closing_count"] == 1
