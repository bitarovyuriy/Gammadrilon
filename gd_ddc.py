# -*- coding: utf-8 -*-
"""Яркость монитора по DDC/CI через `dxva2.dll`.

Зачем: гамма-кривая меняет картинку программно, а подсветка матрицы остаётся
яркой — в тёмной комнате это слепит. DDC/CI говорит с монитором по шине и
меняет именно подсветку.

Три правила, без которых это ломает UX:
  * вызовы медленные (до 200 мс) — только из фонового потока, никогда в GUI;
  * дескриптор монитора кэшируется: получение списка — самый дорогой шаг;
  * `DestroyPhysicalMonitors` обязателен, иначе драйвер копит дескрипторы.

Матрицы ноутбуков и часть мониторов DDC/CI не понимают — тогда честный отказ
(`supported=False`), а не ползунок, который ничего не делает.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt

MC_CAPS_BRIGHTNESS = 0x00000002

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
_user32.MonitorFromWindow.restype = wt.HANDLE
_user32.GetDesktopWindow.restype = wt.HWND

MONITOR_DEFAULTTOPRIMARY = 0x00000001


class _PhysicalMonitor(ctypes.Structure):
    _fields_ = [("handle", wt.HANDLE), ("description", wt.WCHAR * 128)]


def _load_dxva2():
    try:
        lib = ctypes.WinDLL("dxva2.dll", use_last_error=True)
    except OSError:
        return None
    lib.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wt.HANDLE,
                                                             ctypes.POINTER(wt.DWORD)]
    lib.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wt.BOOL
    lib.GetPhysicalMonitorsFromHMONITOR.argtypes = [wt.HANDLE, wt.DWORD,
                                                    ctypes.POINTER(_PhysicalMonitor)]
    lib.GetPhysicalMonitorsFromHMONITOR.restype = wt.BOOL
    lib.DestroyPhysicalMonitors.argtypes = [wt.DWORD, ctypes.POINTER(_PhysicalMonitor)]
    lib.DestroyPhysicalMonitors.restype = wt.BOOL
    lib.GetMonitorCapabilities.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD),
                                           ctypes.POINTER(wt.DWORD)]
    lib.GetMonitorCapabilities.restype = wt.BOOL
    lib.GetMonitorBrightness.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD),
                                         ctypes.POINTER(wt.DWORD),
                                         ctypes.POINTER(wt.DWORD)]
    lib.GetMonitorBrightness.restype = wt.BOOL
    lib.SetMonitorBrightness.argtypes = [wt.HANDLE, wt.DWORD]
    lib.SetMonitorBrightness.restype = wt.BOOL
    return lib


class Monitor:
    """Один физический монитор: яркость читается и ставится по индексу."""

    def __init__(self, lib, handle, description: str, index: int) -> None:
        self._lib = lib
        self._handle = handle
        self.description = description
        self.index = index
        self._min = 0
        self._max = 100
        self.supported = False
        self._probe()

    def _probe(self) -> None:
        capabilities = wt.DWORD()
        temperatures = wt.DWORD()
        if self._lib.GetMonitorCapabilities(self._handle, ctypes.byref(capabilities),
                                            ctypes.byref(temperatures)):
            if not capabilities.value & MC_CAPS_BRIGHTNESS:
                return
        elif not self._read_limits():
            return
        self.supported = True

    def _read_limits(self) -> bool:
        low = wt.DWORD()
        current = wt.DWORD()
        high = wt.DWORD()
        if not self._lib.GetMonitorBrightness(self._handle, ctypes.byref(low),
                                              ctypes.byref(current), ctypes.byref(high)):
            return False
        self._min, self._max = int(low.value), int(high.value)
        return True

    def brightness(self) -> int | None:
        """Текущая яркость в процентах (0..100). None — монитор не ответил."""
        if not self.supported:
            return None
        low = wt.DWORD()
        current = wt.DWORD()
        high = wt.DWORD()
        if not self._lib.GetMonitorBrightness(self._handle, ctypes.byref(low),
                                              ctypes.byref(current), ctypes.byref(high)):
            return None
        self._min, self._max = int(low.value), int(high.value)
        span = max(1, self._max - self._min)
        return round((int(current.value) - self._min) * 100 / span)

    def set_brightness(self, percent: float) -> bool:
        """Поставить яркость в процентах. False — монитор отказал."""
        if not self.supported:
            return False
        if self._max <= self._min:
            self._read_limits()
        span = max(1, self._max - self._min)
        value = self._min + round(max(0.0, min(100.0, float(percent))) * span / 100)
        return bool(self._lib.SetMonitorBrightness(self._handle, value))


class Ddc:
    """Список физических мониторов и освобождение их дескрипторов."""

    def __init__(self) -> None:
        self._lib = _load_dxva2()
        self._raw: list[_PhysicalMonitor] = []
        self._count = 0
        self.monitors: list[Monitor] = []
        self.problem = "" if self._lib is not None else "dxva2.dll не загрузилась"

    def load(self) -> bool:
        """Найти мониторы. Дешёвый повторный вызов — список уже в памяти."""
        if self.monitors or self._lib is None:
            return bool(self.monitors)
        monitor = _user32.MonitorFromWindow(_user32.GetDesktopWindow(),
                                            MONITOR_DEFAULTTOPRIMARY)
        if not monitor:
            self.problem = "окно рабочего стола не найдено"
            return False
        count = wt.DWORD()
        if not self._lib.GetNumberOfPhysicalMonitorsFromHMONITOR(
                monitor, ctypes.byref(count)) or count.value == 0:
            self.problem = "мониторы не найдены"
            return False
        raw = (_PhysicalMonitor * count.value)()
        if not self._lib.GetPhysicalMonitorsFromHMONITOR(monitor, count.value, raw):
            self.problem = "дескрипторы мониторов не получены"
            return False
        self._raw = raw
        self._count = count.value
        self.monitors = [Monitor(self._lib, raw[i].handle, raw[i].description, i)
                         for i in range(count.value)]
        if not any(m.supported for m in self.monitors):
            self.problem = "монитор не умеет DDC/CI"
        return True

    def main(self) -> Monitor | None:
        """Основной монитор: первый, который умеет яркость."""
        for monitor in self.monitors:
            if monitor.supported:
                return monitor
        return None

    def close(self) -> None:
        if self._lib is not None and self._raw:
            try:
                self._lib.DestroyPhysicalMonitors(self._count, self._raw)
            except Exception:  # noqa: BLE001 — при выходе уже неважно
                pass
        self._raw = []
        self._count = 0
        self.monitors = []

    def __enter__(self):
        self.load()
        return self

    def __exit__(self, *_exc):
        self.close()
        return False
