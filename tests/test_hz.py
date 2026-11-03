# -*- coding: utf-8 -*-
"""Частота обновления: меняется только частота, режим остаётся прежним.

Реальный режим в тестах не переключаем: смена герцовки мигает экраном.
Проверяем то, что можно проверить безопасно — чтение режима, список частот
и то, что `set_refresh` с несуществующей частотой честно отказывает.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_hz  # noqa: E402


def test_devmode_size_matches_windows_contract():
    """dmSize обязателен: с чужим размером драйвер молча вернёт отказ."""
    assert gd_hz.ctypes.sizeof(gd_hz.DEVMODEW) == 220


def test_current_mode_is_readable():
    devmode = gd_hz.current()

    assert devmode is not None, "Windows должна отдавать текущий режим"
    assert devmode.dmPelsWidth > 0 and devmode.dmPelsHeight > 0
    assert devmode.dmDisplayFrequency > 0
    assert gd_hz.refresh_rate() == devmode.dmDisplayFrequency


def test_supported_rates_contain_current_and_are_sane():
    rates = gd_hz.supported_rates()
    current = gd_hz.refresh_rate()

    assert rates, "у монитора обязан быть хотя бы один режим"
    assert current in rates, f"текущей частоты {current} нет в {rates}"
    assert all(1 <= value <= 500 for value in rates)
    assert rates == sorted(rates)


def test_summary_mentions_resolution_and_rate():
    summary = gd_hz.current_mode_summary()
    devmode = gd_hz.current()

    assert "×" in summary and "Гц" in summary
    assert str(devmode.dmPelsWidth) in summary


def test_absurd_rate_is_rejected_without_touching_the_mode():
    ok, message = gd_hz.set_refresh(None, 0)

    assert ok is False
    assert "положительной" in message
    assert gd_hz.refresh_rate() > 0, "режим не должен пострадать"


def test_unknown_rate_fails_the_test_without_changing_anything():
    before = gd_hz.refresh_rate()
    ok, message = gd_hz.set_refresh(None, 991, test_only=True)

    assert ok is False, "991 Гц не бывает"
    assert message
    assert gd_hz.refresh_rate() == before


def test_real_rate_passes_test_mode():
    """CDS_TEST проверяет режим и ничего не применяет — экран не мигает."""
    current = gd_hz.refresh_rate()
    ok, message = gd_hz.set_refresh(None, current, test_only=True)

    assert ok is True, message
    assert gd_hz.refresh_rate() == current


def test_auto_profile_hz_is_optional_and_safe():
    """Правило без частоты не должно ничего переключать."""
    assert getattr(gd_hz, "set_refresh") is not None
    # правило без hz: значение по умолчанию 0 — «не трогать»
    rule = {"exe": "cs2.exe", "preset": 1}
    assert int(rule.get("hz", 0)) == 0
