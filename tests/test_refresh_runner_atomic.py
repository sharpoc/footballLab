"""Tests for refresh_runner.py atomic Elo write."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from worldcup.refresh_runner import _write_text_atomic


def test_write_text_atomic_success():
    """Successful write replaces target file with new content."""
    with TemporaryDirectory() as tmp:
        target = Path(tmp) / "elo_world.tsv"
        target.write_text("old content", encoding="utf-8")

        _write_text_atomic(target, "new content")

        assert target.read_text(encoding="utf-8") == "new content"


def test_write_text_atomic_no_temp_files_on_success():
    with TemporaryDirectory() as tmp:
        target = Path(tmp) / "elo_world.tsv"
        _write_text_atomic(target, "content")
        files = [f.name for f in Path(tmp).iterdir()]
        assert not any(f.endswith(".tmp") for f in files)


def test_write_text_atomic_preserves_old_on_replace_failure():
    """If os.replace fails, original file stays intact and no temp left."""
    with TemporaryDirectory() as tmp:
        target = Path(tmp) / "elo_world.tsv"
        target.write_text("original", encoding="utf-8")

        with patch("worldcup.refresh_runner.os.replace", side_effect=OSError("disk full")):
            try:
                _write_text_atomic(target, "new data that should not appear")
            except OSError:
                pass

        assert target.read_text(encoding="utf-8") == "original"
        files = [f.name for f in Path(tmp).iterdir()]
        assert not any(f.endswith(".tmp") for f in files)


def test_write_text_atomic_creates_new_file():
    """Works when target does not yet exist."""
    with TemporaryDirectory() as tmp:
        target = Path(tmp) / "new_file.tsv"
        _write_text_atomic(target, "fresh content")
        assert target.read_text(encoding="utf-8") == "fresh content"


def test_write_text_atomic_unicode():
    """Handles non-ASCII content correctly."""
    with TemporaryDirectory() as tmp:
        target = Path(tmp) / "elo.tsv"
        content = "Team\tRating\n日本\t1800\n韩国\t1750\n"
        _write_text_atomic(target, content)
        assert target.read_text(encoding="utf-8") == content
