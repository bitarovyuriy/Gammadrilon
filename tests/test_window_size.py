# -*- coding: utf-8 -*-
"""Окно помнит размер: растянул под себя — таким и откроется."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as gd_main  # noqa: E402

from PyQt5.QtCore import QSize  # noqa: E402


class FakeHost:
    """Минимум, который нужен методам размера."""

    def __init__(self, settings: dict) -> None:
        self.config = {"settings": settings}
        self._size = QSize(910, 700)
        self.resized: QSize | None = None
        self.maximized = False
        self.min_window_size = QSize(720, 560)

    # — то, что вызывают методы —
    def resize(self, width, height=None):  # noqa: N802 — имя из Qt
        size = width if isinstance(width, QSize) else QSize(int(width), int(height))
        self.resized = size
        self._size = size

    def size(self):  # noqa: N802
        return self._size

    def isMaximized(self):  # noqa: N802
        return self.maximized

    def isFullScreen(self):  # noqa: N802
        return False

    def showMaximized(self):  # noqa: N802
        self.maximized = True


def test_saved_size_is_restored():
    host = FakeHost({"window_size": [1280, 800], "window_maximized": False})

    gd_main.MainWindow._restore_window_size(host)

    assert host.resized == QSize(1280, 800)


def test_size_below_minimum_is_raised():
    host = FakeHost({"window_size": [400, 300]})

    gd_main.MainWindow._restore_window_size(host)

    assert host.resized == QSize(720, 560), "окно не должно стать меньше минимума"


def test_broken_size_is_ignored():
    host = FakeHost({"window_size": "большое"})

    gd_main.MainWindow._restore_window_size(host)

    assert host.resized is None, "битое значение не трогает окно"


def test_maximized_flag_is_restored():
    host = FakeHost({"window_size": [1280, 800], "window_maximized": True})

    gd_main.MainWindow._restore_window_size(host)

    assert host.maximized is True


def test_size_is_remembered_on_exit():
    host = FakeHost({})
    host._size = QSize(1440, 900)

    gd_main.MainWindow._remember_window_size(host)

    assert host.config["settings"]["window_size"] == [1440, 900]
    assert host.config["settings"]["window_maximized"] is False


def test_maximized_window_keeps_last_normal_size():
    host = FakeHost({"window_size": [1280, 800]})
    host.maximized = True

    gd_main.MainWindow._remember_window_size(host)

    assert host.config["settings"]["window_size"] == [1280, 800], "развёрнутое окно не пишет свой размер"
    assert host.config["settings"]["window_maximized"] is True


def test_window_size_is_not_transferred():
    assert "window_size" in gd_main.SETTINGS_NOT_TRANSFERABLE
    assert "window_maximized" in gd_main.SETTINGS_NOT_TRANSFERABLE
