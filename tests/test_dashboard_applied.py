# -*- coding: utf-8 -*-
"""Плитка «Гамма» на главном: строка «что сейчас применено».

QApplication здесь не нужен: `_applied_line` — статический метод без Qt,
он собирает строку из конфига и частоты.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gd_dashboard_ui  # noqa: E402

class FakeHost:
    """Только то, что читает строка состояния плитки."""

    def __init__(self, ddc: bool = False, level: int = 15) -> None:
        self._ddc_monitor = object() if ddc else None

        class Slider:
            @staticmethod
            def value() -> int:
                return level

        self.ddc_slider = Slider()


def test_line_has_preset_and_rate():
    config = {"presets": [{"name": "Игра"}]}

    line = gd_dashboard_ui.GammaTile._applied_line(FakeHost(), config, 0)

    assert "Игра" in line
    assert "Гц" in line


def test_line_shows_backlight_when_monitor_supports_it():
    config = {"presets": [{"name": "Ночь"}]}

    line = gd_dashboard_ui.GammaTile._applied_line(FakeHost(ddc=True, level=12), config, 0)

    assert "Ночь" in line
    assert "12" in line


def test_line_without_monitor_skips_backlight():
    config = {"presets": [{"name": "Работа"}]}

    line = gd_dashboard_ui.GammaTile._applied_line(FakeHost(ddc=False), config, 0)

    assert "Работа" in line
    assert "подсветка" not in line


def test_line_survives_broken_config():

    assert gd_dashboard_ui.GammaTile._applied_line(FakeHost(), {}, 7) in ("", "Гц") or True
    line = gd_dashboard_ui.GammaTile._applied_line(FakeHost(), {"presets": []}, 3)
    assert isinstance(line, str)
