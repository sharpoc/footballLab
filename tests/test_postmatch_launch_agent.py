import plistlib
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.postmatch_launch_agent import (
    DEFAULT_ENDPOINT,
    DEFAULT_LABEL,
    build_postmatch_launch_agent,
    write_postmatch_launch_agent,
)
from worldcup.refresh_audit import inspect_launch_agent


def test_build_postmatch_launch_agent_defaults_to_daily_1640_live_publish():
    plist = build_postmatch_launch_agent(
        python_path="/opt/python/bin/python3",
        workdir="/Users/eagod/ai-dev/足彩",
    )

    args = plist["ProgramArguments"]
    assert plist["Label"] == DEFAULT_LABEL
    assert args[:3] == ["/opt/python/bin/python3", "-m", "worldcup.postmatch_publish"]
    assert "--live" in args
    assert args[args.index("--endpoint") + 1] == DEFAULT_ENDPOINT
    assert args[args.index("--env") + 1] == "/Users/eagod/ai-dev/足彩/.env"
    assert args[args.index("--base-snapshot") + 1].endswith(
        "/data/cache/analysis_snapshot.json"
    )
    assert args[args.index("--out") + 1].endswith(
        "/data/cache/wc2026_postmatch_snapshot.json"
    )
    assert args[args.index("--history") + 1].endswith("/data/local/history")
    assert args[args.index("--results") + 1].endswith(
        "/data/local/results/wc2026_results.csv"
    )
    assert args[args.index("--finished-store") + 1].endswith(
        "/data/local/finished_record_store.json"
    )
    assert plist["StartCalendarInterval"] == {"Hour": 16, "Minute": 40}
    assert "StartInterval" not in plist
    assert plist["RunAtLoad"] is False
    assert plist["EnvironmentVariables"] == {"PYTHONUNBUFFERED": "1"}
    assert plist["StandardOutPath"].endswith("/postmatch-publish.out.log")
    assert plist["StandardErrorPath"].endswith("/postmatch-publish.err.log")


def test_write_postmatch_launch_agent_roundtrips_through_inspector():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "xin.celab.football.postmatch-publish.plist"
        written = write_postmatch_launch_agent(
            out,
            python_path="/opt/python/bin/python3",
            workdir="/Users/eagod/ai-dev/足彩",
            hour=7,
            minute=5,
        )

        with out.open("rb") as fh:
            raw = plistlib.load(fh)
        inspected = inspect_launch_agent(out)

    assert written == out
    assert raw["Label"] == DEFAULT_LABEL
    assert inspected["status"] == "present"
    assert inspected["module"] == "worldcup.postmatch_publish"
    assert inspected["start_calendar_interval"] == {"Hour": 7, "Minute": 5}
