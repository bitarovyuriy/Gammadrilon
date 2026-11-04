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
