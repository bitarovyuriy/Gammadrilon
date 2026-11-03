# -*- coding: utf-8 -*-
"""Портативный режим: маркер рядом с приложением и копия настроек."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_paths  # noqa: E402


def use_dirs(monkeypatch, app: Path, profile: Path) -> None:
    monkeypatch.setattr(gd_paths, "app_dir", lambda: app)
    monkeypatch.setattr(gd_paths, "log_dir", lambda: profile)


def test_portable_marker_and_settings_copy(monkeypatch, tmp_path):
    app_dir = tmp_path / "app"
    profile = tmp_path / "profile"
    app_dir.mkdir()
    profile.mkdir()
    (profile / gd_paths.CONFIG_NAME).write_text('{"settings": {"theme": "dark"}}',
                                                encoding="utf-8")
    use_dirs(monkeypatch, app_dir, profile)

    ok, message = gd_paths.make_portable()

    assert ok is True, message
    assert (app_dir / gd_paths.MARKER_NAMES[0]).is_file(), "маркер обязан появиться"
    assert (app_dir / gd_paths.CONFIG_NAME).is_file(), "настройки переезжают в папку программы"
    assert message.endswith(gd_paths.MARKER_NAMES[0]) or "portable" in message


def test_portable_works_without_existing_settings(monkeypatch, tmp_path):
    app_dir = tmp_path / "app"
    profile = tmp_path / "profile"
    app_dir.mkdir()
    profile.mkdir()
    use_dirs(monkeypatch, app_dir, profile)

    ok, _message = gd_paths.make_portable()

    assert ok is True
    assert (app_dir / gd_paths.MARKER_NAMES[0]).is_file()
    assert not (app_dir / gd_paths.CONFIG_NAME).exists()


def test_portable_does_not_overwrite_existing_config(monkeypatch, tmp_path):
    app_dir = tmp_path / "app"
    profile = tmp_path / "profile"
    app_dir.mkdir()
    profile.mkdir()
    (profile / gd_paths.CONFIG_NAME).write_text('{"settings": {"theme": "light"}}',
                                                encoding="utf-8")
    (app_dir / gd_paths.CONFIG_NAME).write_text('{"settings": {"theme": "dark"}}',
                                                encoding="utf-8")
    use_dirs(monkeypatch, app_dir, profile)

    gd_paths.make_portable()

    saved = (app_dir / gd_paths.CONFIG_NAME).read_text(encoding="utf-8")
    assert "dark" in saved, "настройки рядом с программой не перезаписываются"


def test_portable_reports_unwritable_folder(monkeypatch, tmp_path):
    app_dir = tmp_path / "app"
    profile = tmp_path / "profile"
    app_dir.mkdir()
    profile.mkdir()
    use_dirs(monkeypatch, app_dir, profile)

    def boom(*_args, **_kwargs):
        raise OSError("нет прав")

    monkeypatch.setattr(Path, "write_text", boom)

    ok, message = gd_paths.make_portable()

    assert ok is False
    assert "маркер" in message
