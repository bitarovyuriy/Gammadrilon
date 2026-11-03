# -*- coding: utf-8 -*-
"""Контраст текста: пары цветов из темы должны читаться (WCAG AA).

`CONTRAST_PAIRS` в `gd_theme` перечисляет пары «текст на фоне», которые
встречаются в интерфейсе. Раньше список никто не проверял — цвет можно было
сделать нечитаемым и не заметить. Норма для мелкого текста — 4.5:1.
"""
from __future__ import annotations

import sys
from pathlib import Path

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
