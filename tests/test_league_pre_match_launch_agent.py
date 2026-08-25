from __future__ import annotations

import json
import plistlib
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_pre_match_launch_agent import (
    DEFAULT_LABEL,
    build_league_pre_match_launch_agent,
    write_league_pre_match_launch_agent,
)


PYTHON = "/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
ROOT = "/Users/eagod/ai-dev/足彩"


def test_observation_launch_agent_has_independent_label_exact_paths_and_safe_defaults():
    plist = build_league_pre_match_launch_agent(
        python_path=PYTHON,
        workdir=ROOT,
    )

    assert plist["Label"] == DEFAULT_LABEL == "xin.celab.football.league-pre-match"
    assert plist["ProgramArguments"] == [
        PYTHON,
        "-m",
        "worldcup.league_pre_match_runner",
        "--root",
        ROOT,
        "--live-lineups",
        "--write-lineups",
    ]
    assert plist["WorkingDirectory"] == ROOT
    assert plist["StartInterval"] == 300
    assert plist["RunAtLoad"] is False
    assert plist["StandardOutPath"].endswith("/league-pre-match.out.log")
    assert plist["StandardErrorPath"].endswith("/league-pre-match.err.log")
    assert "EnvironmentVariables" not in plist
    assert plist["Label"] != "xin.celab.football.pre-match"


def test_full_live_launch_agent_contains_every_layer_and_quota_guard_once():
    plist = build_league_pre_match_launch_agent(
        python_path=PYTHON,
        workdir=ROOT,
        full_live=True,
    )

    args = plist["ProgramArguments"]
    expected = (
        "--live-lineups",
        "--write-lineups",
        "--refresh-after-lineups",
        "--live-refresh",
        "--refresh-guard",
        "--publish",
        "--notify",
    )
    assert all(args.count(flag) == 1 for flag in expected)
    assert args.index("--refresh-guard") > args.index("--live-refresh")
    serialized = json.dumps(plist).lower()
    for forbidden in ("launchctl", "ingest_hmac_secret", "the_odds_api_key", "wxpusher"):
        assert forbidden not in serialized


def test_write_generator_only_writes_requested_plist_and_never_loads_timer():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "league-pre-match.plist"
        written = write_league_pre_match_launch_agent(
            out,
            python_path=PYTHON,
            workdir=ROOT,
            full_live=False,
        )
        with out.open("rb") as handle:
            plist = plistlib.load(handle)

        assert written == out
        assert list(Path(tmp).iterdir()) == [out]
        assert plist["Label"] == DEFAULT_LABEL
        assert plist["RunAtLoad"] is False
