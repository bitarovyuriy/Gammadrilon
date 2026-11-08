# -*- coding: utf-8 -*-
"""Профили лимита GPU на главном экране — взаимоисключающие.

Ряд сегментов строится независимыми кнопками, поэтому без отдельной логики
после клика по второму профилю первый оставался подсвеченным: на плитке
горели сразу три-четыре пресета.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

import main as _gd_main  # noqa: E402,F401  — выставляет QT_PLUGIN_PATH
import gd_dashboard_ui as dash  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, name, ok):
        for slot in self.slots:
            slot(name, ok)


class FakePage:
    def __init__(self):
        self.applied = []
        self.profile_result = FakeSignal()

    def apply_profile(self, profile):
        self.applied.append(profile)


class FakeBoard(QWidget):
    host = None

    def __init__(self, page=None):
        super().__init__()
        self.page = page

    def ensure_page(self, key):
        return self.page


def _buttons(*names):
    buttons = {}
    for name in names:
        button = QPushButton(name)
        button.setCheckable(True)       # как в _chips: иначе setChecked не держится
        buttons[name] = button
    return buttons


def test_only_one_segment_stays_checked():
    buttons = _buttons("quiet", "default", "game", "max")
    for button in buttons.values():
        button.setChecked(True)

    dash._select_only(buttons, "max")

    checked = [name for name, button in buttons.items() if button.isChecked()]
    assert checked == ["max"]


def test_empty_key_clears_all():
    buttons = _buttons("quiet", "max")
    for button in buttons.values():
        button.setChecked(True)

    dash._select_only(buttons, "")

    assert not any(button.isChecked() for button in buttons.values())


def test_pick_marks_one_profile_and_applies_it():
    page = FakePage()
    tile = dash.GpuLimitTile("w.gpu_limit", FakeBoard(page))

    tile._pick("game")

    assert page.applied == ["game"]
    checked = [key for key, button in tile.chips.items() if button.isChecked()]
    assert checked == ["game"]

    tile._pick("quiet")

    checked = [key for key, button in tile.chips.items() if button.isChecked()]
    assert checked == ["quiet"], "предыдущий профиль должен погаснуть"


def test_pick_without_page_does_not_leave_lit_button():
    tile = dash.GpuLimitTile("w.gpu_limit", FakeBoard(None))

    tile._pick("max")

    assert not any(button.isChecked() for button in tile.chips.values())
