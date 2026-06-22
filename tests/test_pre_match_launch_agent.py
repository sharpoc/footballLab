import plistlib
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.pre_match_launch_agent import (
    DEFAULT_LABEL,
    build_pre_match_launch_agent,
    write_pre_match_launch_agent,
)
from worldcup.refresh_audit import inspect_launch_agent


def test_build_pre_match_launch_agent_defaults_to_lineups_only():
    plist = build_pre_match_launch_agent(
        python_path="/opt/python/bin/python3",
        workdir="/Users/eagod/ai-dev/足彩",
    )

    args = plist["ProgramArguments"]
    assert plist["Label"] == DEFAULT_LABEL
    assert args[:3] == ["/opt/python/bin/python3", "-m", "worldcup.pre_match_runner"]
    assert "--live-lineups" in args
    assert "--write-lineups" in args
    assert "--notify-missing" in args
    assert "--notify-audit" in args
    assert "--refresh-after-lineups" not in args
    assert "--live-refresh" not in args
    assert plist["WorkingDirectory"] == "/Users/eagod/ai-dev/足彩"
    assert plist["StartInterval"] == 300
    assert plist["RunAtLoad"] is False
    assert plist["StandardOutPath"].endswith("/Library/Logs/worldcup/pre-match.out.log")
    assert plist["StandardErrorPath"].endswith("/Library/Logs/worldcup/pre-match.err.log")


def test_build_pre_match_launch_agent_can_opt_into_post_lineup_refresh():
    plist = build_pre_match_launch_agent(
        python_path="/opt/python/bin/python3",
        workdir="/Users/eagod/ai-dev/足彩",
        allow_live_refresh=True,
    )

    args = plist["ProgramArguments"]
    assert "--refresh-guard" in args
    assert "--refresh-after-lineups" in args
    assert "--live-refresh" in args


def test_write_pre_match_launch_agent_roundtrips_through_launch_agent_inspector():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "xin.celab.football.pre-match.plist"
        written = write_pre_match_launch_agent(
            out,
            python_path="/opt/python/bin/python3",
            workdir="/Users/eagod/ai-dev/足彩",
        )

        with open(out, "rb") as fh:
            raw = plistlib.load(fh)
        inspected = inspect_launch_agent(out)

    assert written == out
    assert raw["Label"] == DEFAULT_LABEL
    assert inspected["status"] == "present"
    assert inspected["module"] == "worldcup.pre_match_runner"
    assert inspected["start_interval"] == 300
    assert "--live-refresh" not in inspected["program_arguments"]
