import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.readiness import run_readiness_checks


ENV_EXAMPLE_TEMPLATE = (
    "API_FOOTBALL_KEY=\n"
    "THE_ODDS_API_KEY=\n"
    "THE_ODDS_API_KEY_PRIMARY=\n"
    "THE_ODDS_API_KEY_SECONDARY=\n"
    "THE_ODDS_API_KEY_TERTIARY=\n"
    "ODDS_API_IO_KEY=\n"
    "ODDSPAPI_KEY=\n"
    "INGEST_HMAC_SECRET=\n"
    "WORLDCUP_STORE=\n"
    "DATABASE_URL=\n"
)


def _write(path: Path, text: str = "{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_readiness_reports_ok_when_local_artifacts_exist():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        _write(
            root / ".env.example",
            ENV_EXAMPLE_TEMPLATE,
        )
        _write(
            root / "data/cache/analysis_snapshot.json",
            '{"counts":{"matches":1},"matches":[{"home_team":"Mexico","away_team":"South Africa"}]}',
        )
        _write(root / "data/cache/quota.json", '{"providers":{}}')
        _write(root / "data/cache/preview.html", "<html>仅用于研究分析，不构成投注建议</html>")
        _write(root / "data/cache/site/index.html", "<html>仅用于研究分析，不构成投注建议</html>")
        _write(root / "data/local/worldcup.db", "sqlite placeholder")
        _write(root / ".gitignore", ".env\ndata/cache/\ndata/local/\ndata/probe/\n")

        result = run_readiness_checks(root)

        assert result["ok"] is True
        assert result["summary"]["errors"] == 0
        assert result["checks"]["env_THE_ODDS_API_KEY"]["status"] == "ok"
        assert result["checks"]["env_example"]["status"] == "ok"
        assert result["checks"]["env_store"]["status"] == "ok"
        assert result["checks"]["env_store"]["store"] == "sqlite"
        assert result["checks"]["cache_snapshot"]["matches"] == 1
        assert result["checks"]["ignored_data_cache"]["status"] == "ok"


def test_readiness_reports_missing_required_artifacts_without_secrets():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=secret-value\n")
        _write(root / ".gitignore", ".env\n")

        result = run_readiness_checks(root)

        assert result["ok"] is False
        assert result["summary"]["errors"] > 0
        assert result["checks"]["env_INGEST_HMAC_SECRET"]["status"] == "error"
        assert result["checks"]["cache_snapshot"]["status"] == "error"
        assert "secret-value" not in str(result)


def test_readiness_rejects_env_example_with_values_or_missing_names():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        _write(root / ".env.example", "THE_ODDS_API_KEY=real-ish-value\n")
        _write(root / ".gitignore", ".env\n.env.*\n!.env.example\ndata/cache/\ndata/local/\ndata/probe/\n")

        result = run_readiness_checks(root)

        assert result["ok"] is False
        assert result["checks"]["env_example"]["status"] == "error"
        assert result["checks"]["env_example"]["message"] == "contains_values"
        assert "real-ish-value" not in str(result)


def test_readiness_accepts_sqlite_store_without_database_url():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nWORLDCUP_STORE=sqlite\n")
        _write(
            root / ".env.example",
            ENV_EXAMPLE_TEMPLATE,
        )
        _write(
            root / "data/cache/analysis_snapshot.json",
            '{"counts":{"matches":1},"matches":[{"home_team":"Mexico","away_team":"South Africa"}]}',
        )
        _write(root / "data/cache/quota.json", '{"providers":{}}')
        _write(root / ".gitignore", ".env\ndata/cache/\ndata/local/\ndata/probe/\n")

        result = run_readiness_checks(root)

        assert result["checks"]["env_store"]["status"] == "ok"
        assert result["checks"]["env_store"]["store"] == "sqlite"


