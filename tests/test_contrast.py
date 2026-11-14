# -*- coding: utf-8 -*-
"""Контраст текста: пары цветов из темы должны читаться (WCAG AA).

`CONTRAST_PAIRS` в `gd_theme` перечисляет пары «текст на фоне», которые
встречаются в интерфейсе. Раньше список никто не проверял — цвет можно было
сделать нечитаемым и не заметить. Норма для мелкого текста — 4.5:1.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_theme  # noqa: E402

MIN_RATIO = 4.5


def _channel(value: int) -> float:
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    """Относительная яркость цвета по WCAG."""
    text = colour.lstrip("#")
    red, green, blue = (int(text[index:index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_pairs_are_declared():
    assert gd_theme.CONTRAST_PAIRS, "пары для проверки контраста должны быть объявлены"


def test_dark_theme_pairs_pass():
    problems = []
    for ink, paper in gd_theme.CONTRAST_PAIRS:
        palette = gd_theme.DARK
        if ink not in palette or paper not in palette:
            problems.append(f"{ink} на {paper}: нет цвета в тёмной теме")
            continue
        ratio = contrast_ratio(palette[ink], palette[paper])
        if ratio < MIN_RATIO:
            problems.append(f"{ink} на {paper}: {ratio:.2f}:1 (нужно {MIN_RATIO})")
    assert not problems, "; ".join(problems)


def test_light_theme_pairs_pass():
    problems = []
    for ink, paper in gd_theme.CONTRAST_PAIRS:
        palette = gd_theme.LIGHT
        if ink not in palette or paper not in palette:
            problems.append(f"{ink} на {paper}: нет цвета в светлой теме")
            continue
        ratio = contrast_ratio(palette[ink], palette[paper])
        if ratio < MIN_RATIO:
            problems.append(f"{ink} на {paper}: {ratio:.2f}:1 (нужно {MIN_RATIO})")
    assert not problems, "; ".join(problems)


def test_ratio_math_is_right():
    # белое на чёрном — максимум 21:1, одинаковые цвета — 1:1
    assert round(contrast_ratio("#FFFFFF", "#000000"), 1) == 21.0
    assert round(contrast_ratio("#777777", "#777777"), 2) == 1.0


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize(("role", "checked"), [
    ("PrimaryButton", None),
    ("Segment", True),
    ("Segment", False),
    ("ToggleButton", True),
    ("ToggleButton", False),
])
def test_keyboard_focus_renders_a_contrasting_button_border(theme, role, checked, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtCore import QEvent, Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

    app = QApplication.instance() or QApplication([])
    previous_theme = gd_theme.active_name()
    window = QWidget()
    window.setObjectName("PanelRoot")
    layout = QVBoxLayout(window)
    start = QPushButton("Start", window)
    target = QPushButton("Action", window)
    target.setObjectName(role)
    target.setFocusPolicy(Qt.StrongFocus)
    target.setFixedSize(160, 40)
    if checked is not None:
        target.setCheckable(True)
        target.setChecked(checked)
    layout.addWidget(start)
    layout.addWidget(target)

    def top_colors():
        grab = target.grab()
        image = grab.toImage()
        center = image.width() // 2
        return [image.pixelColor(center, row).name()
                for row in range(round(6 * grab.devicePixelRatioF()))]

    try:
        gd_theme.apply(theme)
        window.setStyleSheet(gd_theme.STYLESHEET)
        window.show()
        start.setFocus()
        app.processEvents()
        assert start.hasFocus()
        before = top_colors()

        QTest.keyClick(start, Qt.Key_Tab)
        assert target.hasFocus()
        focused = top_colors()
        filled = checked or role == "PrimaryButton"
        expected = gd_theme.active()["FOCUS_ON_ACCENT" if filled else "ACCENT"]
        border_width = round(2 * target.devicePixelRatioF())
        assert focused[:border_width] == [expected.lower()] * border_width
        assert focused[-1] == before[-1]
        assert contrast_ratio(focused[0], focused[-1]) >= 3
        assert contrast_ratio(focused[border_width - 1], before[border_width - 1]) >= 3
        if filled:
            for backdrop in (gd_theme.PAPER, gd_theme.SURFACE):
                assert contrast_ratio(focused[0], backdrop) >= 3

        QApplication.sendEvent(target, QEvent(QEvent.Enter))
        hovered = top_colors()
        assert hovered[:border_width] == focused[:border_width]
        assert hovered[-1] != focused[-1]
        assert contrast_ratio(hovered[0], hovered[-1]) >= 3

        QTest.keyPress(target, Qt.Key_Space)
        assert target.isDown()
        pressed = top_colors()
        assert pressed[:border_width] == focused[:border_width]
        assert contrast_ratio(pressed[0], pressed[-1]) >= 3
        QTest.keyRelease(target, Qt.Key_Space)
        QTest.keyClick(target, Qt.Key_Backtab, Qt.ShiftModifier)
        assert start.hasFocus()
    finally:
        window.close()
        gd_theme.apply(previous_theme)
