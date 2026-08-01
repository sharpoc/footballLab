from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from worldcup.nginx_routes import (
    DAILY_PICK_ROUTES,
    install_daily_picks_routes,
    normalize_site_config,
    render_daily_picks_snippet,
)
from worldcup.ssh_deploy import CommandResult, _deploy_script, run_ssh_deploy


SITE_TEMPLATE = """server {
    listen 443 ssl;
    server_name football.celab.xin;

    location = /api/daily-picks {
        proxy_pass http://127.0.0.1:8788;
    }

    location = /daily-picks {
        proxy_pass http://127.0.0.1:8788;
    }

    location / {
        try_files $uri /index.html;
    }
}
"""


def test_versioned_template_matches_renderer() -> None:
    template = Path("deploy/nginx/worldcup-daily-picks.conf").read_text(encoding="utf-8")
    assert template == render_daily_picks_snippet()


def _runner_for(*, nginx_test: int = 0, reload_result: int = 0):
    calls: list[list[str]] = []

    def runner(args: list[str], **_kwargs):
        calls.append(args)
        if args[:2] == ["nginx", "-t"]:
            return SimpleNamespace(returncode=nginx_test, stdout="", stderr="nginx test failed")
        if args[:3] == ["systemctl", "reload", "nginx"]:
            return SimpleNamespace(returncode=reload_result, stdout="", stderr="reload failed")
        raise AssertionError(f"unexpected command: {args}")

    runner.calls = calls
    return runner


def test_rendered_snippet_contains_only_the_four_exact_daily_pick_routes() -> None:
    rendered = render_daily_picks_snippet()

    assert tuple(DAILY_PICK_ROUTES) == (
        "/api/daily-picks",
        "/daily-picks",
        "/api/daily-picks-sidecar",
        "/daily-picks-sidecar",
    )
    for route in DAILY_PICK_ROUTES:
        assert f"location = {route} {{" in rendered
    assert rendered.count("proxy_pass http://127.0.0.1:8788;") == 4
    assert "location / {" not in rendered
    assert "api_key" not in rendered.lower()
    assert "secret" not in rendered.lower()


def test_normalize_site_config_replaces_legacy_routes_with_one_managed_include() -> None:
    normalized = normalize_site_config(SITE_TEMPLATE)

    assert "include /etc/nginx/snippets/worldcup-daily-picks.conf;" in normalized
    assert normalized.count("include /etc/nginx/snippets/worldcup-daily-picks.conf;") == 1
    assert "location / {" in normalized
    assert all(f"location = {route} {{" not in normalized for route in DAILY_PICK_ROUTES)
    assert normalize_site_config(normalized) == normalized


def test_install_is_atomic_backed_up_and_reloads_only_after_nginx_test() -> None:
    with TemporaryDirectory() as raw_tmp:
        tmp_path = Path(raw_tmp)
        site = tmp_path / "football.celab.xin.conf"
        snippet = tmp_path / "worldcup-daily-picks.conf"
        source = tmp_path / "template.conf"
        backup_dir = tmp_path / "backups"
        old_snippet = "legacy snippet\n"
        site.write_text(SITE_TEMPLATE, encoding="utf-8")
        snippet.write_text(old_snippet, encoding="utf-8")
        source.write_text(render_daily_picks_snippet(), encoding="utf-8")
        runner = _runner_for()

        result = install_daily_picks_routes(
            site_config=site,
            snippet_path=snippet,
            snippet_source=source,
            backup_dir=backup_dir,
            nginx_service="nginx",
            command_runner=runner,
            timestamp="20260801T120000Z",
        )

        assert result["status"] == "installed"
        assert result["changed"] is True
        assert runner.calls == [["nginx", "-t"], ["systemctl", "reload", "nginx"]]
        assert snippet.read_text(encoding="utf-8") == render_daily_picks_snippet()
        assert f"include {snippet};" in site.read_text(
            encoding="utf-8"
        )
        assert (backup_dir / "20260801T120000Z-football.celab.xin.conf").read_text(
            encoding="utf-8"
        ) == SITE_TEMPLATE
        assert (backup_dir / "20260801T120000Z-worldcup-daily-picks.conf").read_text(
            encoding="utf-8"
        ) == old_snippet