def test_readiness_requires_database_url_name_when_postgres_selected():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nWORLDCUP_STORE=postgres\n")
        _write(
            root / ".env.example",
            ENV_EXAMPLE_TEMPLATE,
        )
        _write(
            root / "data/cache/analysis_snapshot.json",
            '{"counts":{"matches":1},"matches":[{"home_team":"Mexico","away_team":"South Africa"}]}',
        )
        _write(root / "data/cache/quota.json", '{"providers":{}}')
        _write(root / ".gitignore", ".env\ndata/cache/\ndata/local/\ndata/probe/\n")

        result = run_readiness_checks(root)

        assert result["ok"] is False
        assert result["checks"]["env_store"]["status"] == "error"
        assert result["checks"]["env_store"]["message"] == "missing_DATABASE_URL"
        assert "postgresql://" not in str(result)


def test_readiness_accepts_postgres_store_when_database_url_name_exists_without_printing_value():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root / ".env",
            "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nWORLDCUP_STORE=postgres\nDATABASE_URL=postgresql://user:pass@example.invalid/db\n",
        )
        _write(
            root / ".env.example",
            ENV_EXAMPLE_TEMPLATE,
        )
        _write(
            root / "data/cache/analysis_snapshot.json",
            '{"counts":{"matches":1},"matches":[{"home_team":"Mexico","away_team":"South Africa"}]}',
        )
        _write(root / "data/cache/quota.json", '{"providers":{}}')
        _write(root / ".gitignore", ".env\ndata/cache/\ndata/local/\ndata/probe/\n")

        result = run_readiness_checks(root)

        assert result["checks"]["env_store"]["status"] == "ok"
        assert result["checks"]["env_store"]["store"] == "postgres"
        assert "postgresql://user:pass@example.invalid/db" not in str(result)


def test_readiness_rejects_broken_snapshot_and_preview_without_disclaimer():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        _write(
            root / ".env.example",
            ENV_EXAMPLE_TEMPLATE,
        )
        _write(root / "data/cache/analysis_snapshot.json", '{"matches":[]}')
        _write(root / "data/cache/quota.json", "not json")
        _write(root / "data/cache/preview.html", "<html>No disclaimer</html>")
        _write(root / "data/cache/site/index.html", "<html>No disclaimer</html>")
        _write(root / ".gitignore", ".env\ndata/cache/\ndata/local/\ndata/probe/\n")

        result = run_readiness_checks(root)

        assert result["ok"] is False
        assert result["checks"]["cache_snapshot"]["message"] == "no_matches"
        assert result["checks"]["cache_quota"]["status"] == "warn"
        assert result["checks"]["cache_preview"]["message"] == "missing_disclaimer"
        assert result["checks"]["static_site_index"]["message"] == "missing_disclaimer"


# --- Profile and env_path tests (TDD: expected to fail until implementation) ---


def test_profile_full_is_default_and_runs_all_12_checks():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        _write(root / ".env.example", ENV_EXAMPLE_TEMPLATE)
        _write(root / "data/cache/analysis_snapshot.json", '{"counts":{"matches":1},"matches":[{"home_team":"A","away_team":"B"}]}')
        _write(root / "data/cache/quota.json", '{"providers":{}}')
        _write(root / "data/cache/preview.html", "<html>仅用于研究分析，不构成投注建议</html>")
        _write(root / "data/cache/site/index.html", "<html>仅用于研究分析，不构成投注建议</html>")
        _write(root / ".gitignore", ".env\ndata/cache/\ndata/local/\ndata/probe/\n")

        result = run_readiness_checks(root, profile="full")

        assert result["ok"] is True
        assert result["summary"]["checks"] == 12
        assert "env_INGEST_HMAC_SECRET" in result["checks"]
        assert "ignored_data_probe" in result["checks"]


def test_profile_server_only_checks_hmac_and_store():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "INGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nWORLDCUP_STORE=sqlite\n")

        result = run_readiness_checks(root, profile="server")

        assert result["ok"] is True
        assert set(result["checks"].keys()) == {"env_INGEST_HMAC_SECRET", "env_store"}
        assert result["summary"]["checks"] == 2


def test_profile_server_weak_hmac_fails():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "INGEST_HMAC_SECRET=short\nWORLDCUP_STORE=sqlite\n")

        result = run_readiness_checks(root, profile="server")

        assert result["ok"] is False
        assert result["checks"]["env_INGEST_HMAC_SECRET"]["status"] == "error"
        assert result["checks"]["env_INGEST_HMAC_SECRET"]["message"] == "weak_secret"


