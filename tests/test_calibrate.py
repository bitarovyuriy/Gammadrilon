# -*- coding: utf-8 -*-
"""Калибровка: плитки считаются той же формулой, что кривая гаммы."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gd_calibrate  # noqa: E402

_APP = None


def app():
    global _APP
    from PyQt5.QtWidgets import QApplication

    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_strip_matches_gamma_controller():
    """Предпросмотр обязан совпадать с тем, что реально уйдёт на экран."""
    import main as gd_main

    app()
    strip = gd_calibrate.StepStrip()
    strip.set_curve(1.0, 0.25)

    ramp, _green, _blue = gd_main.GammaController().build_ramp(1.0, 1.0, 1.0, 6500, 0.25)
    for value in gd_calibrate.STEPS:
        expected = round(ramp[value] / 65535 * 255)
        assert abs(strip._level(value) - expected) <= 1, value


def test_shadow_lifts_dark_tiles_only():
    app()
    strip = gd_calibrate.StepStrip()

    strip.set_curve(1.0, 0.0)
    dark_plain = strip._level(16)
    light_plain = strip._level(200)

    strip.set_curve(1.0, 0.25)
    assert strip._level(16) > dark_plain, "тени должны подниматься"
    assert abs(strip._level(200) - light_plain) <= 1, "светлое почти не трогаем"


def test_dialog_returns_curve_and_calls_back():
    app()
    applied = []

    dialog = gd_calibrate.CalibrationDialog(on_apply=lambda g, s: applied.append((g, s)))
    dialog.gamma_slider.setValue(120)
    dialog.shadow_slider.setValue(40)

    assert abs(dialog.gamma() - 1.2) < 0.001
    assert abs(dialog.shadow() - 0.1) < 0.001

    dialog._accept()

    assert applied and abs(applied[0][0] - 1.2) < 0.001
    assert abs(applied[0][1] - 0.1) < 0.001


def test_callback_failure_does_not_block_dialog():
    app()
    dialog = gd_calibrate.CalibrationDialog(on_apply=lambda g, s: (_ for _ in ()).throw(RuntimeError()))

    dialog._accept()  # не должно бросить

    assert dialog.result() == dialog.Accepted


def test_white_patch_is_neutral_at_6500():
    app()
    patch = gd_calibrate.WhitePatch()

    patch.set_temperature(6500)
    colour = patch._tint()

    assert abs(colour.red() - colour.blue()) <= 6, "6500 К должны быть нейтральными"
    assert abs(colour.green() - colour.blue()) <= 6


def test_white_patch_goes_warm_and_cold():
    app()
    patch = gd_calibrate.WhitePatch()

    patch.set_temperature(3000)
    warm = patch._tint()
    patch.set_temperature(10000)
    cold = patch._tint()

    assert warm.red() > warm.blue() + 40, "3000 К — тёплый сдвиг"
    assert cold.blue() > cold.red(), "10000 К — холодный сдвиг"


def test_white_point_dialog_returns_temperature():
    app()
    applied = []
    dialog = gd_calibrate.WhitePointDialog(on_apply=applied.append, temperature=6500)

    dialog.slider.setValue(5000)
    assert dialog.temperature() == 5000
    dialog._accept()

    assert applied == [5000]
