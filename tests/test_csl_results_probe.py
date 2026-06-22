import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.csl_results_probe import main, read_sample_rows


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "season",
        "round",
        "date",
        "kickoff_time_local",
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "neutral",
        "status",
        "source_match_id",
        "source_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _sample_row(**overrides):
    row = {
        "season": "2026",
        "round": "1",
        "date": "2026-03-01",
        "kickoff_time_local": "19:35",
        "home_team": "Shanghai Port",
        "away_team": "Shandong Taishan",
        "home_score": "2",
        "away_score": "0",
        "neutral": "0",
        "status": "finished",
        "source_match_id": "m1",
        "source_url": "https://example.invalid/m1",
    }
    row.update(overrides)
    return row


def test_read_sample_rows_supports_csv_and_json_files():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        csv_path = root / "sample.csv"
        json_path = root / "sample.json"
        _write_csv(csv_path, [_sample_row()])
        json_path.write_text(json.dumps([_sample_row(home_score="1")]), encoding="utf-8")

        assert read_sample_rows(csv_path)[0]["home_score"] == "2"
        assert read_sample_rows(json_path)[0]["home_score"] == "1"


def test_probe_main_writes_diagnostics_and_candidate_without_network():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        primary_path = root / "primary.csv"
        check_path = root / "check.csv"
        output_path = root / "diagnostics.json"
        replay_path = root / "candidate.csv"
        primary_rows = [
            _sample_row(season="2023", date="2023-04-01", source_match_id="m2023"),
            _sample_row(season="2024", date="2024-04-01", home_team="Shanghai Port", away_team="Beijing Guoan", home_score="1", away_score="0", source_match_id="m2024"),
            _sample_row(season="2025", date="2025-04-01", home_team="Shanghai Shenhua", away_team="Beijing Guoan", home_score="1", away_score="1", source_match_id="m2025"),
            _sample_row(season="2026", date="2026-03-01", source_match_id="m2026"),
        ]
        check_rows = [
            _sample_row(season="2023", date="2023-04-01", source_match_id="c2023"),
            _sample_row(season="2024", date="2024-04-01", home_team="Shanghai Port", away_team="Beijing Guoan", home_score="1", away_score="0", source_match_id="c2024"),
            _sample_row(season="2025", date="2025-04-01", home_team="Shanghai Shenhua", away_team="Beijing Guoan", home_score="1", away_score="1", source_match_id="c2025"),
            _sample_row(season="2026", date="2026-03-01", source_match_id="c2026"),
        ]
        _write_csv(primary_path, primary_rows)
        _write_csv(check_path, check_rows)

        code = main(
            [
                "--competition",
                "csl_2026",
                "--primary-source-id",
                "primary",
                "--primary-sample",
                str(primary_path),
                "--check-source-id",
                "check",
                "--check-sample",
                str(check_path),
                "--output",
                str(output_path),
                "--write-replay-candidate",
                str(replay_path),
                "--min-valid-matches",
                "4",
            ]
        )

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        assert code == 0
        assert payload["competition_id"] == "csl_2026"
        assert payload["coverage"]["valid_finished_matches"] == 4
        assert payload["pending_gate"]["can_enter_replay"] is True
        assert payload["pending_gate"]["can_lift_club_rating_pending"] is False
        assert replay_path.exists()
        assert "INGEST_HMAC_SECRET" not in output_path.read_text(encoding="utf-8")


def test_probe_main_does_not_write_replay_candidate_when_gate_fails():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        primary_path = root / "primary.csv"
        check_path = root / "check.csv"
        output_path = root / "diagnostics.json"
        replay_path = root / "candidate.csv"
        _write_csv(primary_path, [_sample_row()])
        _write_csv(check_path, [_sample_row(home_score="1")])

        code = main(
            [
                "--competition",
                "csl_2026",
                "--primary-source-id",
                "primary",
                "--primary-sample",
                str(primary_path),
                "--check-source-id",
                "check",
                "--check-sample",
                str(check_path),
                "--output",
                str(output_path),
                "--write-replay-candidate",
                str(replay_path),
            ]
        )

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        assert code == 1
        assert payload["quality"]["score_mismatches"]
        assert replay_path.exists() is False
