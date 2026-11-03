# -*- coding: utf-8 -*-
"""Трей: значок возвращается после перезапуска Explorer."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as gd_main  # noqa: E402


class FakeTray:
    def __init__(self, broken: bool = False) -> None:
        self.broken = broken
        self.calls: list[str] = []

    def hide(self) -> None:
        if self.broken:
            raise RuntimeError("трея нет")
        self.calls.append("hide")

    def show(self) -> None:
        self.calls.append("show")


class FakeWindow:
    """Ровно то, что нужно _restore_tray."""

    def __init__(self, tray) -> None:
        self.tray = tray
        self.status = ""

    def set_status(self, text: str, **_kwargs) -> None:
        self.status = text


def test_tray_is_shown_again():
    window = FakeWindow(FakeTray())

    gd_main.MainWindow._restore_tray(window)

    assert window.tray.calls == ["hide", "show"]
    assert "Explorer" in window.status


def test_without_tray_does_nothing():
    window = FakeWindow(None)

    gd_main.MainWindow._restore_tray(window)   # не должно упасть

    assert window.status == ""


def test_broken_tray_does_not_crash():
    window = FakeWindow(FakeTray(broken=True))

    gd_main.MainWindow._restore_tray(window)

    assert window.status == "", "при ошибке статус не трогаем"


def test_taskbar_message_is_registered():
    number = gd_main._taskbar_created_message()

    assert isinstance(number, int)
    assert number >= 0
    assert gd_main._taskbar_created_message() == number, "номер кэшируется"
