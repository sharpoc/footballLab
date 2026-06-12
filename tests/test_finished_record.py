import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.finished_record import build_finished_block


def _closing_snapshot(at: str) -> dict:
    return {
        "snapshot_at": at,
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "stage": "Matchday 1",
                "group": "Group A",
                "market": {
                    "1x2": {"odds": {"home": 1.78, "draw": 3.6, "away": 4.8}},
                    "ou_2_5": {"odds": {"over": 1.9, "under": 2.0}},
                    "ah_main": {"line_home": -1.0, "odds": {"home": 1.74, "away": 2.12}},
                },
                "signals": [
                    {"market_type": "1X2_90min", "selection": "home", "grade": "S", "line": None},
                    {
                        "market_type": "AsianHandicap_90min",
                        "selection": "home_-2.0",
                        "grade": "A",
                        "line": -2.0,
                    },
                    {"market_type": "1X2_90min", "selection": "away", "grade": "C", "line": None},
                ],
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


def test_build_finished_block_freezes_closing_and_tallies_sa():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        history.mkdir()
        (history / "snapshot_20260611T180000Z-live.json").write_text(
            json.dumps(_closing_snapshot("2026-06-11T18:00:00+00:00"))
        )
        results = root / "results.csv"
        _write_results(results, [MEXICO_ROW])

        block = build_finished_block(history, results, root / "store.json")

        assert len(block["matches"]) == 1
        record = block["matches"][0]
        assert record["result"] == {"home_score": 2, "away_score": 0}
        assert record["closing_snapshot_at"] == "2026-06-11T18:00:00+00:00"
        # 2-0 主胜：S 级主胜命中；A 级让球 home -2.0 净胜恰好 2 球走水
        by_grade = {s["grade"]: s for s in record["closing_signals"] if s["grade"] in ("S", "A")}
        assert by_grade["S"]["prediction"]["label"] == "命中"
        assert by_grade["A"]["prediction"]["label"] == "走水"
        assert block["tally"]["S"] == {"hit": 1, "miss": 0, "push": 0}
        assert block["tally"]["A"] == {"hit": 0, "miss": 0, "push": 1}
        # C 级信号保留在明细但不进 tally
        assert any(s["grade"] == "C" for s in record["closing_signals"])
        # closing 赔率从 market 块解析
        assert by_grade["S"]["odds"] == 1.78


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
        # 删掉 closing 归档：若第二次重算会丢失记录；增量 store 必须保住
        closing.unlink()
        second = build_finished_block(history, results, store)

        assert len(second["matches"]) == 1
        assert second["tally"] == first["tally"]


def test_build_finished_block_counts_missing_closing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        history.mkdir()  # 没有任何归档
        results = root / "results.csv"
        _write_results(results, [MEXICO_ROW])

        block = build_finished_block(history, results, root / "store.json")

        assert block["matches"] == []
        assert block["skipped_no_closing"] == 1
        assert block["tally"]["S"] == {"hit": 0, "miss": 0, "push": 0}
