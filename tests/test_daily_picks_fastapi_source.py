from __future__ import annotations

from pathlib import Path


def test_fastapi_source_declares_daily_picks_routes_explicitly():
    source = Path("worldcup/fastapi_app.py").read_text(encoding="utf-8")
    assert '@app.get("/api/daily-picks")' in source
    assert '@app.get("/daily-picks")' in source


def test_daily_picks_source_declares_return_link_and_active_menu():
    source = Path("worldcup/daily_picks_html.py").read_text(encoding="utf-8")
    assert 'href="/preview"' in source
    assert 'href="/daily-picks"' in source
    assert 'aria-current="page"' in source
    assert 'width=device-width' in source
