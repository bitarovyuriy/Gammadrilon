# -*- coding: utf-8 -*-
"""Колесо мыши над списком стратегий не должно менять стратегию.

На плитке «Обход DPI» список живёт внутри прокручиваемой страницы: колесо над
ним молча переключало стратегию вместо прокрутки.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt5.QtGui import QWheelEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication, QComboBox  # noqa: E402

import main as _gd_main  # noqa: E402,F401  — выставляет QT_PLUGIN_PATH
import gd_dashboard_ui as dash  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _wheel(widget, delta=-120):
    event = QWheelEvent(
        QPointF(10, 10), QPointF(10, 10),
        QPoint(0, 0), QPoint(0, delta),
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
    )
    _APP.sendEvent(widget, event)
    return event


def _combo():
    combo = dash.NoWheelCombo()
    combo.addItems(["general.bat", "general (ALT).bat", "general (SIMPLE).bat"])
    combo.setCurrentIndex(1)
    return combo


def test_wheel_does_not_change_strategy():
    combo = _combo()

    event = _wheel(combo)

    assert combo.currentIndex() == 1
    assert event.isAccepted() is False      # событие отдано родителю — страница прокрутится


def test_plain_combo_still_scrolls_for_comparison():
    # контроль: обычный QComboBox колесом листается, значит тест ловит именно нашу правку
    combo = QComboBox()
    combo.addItems(["a", "b", "c"])
    combo.setCurrentIndex(1)

    _wheel(combo)

    assert combo.currentIndex() != 1


def test_tile_uses_the_non_scrolling_combo():
    source = (ROOT / "gd_dashboard_ui.py").read_text(encoding="utf-8")
    assert "self.combo = NoWheelCombo()" in source
