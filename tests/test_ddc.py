# -*- coding: utf-8 -*-
"""DDC/CI: перевод процентов в диапазон монитора и честный отказ.

Настоящий dxva2 не трогаем — яркость монитора в тестах менять нельзя.
Проверяем арифметику диапазона и поведение, когда матрица яркость не умеет.
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_ddc  # noqa: E402


class FakeLib:
    """Ровно те методы dxva2, которые зовёт gd_ddc."""

    def __init__(self, caps: int = gd_ddc.MC_CAPS_BRIGHTNESS, low: int = 10,
                 high: int = 90, current: int = 50, reply: bool = True) -> None:
        self.caps = caps
        self.low = low
        self.high = high
        self.current = current
        self.reply = reply
        self.set_calls: list[int] = []

    def GetMonitorCapabilities(self, _handle, capabilities, _temperatures):  # noqa: N802
        if not self.reply:
            return 0
        capabilities._obj.value = self.caps
        return 1

    def GetMonitorBrightness(self, _handle, low, current, high):  # noqa: N802
        if not self.reply:
            return 0
        low._obj.value = self.low
        current._obj.value = self.current
        high._obj.value = self.high
        return 1

    def SetMonitorBrightness(self, _handle, value):  # noqa: N802
        if not self.reply:
            return 0
        if not self.low <= value <= self.high:
            return 0
        self.current = value
        self.set_calls.append(value)
        return 1


def make_monitor(lib: FakeLib) -> gd_ddc.Monitor:
    return gd_ddc.Monitor(lib, ctypes.c_void_p(1), "Fake panel", 0)


def test_percent_maps_to_monitor_range():
    lib = FakeLib(low=10, high=90, current=50)
    monitor = make_monitor(lib)

    assert monitor.supported is True
    assert monitor.brightness() == 50

    assert monitor.set_brightness(100) is True
    assert lib.set_calls[-1] == 90, "100% — это верхняя граница монитора, не 100"
    assert monitor.set_brightness(0) is True
    assert lib.set_calls[-1] == 10, "0% — нижняя граница монитора"


def test_brightness_reads_back_in_percent():
    lib = FakeLib(low=0, high=200, current=50)
    monitor = make_monitor(lib)

    assert monitor.brightness() == 25, "50 из 0..200 — это четверть"
    monitor.set_brightness(75)
    assert lib.current == 150


def test_monitor_without_brightness_capability_is_honest():
    lib = FakeLib(caps=0, reply=True)
    monitor = make_monitor(lib)

    assert monitor.supported is False
    assert monitor.brightness() is None
    assert monitor.set_brightness(40) is False
    assert lib.set_calls == []


def test_monitor_that_does_not_answer_is_honest():
    lib = FakeLib(reply=False)
    monitor = make_monitor(lib)

    assert monitor.supported is False
    assert monitor.set_brightness(40) is False


def test_out_of_range_percent_is_clamped():
    lib = FakeLib(low=0, high=100, current=50)
    monitor = make_monitor(lib)

    assert monitor.set_brightness(140) is True
    assert lib.set_calls[-1] == 100
    assert monitor.set_brightness(-20) is True
    assert lib.set_calls[-1] == 0


def test_ddc_without_dxva2_explains_itself(monkeypatch):
    monkeypatch.setattr(gd_ddc, "_load_dxva2", lambda: None)
    ddc = gd_ddc.Ddc()

    assert ddc.load() is False
    assert "dxva2" in ddc.problem
    assert ddc.main() is None
    ddc.close()  # без библиотеки закрытие безопасно


def test_close_releases_handles(monkeypatch):
    released = []

    class Lib(FakeLib):
        def DestroyPhysicalMonitors(self, count, raw):  # noqa: N802
            released.append(count)
            return 1

    lib = Lib()
    ddc = gd_ddc.Ddc()
    ddc._lib = lib
    raw = (gd_ddc._PhysicalMonitor * 1)()
    ddc._raw = raw
    ddc._count = 1
    ddc.monitors = [make_monitor(lib)]

    ddc.close()

    assert released == [1], "дескрипторы обязаны освобождаться"
    assert ddc.monitors == []


def test_main_picks_supported_monitor():
    good = FakeLib()
    bad = FakeLib(caps=0)
    ddc = gd_ddc.Ddc()
    ddc.monitors = [make_monitor(bad), make_monitor(good)]

    assert ddc.main() is not None
    assert ddc.main().supported is True


def test_preset_remembers_backlight_level():
    """Пресет «ночь» меняет и матрицу, а не только кривую."""
    import main as gd_main

    clean, dropped = gd_main.MainWindow._sanitize_presets(
        [{"name": "Ночь", "ddc": 15}, {"name": "День", "ddc": 250}])

    assert dropped == 0
    assert clean[0]["ddc"] == 15
    assert clean[1]["ddc"] == 100, "запредел обрезается, импорт не падает"


def test_preset_without_ddc_means_leave_alone():
    import main as gd_main

    clean, _dropped = gd_main.MainWindow._sanitize_presets([{"name": "Работа"}])

    assert clean[0]["ddc"] == -1, "без поля подсветку не трогаем"
