# -*- coding: utf-8 -*-
"""Автозапуск: обычный и с правами администратора.

Проверяем файл, который ложится в «Автозагрузку»: от него зависит, будет ли
приложение при входе просить UAC. Живой профиль не трогаем — APPDATA подменяем
на временную папку.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as gd_main  # noqa: E402


@pytest.fixture()
def startup(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    folder = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    folder.mkdir(parents=True)
    return folder / f"{gd_main.APP_NAME}.vbs"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-16")


def test_admin_startup_asks_for_rights(startup):
    gd_main.enable_startup(admin=True)

    content = _text(startup)
    assert "RunAs" in content
    assert "powershell" in content.lower()
    # окно не должно мелькать: WScript запускает скрыто
    assert '", 0, False' in content


def test_plain_startup_has_no_elevation(startup):
    gd_main.enable_startup(admin=False)

    content = _text(startup)
    assert "RunAs" not in content
    assert '", 0, False' in content


def test_switching_mode_rewrites_the_same_file(startup):
    gd_main.enable_startup(admin=True)
    assert "RunAs" in _text(startup)

    gd_main.enable_startup(admin=False)
    assert "RunAs" not in _text(startup)
    assert startup.exists()


def test_old_bat_is_removed(startup):
    legacy = startup.with_suffix(".bat")
    legacy.write_text("old", encoding="utf-8")

    gd_main.enable_startup(admin=True)

    assert not legacy.exists()


def test_disable_removes_the_file(startup):
    gd_main.enable_startup(admin=True)

    gd_main.disable_startup()

    assert not startup.exists()

def test_write_failure_preserves_existing_script_and_removes_temp(startup, monkeypatch):
    gd_main.enable_startup(admin=True)
    before = startup.read_bytes()

    def deny_replace(self, target):
        raise PermissionError("script locked")

    monkeypatch.setattr(Path, "replace", deny_replace)
    with pytest.raises(PermissionError, match="script locked"):
        gd_main.enable_startup(admin=False)

    assert startup.read_bytes() == before
    assert list(startup.parent.iterdir()) == [startup]


class StartupWindow:
    start_with_windows = gd_main.MainWindow.start_with_windows

    def __init__(self):
        self.config = {"settings": {"start_with_windows": True, "start_admin": True}}
        self.saves = 0
        self.status = None

    def _save_config(self):
        self.saves += 1

    def set_status(self, message, ok=True):
        self.status = (message, ok)


def test_delete_failure_does_not_disable_preference(startup, monkeypatch):
    gd_main.enable_startup()
    real_unlink = Path.unlink

    def deny_unlink(self, *args, **kwargs):
        if self == startup:
            raise PermissionError("script locked")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_unlink)
    window = StartupWindow()

    assert gd_main.MainWindow.set_start_with_windows(window, False)
    assert window.status[1] is False
    assert window.config["settings"]["start_with_windows"] is True
    assert window.saves == 0
    assert startup.exists()


def test_failed_mode_change_does_not_save_admin_preference(startup, monkeypatch):
    gd_main.enable_startup(admin=True)

    def deny_enable(admin):
        raise PermissionError("script locked")

    monkeypatch.setattr(gd_main, "enable_startup", deny_enable)
    window = StartupWindow()

    assert gd_main.MainWindow.set_start_admin(window, False)
    assert window.status[1] is False
    assert window.config["settings"]["start_admin"] is True
    assert window.saves == 0
    assert "RunAs" in _text(startup)
