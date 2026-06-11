import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.collectors.eloratings import parse_elo_ratings
from worldcup.elo_local import (
    compute_updated_world_tsv,
    freeze_baseline,
    load_baseline,
)

WORLD_TSV = "1\t1\tMX\t1875\n2\t2\tZA\t1700\n3\t3\tCA\t1810\n"
TEAMS_TSV = "MX\tMexico\nZA\tSouth Africa\nCA\tCanada\n"
OPENFOOTBALL = {
    "matches": [
        {
            "round": "Matchday 1",
            "date": "2026-06-11",
            "time": "13:00 UTC-6",
            "team1": "Mexico",
            "team2": "South Africa",
            "ground": "Mexico City",
            "score1": 2,
            "score2": 0,
        },
        {
            "round": "Matchday 1",
            "date": "2026-06-12",
            "time": "13:00 UTC-6",
            "team1": "Canada",
            "team2": "Mexico",
            "ground": "Toronto",
        },
    ]
}


def _seed_cache(cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "elo_world.tsv").write_text(WORLD_TSV)
    (cache / "elo_teams.tsv").write_text(TEAMS_TSV)
    (cache / "openfootball_2026.json").write_text(json.dumps(OPENFOOTBALL))


def test_freeze_and_load_baseline_roundtrip():
    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)

        meta = freeze_baseline(cache, baseline_at="2026-06-01T00:00:00+00:00")

        assert meta["baseline_at"] == "2026-06-01T00:00:00+00:00"
        ratings, aliases, baseline_at = load_baseline(cache)
        assert ratings["MX"].rating == 1875
        assert aliases["Mexico"] == "MX"
        assert baseline_at == "2026-06-01T00:00:00+00:00"


def test_compute_applies_finished_results_after_baseline():
    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)
        freeze_baseline(cache, baseline_at="2026-06-01T00:00:00+00:00")

        out = compute_updated_world_tsv(cache, min_rows=2)
        updated = parse_elo_ratings(out)

        assert updated["MX"].rating > 1875
        assert updated["ZA"].rating < 1700
        assert updated["CA"].rating == 1810
        assert updated["MX"].rating - 1875 == 1700 - updated["ZA"].rating
        assert updated["MX"].rank == 1


def test_compute_skips_results_before_baseline():
    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)
        freeze_baseline(cache, baseline_at="2026-06-12T00:00:00+00:00")

        out = compute_updated_world_tsv(cache, min_rows=2)
        updated = parse_elo_ratings(out)

        assert updated["MX"].rating == 1875
        assert updated["ZA"].rating == 1700


def test_compute_rejects_too_few_rows():
    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)
        freeze_baseline(cache, baseline_at="2026-06-01T00:00:00+00:00")

        try:
            compute_updated_world_tsv(cache, min_rows=200)
        except ValueError as exc:
            assert "rows" in str(exc)
        else:
            raise AssertionError("expected ValueError for too few rows")


def test_load_baseline_raises_when_missing():
    with TemporaryDirectory() as tmp:
        try:
            load_baseline(Path(tmp))
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("expected FileNotFoundError")


def test_cli_check_reports_baseline_and_pending_results(capsys=None):
    from worldcup.elo_local import main

    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)
        freeze_baseline(cache, baseline_at="2026-06-01T00:00:00+00:00")

        code = main(["--cache-dir", str(cache), "--check"])

        assert code == 0


def test_cli_freeze_writes_baseline_files():
    from worldcup.elo_local import main

    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)

        code = main(
            ["--cache-dir", str(cache), "--freeze", "--baseline-at", "2026-06-01T00:00:00+00:00"]
        )

        assert code == 0
        ratings, _aliases, baseline_at = load_baseline(cache)
        assert ratings["MX"].rating == 1875
        assert baseline_at == "2026-06-01T00:00:00+00:00"
