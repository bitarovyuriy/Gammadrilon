# -*- coding: utf-8 -*-
"""Сенсоры CPU через PDH (английские имена счётчиков — не зависят от локали).

Живая частота = Processor Frequency (номинал, МГц) x % Processor Performance / 100.
Температура: на этой системе без доп. ПО недоступна — честно отдаём None.
"""

from __future__ import annotations

import ctypes

pdh = ctypes.WinDLL("pdh.dll")

PDH_FMT_DOUBLE = 0x00000200


class _PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_ulong), ("doubleValue", ctypes.c_double)]


class CpuSensors:
    """Один экземпляр на процесс; sample() раз в ~1 сек."""

    def __init__(self) -> None:
        self._q = ctypes.c_void_p()
        self._counters: dict[str, ctypes.c_void_p] = {}
        self._ok = False
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._q)) != 0:
            return
        for name, path in (
            ("perf", r"\Processor Information(_Total)\% Processor Performance"),
            ("freq", r"\Processor Information(_Total)\Processor Frequency"),
            ("load", r"\Processor Information(_Total)\% Processor Time"),
        ):
            c = ctypes.c_void_p()
            if pdh.PdhAddEnglishCounterW(self._q, path, 0, ctypes.byref(c)) == 0:
                self._counters[name] = c
        self._ok = "perf" in self._counters and "freq" in self._counters
        if self._ok:
            pdh.PdhCollectQueryData(self._q)  # базовый замер

    def _read(self, name: str):
        c = self._counters.get(name)
        if not c:
            return None
        val = _PDH_FMT_COUNTERVALUE()
        t = ctypes.c_ulong()
        r = pdh.PdhGetFormattedCounterValue(c, PDH_FMT_DOUBLE, ctypes.byref(t), ctypes.byref(val))
        return val.doubleValue if r == 0 else None

    def sample(self) -> dict:
        """{'mhz': float|None, 'perf_pct': float|None, 'load_pct': float|None, 'temp_c': None}"""
        if not self._ok:
            return {"mhz": None, "perf_pct": None, "load_pct": None, "temp_c": None}
        pdh.PdhCollectQueryData(self._q)
        freq = self._read("freq")
        perf = self._read("perf")
        load = self._read("load")
        mhz = freq * perf / 100.0 if (freq and perf is not None) else None
        return {"mhz": mhz, "perf_pct": perf, "load_pct": load, "temp_c": None}

    def close(self) -> None:
        if self._q:
            pdh.PdhCloseQuery(self._q)
            self._q = ctypes.c_void_p()


def ram_usage():
    """Процент занятой оперативной памяти (None, если psutil недоступен)."""
    try:
        import psutil

        return psutil.virtual_memory().percent
    except Exception:  # noqa: BLE001
        return None