def test_profile_server_ignores_dev_hygiene_checks():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "INGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nWORLDCUP_STORE=sqlite\n")

        result = run_readiness_checks(root, profile="server")

        assert "env_example" not in result["checks"]
        assert "ignored_env" not in result["checks"]
        assert "cache_preview" not in result["checks"]
        assert "env_THE_ODDS_API_KEY" not in result["checks"]


def test_profile_publisher_checks_hmac_odds_snapshot_quota():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        _write(root / "data/cache/analysis_snapshot.json", '{"counts":{"matches":1},"matches":[{"home_team":"A","away_team":"B"}]}')
        _write(root / "data/cache/quota.json", '{"providers":{}}')

        result = run_readiness_checks(root, profile="publisher")

        assert result["ok"] is True
        expected_keys = {"env_INGEST_HMAC_SECRET", "env_THE_ODDS_API_KEY", "cache_snapshot", "cache_quota"}
        assert set(result["checks"].keys()) == expected_keys
        assert result["summary"]["checks"] == 4


def test_profile_publisher_missing_odds_key_fails():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "INGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        _write(root / "data/cache/analysis_snapshot.json", '{"counts":{"matches":1},"matches":[{"home_team":"A","away_team":"B"}]}')
        _write(root / "data/cache/quota.json", '{"providers":{}}')

        result = run_readiness_checks(root, profile="publisher")

        assert result["ok"] is False
        assert result["checks"]["env_THE_ODDS_API_KEY"]["status"] == "error"


def test_profile_publisher_missing_snapshot_fails():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        _write(root / "data/cache/quota.json", '{"providers":{}}')

        result = run_readiness_checks(root, profile="publisher")

        assert result["ok"] is False
        assert result["checks"]["cache_snapshot"]["status"] == "error"


def test_env_path_overrides_root_dot_env():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        alt_env = Path(tmp) / "alt" / "prod.env"
        _write(alt_env, "INGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nWORLDCUP_STORE=sqlite\n")

        result = run_readiness_checks(root, profile="server", env_path=alt_env)

        assert result["ok"] is True
        assert result["checks"]["env_INGEST_HMAC_SECRET"]["status"] == "ok"


def test_env_path_missing_file_safe_failure():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        nonexistent = Path(tmp) / "does_not_exist.env"

        result = run_readiness_checks(root, profile="server", env_path=nonexistent)

        assert result["ok"] is False
        assert result["checks"]["env_INGEST_HMAC_SECRET"]["status"] == "error"


def test_warning_does_not_affect_ok_with_profile():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        _write(root / ".env.example", ENV_EXAMPLE_TEMPLATE)
        _write(root / "data/cache/analysis_snapshot.json", '{"counts":{"matches":1},"matches":[{"home_team":"A","away_team":"B"}]}')
        _write(root / "data/cache/quota.json", "not json")
        _write(root / ".gitignore", ".env\ndata/cache/\ndata/local/\ndata/probe/\n")

        result = run_readiness_checks(root, profile="full")

        assert result["summary"]["warnings"] > 0
        assert result["ok"] is True


def test_cli_profile_argument():
    import subprocess
    import sys

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "INGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nWORLDCUP_STORE=sqlite\n")

        proc = subprocess.run(
            [sys.executable, "-m", "worldcup.readiness", "--root", str(root), "--profile", "server"],
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0
        output = json.loads(proc.stdout)
        assert output["summary"]["checks"] == 2


def test_cli_env_path_argument():
    import subprocess
    import sys

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        alt_env = Path(tmp) / "custom.env"
        _write(alt_env, "INGEST_HMAC_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nWORLDCUP_STORE=sqlite\n")

        proc = subprocess.run(
            [sys.executable, "-m", "worldcup.readiness", "--root", str(root), "--profile", "server", "--env-path", str(alt_env)],
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0
        output = json.loads(proc.stdout)
        assert output["checks"]["env_INGEST_HMAC_SECRET"]["status"] == "ok"


def test_invalid_profile_raises_valueerror():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            run_readiness_checks(root, profile="nonexistent")
            assert False, "should have raised ValueError"
        except ValueError as e:
            assert "unknown profile" in str(e)
