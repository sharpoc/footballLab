from __future__ import annotations

import io
import json
import os
import plistlib
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from worldcup.league_postmatch_launch_agent import (
    DEFAULT_LABEL,
    build_league_postmatch_launch_agent,
    main,
    write_league_postmatch_launch_agent,
)


PYTHON = "/runtime/python3"
ROOT = "/repo"
LOG_DIR = "/Users/example/Library/Logs/worldcup"


def test_full_live_plist_has_exact_schedule_and_is_not_run_at_load():
    """Changing the scheduled wake times or omitting a live flag must fail this test."""
    plist = build_league_postmatch_launch_agent(
        python=PYTHON,
        workdir=ROOT,
        full_live=True,
        log_dir=LOG_DIR,
    )

    assert plist["Label"] == DEFAULT_LABEL == "xin.celab.football.league-postmatch"
    assert plist["StartCalendarInterval"] == [
        {"Hour": 10, "Minute": 30},
        {"Hour": 16, "Minute": 30},
    ]
    assert "StartInterval" not in plist
    assert plist["RunAtLoad"] is False
    assert plist["ProgramArguments"] == [
        PYTHON,
        "-m",
        "worldcup.league_postmatch_runner",
        "--root",
        ROOT,
        "--live",
        "--write",
        "--notify",
    ]
    assert plist["WorkingDirectory"] == ROOT
    assert plist["StandardOutPath"] == f"{LOG_DIR}/league-postmatch.out.log"
    assert plist["StandardErrorPath"] == f"{LOG_DIR}/league-postmatch.err.log"


def test_schedule_mutation_does_not_leak_between_generated_plists():
    """Sharing the schedule list between calls must fail this isolation regression."""
    first = build_league_postmatch_launch_agent(
        python=PYTHON,
        workdir=ROOT,
        log_dir=LOG_DIR,
    )
    first["StartCalendarInterval"][0]["Hour"] = 0

    second = build_league_postmatch_launch_agent(
        python=PYTHON,
        workdir=ROOT,
        log_dir=LOG_DIR,
    )

    assert second["StartCalendarInterval"] == [
        {"Hour": 10, "Minute": 30},
        {"Hour": 16, "Minute": 30},
    ]


def test_observation_plist_keeps_no_due_wakes_read_only_and_contains_no_sensitive_config():
    """Adding live side effects or config values to the observation timer must fail."""
    plist = build_league_postmatch_launch_agent(
        python=PYTHON,
        workdir=ROOT,
        log_dir=LOG_DIR,
    )

    assert plist["ProgramArguments"] == [
        PYTHON,
        "-m",
        "worldcup.league_postmatch_runner",
        "--root",
        ROOT,
    ]
    assert plist["RunAtLoad"] is False
    serialized = json.dumps(plist, sort_keys=True).lower()
    for forbidden in (
        "--live",
        "--write",
        "--notify",
        "launchctl",
        "endpoint",
        "secret",
        "api_key",
        "token",
        "wxpusher",
        ".env",
    ):
        assert forbidden not in serialized


def test_writer_atomically_replaces_only_the_requested_plist():
    """Replacing a plist in place instead of atomically must fail this test."""
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        out = directory / "league-postmatch.plist"
        out.write_bytes(b"old plist")
        replacements: list[tuple[Path, Path, bool]] = []
        real_replace = os.replace

        def replace(source: str | Path, destination: str | Path) -> None:
            source_path = Path(source)
            destination_path = Path(destination)
            replacements.append((source_path, destination_path, destination_path.exists()))
            real_replace(source_path, destination_path)

        with patch(
            "worldcup.league_postmatch_launch_agent.os.replace",
            side_effect=replace,
        ):
            written = write_league_postmatch_launch_agent(
                out,
                python=PYTHON,
                workdir=ROOT,
                full_live=False,
                log_dir=LOG_DIR,
            )

        with out.open("rb") as handle:
            plist = plistlib.load(handle)

        assert written == out
        assert plist["Label"] == DEFAULT_LABEL
        assert len(replacements) == 1
        source, destination, destination_existed = replacements[0]
        assert source.parent == destination.parent == out.resolve().parent
        assert source != destination == out.resolve()
        assert source.name.startswith(".league-postmatch.plist.")
        assert destination_existed is True
        assert list(directory.iterdir()) == [out]


def test_cli_prints_dry_run_json_without_creating_a_plist():
    """Making the default CLI write or load a timer must fail this test."""
    with TemporaryDirectory() as tmp:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main([
                "--python", PYTHON,
                "--workdir", ROOT,
                "--log-dir", LOG_DIR,
            ])

        payload = json.loads(output.getvalue())
        assert exit_code == 0
        assert payload["status"] == "dry_run"
        assert payload["path"] is None
        assert payload["loaded"] is False
        assert payload["plist"]["Label"] == DEFAULT_LABEL
        assert list(Path(tmp).iterdir()) == []
