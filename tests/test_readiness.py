from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.readiness import run_readiness_checks


ENV_EXAMPLE_TEMPLATE = (
    "API_FOOTBALL_KEY=\n"
    "THE_ODDS_API_KEY=\n"
    "THE_ODDS_API_KEY_PRIMARY=\n"
    "THE_ODDS_API_KEY_SECONDARY=\n"
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
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=y\n")
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
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=y\n")
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
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=y\nWORLDCUP_STORE=sqlite\n")
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
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=y\nWORLDCUP_STORE=postgres\n")
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
            "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=y\nWORLDCUP_STORE=postgres\nDATABASE_URL=postgresql://user:pass@example.invalid/db\n",
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
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=y\n")
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
