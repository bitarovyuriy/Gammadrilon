# -*- coding: utf-8 -*-
"""Сенсоры CPU: частота и загрузка через PDH, температуры — через LibreHardwareMonitor.

Живая частота = Processor Frequency (номинал, МГц) x % Processor Performance / 100.

Температура: Windows сама её не отдаёт (ACPI-зоны на десктопных платах пустые), а
MSR/SMN процессора и Super I/O платы доступны только из ring0. Поэтому рядом с
приложением лежит хелпер `GammadrilonSensors.exe` (C#, LibreHardwareMonitor) —
он работает через подписанный драйвер PawnIO, снимает замер раз в секунду и
пишет строку JSON в `data_dir()/sensors.json`. Здесь мы эту строку читаем.

Разделение процессов не от роскоши: драйвер требует прав администратора, а само
окно Gammadrilon работает обычным пользователем. Хелпер поднимается один раз с
повышением прав и выходит вместе с окном (следит за PID родителя).
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path

import gd_paths

pdh = ctypes.WinDLL("pdh.dll")

PDH_FMT_DOUBLE = 0x00000200

#: сколько секунд файл замера считается свежим
SAMPLE_FRESH = 5.0
#: не чаще этого пытаемся поднять хелпер заново (секунды)
START_BACKOFF = 20.0

_start_ts = 0.0
#: какой канал платы считать VRM (0 — не показывать); ставится из настроек
_vrm_channel = 0
_last_good: dict = {"cpu": None, "cpu_max": None, "vrm": None, "board": [], "ok": False, "error": ""}


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
        """{'mhz','perf_pct','load_pct','temp_c','vrm_c'} — пустые поля, если данных нет."""
        temps = read()
        temp = temps.get("cpu")
        vrm = _vrm_value(temps)
        if not self._ok:
            return {"mhz": None, "perf_pct": None, "load_pct": None,
                    "temp_c": temp, "vrm_c": vrm}
        pdh.PdhCollectQueryData(self._q)
        freq = self._read("freq")
        perf = self._read("perf")
        load = self._read("load")
        mhz = freq * perf / 100.0 if (freq and perf is not None) else None
        return {"mhz": mhz, "perf_pct": perf, "load_pct": load,
                "temp_c": temp, "vrm_c": vrm}

    def close(self) -> None:
        if self._q:
            pdh.PdhCloseQuery(self._q)
            self._q = ctypes.c_void_p()


def _vrm_value(temps: dict) -> float | None:
    """Температура VRM: либо датчик с именем VRM, либо выбранный вручную канал платы.

    На бюджетных платах VRM-термистора нет, и Super I/O отдаёт безымянные каналы
    «Temperature #1…#6». Какой из них VRM — знает только человек, поэтому выбор
    живёт в настройках (`settings.sensors_vrm_channel`, 1..6, 0 — выключено).
    """
    direct = temps.get("vrm")
    if direct is not None:
        return direct
    if _vrm_channel <= 0:
        return None
    board = temps.get("board") or []
    for item in board:
        label, _, raw = str(item).partition("=")
        if label.strip() == f"Temperature #{_vrm_channel}":
            try:
                return float(raw)
            except ValueError:
                return None
    return None


def set_vrm_channel(channel: int) -> None:
    """Какой канал платы считать VRM (0 — не показывать)."""
    global _vrm_channel
    try:
        _vrm_channel = max(0, int(channel))
    except (TypeError, ValueError):
        _vrm_channel = 0


def board_channels() -> list[tuple[str, float]]:
    """Все температурные каналы платы: [('Temperature #1', 32.0), ...]."""
    out: list[tuple[str, float]] = []
    for item in read().get("board") or []:
        label, _, raw = str(item).partition("=")
        try:
            out.append((label.strip(), float(raw)))
        except ValueError:
            continue
    return out


def helper_path() -> Path | None:
    """Где лежит GammadrilonSensors.exe — рядом с приложением или в vendor/lhm."""
    bases: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).resolve().parent)
    bases.append(Path(__file__).resolve().parent)
    for base in bases:
        for cand in (base / "vendor" / "lhm" / "GammadrilonSensors.exe",
                     base / "GammadrilonSensors.exe"):
            if cand.is_file():
                return cand
    return None


def sample_path() -> Path:
    """Файл, куда хелпер пишет замер (один на пользователя)."""
    return gd_paths.data_dir() / "sensors.json"


def driver_present() -> bool:
    """Установлен ли драйвер PawnIO (без него хелпер видит нули)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Services\PawnIO"):
            return True
    except OSError:
        return False


def service_running() -> bool:
    """Хелпер жив, если файл замера свежий."""
    try:
        return (time.time() - sample_path().stat().st_mtime) < SAMPLE_FRESH
    except OSError:
        return False


def start(force: bool = False) -> bool:
    """Поднять хелпер с повышением прав. True — если он уже пишет замеры."""
    global _start_ts
    if not driver_present():
        return False
    if service_running() and not force:
        return True
    now = time.monotonic()
    if not force and now - _start_ts < START_BACKOFF:
        return False
    helper = helper_path()
    if helper is None:
        return False
    _start_ts = now
    args = f'--stream 1000 --out "{sample_path()}" --parent {os.getpid()}'
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(helper), args, str(helper.parent), 0)
    except Exception:  # noqa: BLE001 — оболочка недоступна
        return False
    return int(rc) > 32


def stop() -> None:
    """Ничего не делает намеренно: хелпер сам уходит, когда закрывается окно.

    Убить его из непривилегированного процесса всё равно нельзя, а PID родителя
    он знает из командной строки (`--parent`).
    """
    return None


def read() -> dict:
    """Последний замер температур. Не блокирует и не бросает исключений."""
    global _last_good
    try:
        text = sample_path().read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError):
        return dict(_last_good)
    if not isinstance(data, dict):
        return dict(_last_good)
    if data.get("ok"):
        _last_good = {
            "cpu": data.get("cpu"),
            "cpu_max": data.get("cpu_max"),
            "vrm": data.get("vrm"),
            "board": data.get("board") or [],
            "ok": True,
            "error": "",
        }
    else:
        _last_good = dict(_last_good, ok=False, error=str(data.get("error") or ""))
    return dict(_last_good)


def ram_usage():
    """Процент занятой оперативной памяти (None, если psutil недоступен)."""
    try:
        import psutil

        return psutil.virtual_memory().percent
    except Exception:  # noqa: BLE001
        return None
