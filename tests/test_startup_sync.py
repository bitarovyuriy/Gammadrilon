# -*- coding: utf-8 -*-
"""Автозапуск: файл приводится в соответствие с настройкой при старте.

Бывает тихое расхождение: галочка «Запускать вместе с Windows» выключена, а
файл в «Автозагрузке» остался от прошлой версии — и программа поднимается при
входе в систему, хотя обещает обратное. Или галочка включена, а файл ссылается
на старую сборку (`C:/gd_build/dist/...`) вместо установленной.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as gd_main  # noqa: E402


class Window:
    def __init__(self, start_with_windows: bool) -> None:
        self.config = {"settings": {"start_with_windows": start_with_windows}}


def test_sync_removes_file_when_disabled(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(gd_main, "disable_startup", lambda: calls.append("disable"))
    monkeypatch.setattr(gd_main, "enable_startup", lambda: calls.append("enable"))

    gd_main.MainWindow._sync_startup(Window(False))

    assert calls == ["disable"], "выключено — файл автозапуска убираем"


def test_sync_writes_file_when_enabled(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(gd_main, "disable_startup", lambda: calls.append("disable"))
    monkeypatch.setattr(gd_main, "enable_startup", lambda: calls.append("enable"))

    gd_main.MainWindow._sync_startup(Window(True))

    assert calls == ["enable"], "включено — файл перезаписывается под текущий exe"


def test_sync_survives_failure(monkeypatch):
    def boom():
        raise OSError("нет доступа")

    monkeypatch.setattr(gd_main, "enable_startup", boom)
    monkeypatch.setattr(gd_main, "disable_startup", boom)

    gd_main.MainWindow._sync_startup(Window(True))   # не должно бросить наружу


def test_startup_path_is_vbs_in_startup_folder():
    path = gd_main.get_startup_shortcut_path()

    assert path.suffix == ".vbs"
    assert "Startup" in str(path)
