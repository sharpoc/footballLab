import plistlib
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.csl_scheduled_launch_agent import (
    DEFAULT_LABEL,
    build_csl_scheduled_launch_agent,
    write_csl_scheduled_launch_agent,
)
from worldcup.refresh_audit import inspect_launch_agent


def test_build_csl_scheduled_launch_agent_defaults_to_safe_live_runner():
    plist = build_csl_scheduled_launch_agent(
        python_path="/opt/python/bin/python3",
        workdir="/Users/eagod/ai-dev/足彩",
    )

    args = plist["ProgramArguments"]
    assert plist["Label"] == DEFAULT_LABEL
    assert args[:3] == ["/opt/python/bin/python3", "-m", "worldcup.csl_scheduled_publish"]
    assert "--live" in args
    assert "--cache-dir" in args
    assert "/Users/eagod/ai-dev/足彩/data/cache" in args
    assert "--quota-path" in args
    assert "/Users/eagod/ai-dev/足彩/data/cache/quota.json" in args
    assert "--snapshot-path" in args
    assert "/Users/eagod/ai-dev/足彩/data/cache/csl_publish_snapshot.json" in args
    assert "--diagnostics-snapshot-path" in args
    assert (
        "/Users/eagod/ai-dev/足彩/data/local/diagnostics/csl_live_league_snapshot.json" in args
    )
    assert "--env" in args
    assert "/Users/eagod/ai-dev/足彩/.env" in args
    assert plist["StartInterval"] == 1800
    assert plist["RunAtLoad"] is False
    assert plist["EnvironmentVariables"] == {"PYTHONUNBUFFERED": "1"}
    assert plist["StandardOutPath"].endswith("/csl-scheduled-publish.out.log")
    assert plist["StandardErrorPath"].endswith("/csl-scheduled-publish.err.log")


def test_write_csl_scheduled_launch_agent_roundtrips_through_inspector():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "xin.celab.football.csl-scheduled-publish.plist"
        written = write_csl_scheduled_launch_agent(
            out,
            python_path="/opt/python/bin/python3",
            workdir="/Users/eagod/ai-dev/足彩",
        )

        with out.open("rb") as fh:
            raw = plistlib.load(fh)
        inspected = inspect_launch_agent(out)

    assert written == out
    assert raw["Label"] == DEFAULT_LABEL
    assert inspected["status"] == "present"
    assert inspected["module"] == "worldcup.csl_scheduled_publish"
    assert inspected["start_interval"] == 1800