def test_install_is_idempotent_without_backup_or_reload() -> None:
    with TemporaryDirectory() as raw_tmp:
        tmp_path = Path(raw_tmp)
        site = tmp_path / "football.celab.xin.conf"
        snippet = tmp_path / "worldcup-daily-picks.conf"
        source = tmp_path / "template.conf"
        backup_dir = tmp_path / "backups"
        site.write_text(normalize_site_config(SITE_TEMPLATE, include_path=str(snippet)), encoding="utf-8")
        source.write_text(render_daily_picks_snippet(), encoding="utf-8")
        snippet.write_text(render_daily_picks_snippet(), encoding="utf-8")
        runner = _runner_for()

        result = install_daily_picks_routes(
            site_config=site,
            snippet_path=snippet,
            snippet_source=source,
            backup_dir=backup_dir,
            command_runner=runner,
            timestamp="20260801T120000Z",
        )

        assert result["status"] == "unchanged"
        assert result["changed"] is False
        assert runner.calls == []
        assert not backup_dir.exists()


def test_nginx_test_failure_restores_files_and_never_reloads() -> None:
    with TemporaryDirectory() as raw_tmp:
        tmp_path = Path(raw_tmp)
        site = tmp_path / "football.celab.xin.conf"
        snippet = tmp_path / "worldcup-daily-picks.conf"
        source = tmp_path / "template.conf"
        backup_dir = tmp_path / "backups"
        site.write_text(SITE_TEMPLATE, encoding="utf-8")
        snippet.write_text("legacy snippet\n", encoding="utf-8")
        source.write_text(render_daily_picks_snippet(), encoding="utf-8")
        runner = _runner_for(nginx_test=1)

        result = install_daily_picks_routes(
            site_config=site,
            snippet_path=snippet,
            snippet_source=source,
            backup_dir=backup_dir,
            command_runner=runner,
            timestamp="20260801T120000Z",
        )

        assert result["status"] == "nginx_test_failed"
        assert result["restored"] is True
        assert runner.calls == [["nginx", "-t"]]
        assert site.read_text(encoding="utf-8") == SITE_TEMPLATE
        assert snippet.read_text(encoding="utf-8") == "legacy snippet\n"


def test_reload_failure_restores_old_files_and_attempts_old_config_recovery() -> None:
    with TemporaryDirectory() as raw_tmp:
        tmp_path = Path(raw_tmp)
        site = tmp_path / "football.celab.xin.conf"
        snippet = tmp_path / "worldcup-daily-picks.conf"
        source = tmp_path / "template.conf"
        backup_dir = tmp_path / "backups"
        site.write_text(SITE_TEMPLATE, encoding="utf-8")
        snippet.write_text("legacy snippet\n", encoding="utf-8")
        source.write_text(render_daily_picks_snippet(), encoding="utf-8")
        runner = _runner_for(reload_result=1)

        result = install_daily_picks_routes(
            site_config=site,
            snippet_path=snippet,
            snippet_source=source,
            backup_dir=backup_dir,
            command_runner=runner,
            timestamp="20260801T120000Z",
        )

        assert result["status"] == "reload_failed"
        assert result["restored"] is True
        assert runner.calls == [
            ["nginx", "-t"],
            ["systemctl", "reload", "nginx"],
            ["nginx", "-t"],
            ["systemctl", "reload", "nginx"],
        ]
        assert site.read_text(encoding="utf-8") == SITE_TEMPLATE
        assert snippet.read_text(encoding="utf-8") == "legacy snippet\n"


def test_deploy_script_installs_versioned_nginx_routes_before_service_restart() -> None:
    script = _deploy_script(
        release="/opt/worldcup/releases/abc123",
        current_symlink="/opt/worldcup/current",
        service="worldcup.service",
        nginx_service="nginx",
        py_compile_files=("worldcup/http_app.py", "worldcup/nginx_routes.py"),
        readyz_url="http://127.0.0.1:8788/readyz",
        rollback_on_fail=True,
    )

    assert "worldcup.nginx_routes" in script
    assert "--install" in script
    assert "--site-config" in script
    assert "/etc/nginx/sites-available/football.celab.xin.conf" in script
    assert "/etc/nginx/snippets/worldcup-daily-picks.conf" in script
    assert "/root/nginx-backups" in script
    assert 'systemctl reload "$nginx_service"' not in script
    assert 'systemctl restart "$service"' in script
    assert script.index('systemctl restart "$service"') < script.index("--install")


def test_ssh_deploy_dry_run_does_not_archive_ssh_or_install_nginx_routes() -> None:
    calls: list[list[str]] = []

    def runner(args: list[str], **_kwargs) -> CommandResult:
        calls.append(args)
        if args[:3] == ["git", "rev-parse", "--verify"]:
            return CommandResult(0, "abc123\n", "")
        if args[:3] == ["git", "status", "--porcelain"]:
            return CommandResult(0, "", "")
        raise AssertionError(f"dry-run must not execute: {args}")

    result = run_ssh_deploy(root=".", live=False, command_runner=runner)

    assert result["status"] == "dry_run_ready"
    assert calls == [
        ["git", "rev-parse", "--verify", "HEAD"],
        ["git", "status", "--porcelain"],
    ]
