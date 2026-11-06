# -*- coding: utf-8 -*-
"""Вкладки «Утилит» берут цвета из общей темы, а не из локального стиля.

Локальный setStyleSheet на виджете отключает наследование темы: вкладки
становились белыми на тёмном фоне. Плюс QTabBar в стиле Fusion рисует светлую
базовую линию — на тёмной теме она выглядела белой полосой во всю ширину.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_theme  # noqa: E402

UTILS = (ROOT / "gd_utils_ui.py").read_text(encoding="utf-8")
QSS = gd_theme.STYLESHEET


def _block(selector: str) -> str:
    start = QSS.find(selector + " {")
    assert start >= 0, f"в теме нет правила {selector}"
    return QSS[start:QSS.find("}", start)]


def test_subtabs_block_is_styled_by_theme():
    pane = _block("QTabWidget#SubTabs::pane")
    assert "border: none" in pane


def test_tab_bar_does_not_draw_light_base_line():
    bar = _block("QTabWidget#SubTabs QTabBar")
    assert "qproperty-drawBase: 0" in bar


def test_selected_tab_is_visible():
    block = QSS[QSS.find("QTabWidget#SubTabs QTabBar::tab:selected"):]
    block = block[:block.find("}")]
    assert "border-bottom" in block


def test_no_local_stylesheet_on_tabs_widget():
    # `self._tabs.setStyleSheet(...)` ломает тему — такого вызова быть не должно
    assert not re.search(r"_tabs\.setStyleSheet", UTILS)


def test_theme_has_no_unreplaced_tokens():
    assert "@" not in QSS[QSS.find("QTabWidget#SubTabs"):QSS.find("QTabWidget#SubTabs") + 800]
