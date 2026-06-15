import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.odds_trend import (
    attach_trends,
    extract_match_trend,
    list_history_files,
)


def _hist_snapshot(at: str, odds_home: float, ah_line: float = -1.0, ou_line: float = 2.5) -> dict:
    return {
        "snapshot_at": at,
        "matches": [
            {
                "kickoff_at_utc": "2026-06-15T19:00:00+00:00",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "market": {
                    "1x2": {"odds": {"home": odds_home, "draw": 3.6, "away": 4.8}},
                    "ou_2_5": {"line": ou_line, "odds": {"over": 1.9, "under": 2.0}},
                    "ah_main": {
                        "line_home": ah_line,
                        "odds": {"home": 1.74, "away": 2.12},
                    },
                },
            }
        ],
    }


def test_extract_trend_keeps_only_changes_plus_first_and_last():
    snapshots = [
        _hist_snapshot("2026-06-12T00:00:00+00:00", 1.85),
        _hist_snapshot("2026-06-12T06:00:00+00:00", 1.85),  # 无变化，跳过
        _hist_snapshot("2026-06-12T12:00:00+00:00", 1.80),  # 变化，保留
        _hist_snapshot("2026-06-12T18:00:00+00:00", 1.80),  # 无变化但是最新点，保留
    ]

    trend = extract_match_trend(snapshots, "mexico", "south_africa")

    home_points = trend["1x2"]["home"]
    assert [p[1] for p in home_points] == [1.85, 1.8, 1.8]
    assert home_points[0][0] == "2026-06-12T00:00:00+00:00"
    assert home_points[-1][0] == "2026-06-12T18:00:00+00:00"
    # OU 全程无变化：只剩首点 + 最新点
    assert [p[1] for p in trend["ou_2_5"]["over"]] == [1.9, 1.9]


def test_extract_trend_records_ah_line_per_point():
    snapshots = [
        _hist_snapshot("2026-06-12T00:00:00+00:00", 1.85, ah_line=-1.0),
        _hist_snapshot("2026-06-12T12:00:00+00:00", 1.85, ah_line=-1.25),
    ]

    trend = extract_match_trend(snapshots, "mexico", "south_africa")

    ah_points = trend["ah_main"]["home"]
    assert ah_points[0][2] == -1.0
    assert ah_points[-1][2] == -1.25


def test_extract_trend_records_ou_line_per_point():
    snapshots = [
        _hist_snapshot("2026-06-12T00:00:00+00:00", 1.85, ou_line=2.5),
        _hist_snapshot("2026-06-12T12:00:00+00:00", 1.85, ou_line=3.5),
    ]

    trend = extract_match_trend(snapshots, "mexico", "south_africa")

    ou_points = trend["ou_2_5"]["over"]
    assert ou_points[0][2] == 2.5
    assert ou_points[-1][2] == 3.5


def test_extract_trend_caps_points_per_selection():
    snapshots = [
        _hist_snapshot(f"2026-06-12T{h:02d}:{m:02d}:00+00:00", 1.5 + h * 0.01 + m * 0.0001)
        for h in range(20)
        for m in (0, 30)
    ]

    trend = extract_match_trend(snapshots, "mexico", "south_africa", max_points=30)

    assert len(trend["1x2"]["home"]) == 30
    # 上限裁剪保最新：末点必须是时间最大的那轮
    assert trend["1x2"]["home"][-1][0] == "2026-06-12T19:30:00+00:00"


def test_list_history_files_filters_by_filename_window():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = root / "snapshot_20260601T000000Z-live.json"
        new = root / "snapshot_20260612T010000Z-live.json"
        raw = root / "odds_raw_20260612T010000Z-live.json.gz"
        for path in (old, new):
            path.write_text("{}")
        raw.write_text("x")

        files = list_history_files(root, since="2026-06-10T00:00:00+00:00")

        assert files == [new]


def test_attach_trends_writes_into_snapshot_matches():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for at, odds in (
            ("20260612T000000Z", 1.85),
            ("20260612T120000Z", 1.80),
        ):
            iso = f"{at[:4]}-{at[4:6]}-{at[6:8]}T{at[9:11]}:{at[11:13]}:00+00:00"
            (root / f"snapshot_{at}-live.json").write_text(
                json.dumps(_hist_snapshot(iso, odds))
            )
        snapshot = _hist_snapshot("2026-06-12T13:00:00+00:00", 1.79)

        attach_trends(snapshot, root, now="2026-06-12T13:00:00+00:00")

        points = snapshot["matches"][0]["odds_trend"]["1x2"]["home"]
        assert [p[1] for p in points] == [1.85, 1.8]
