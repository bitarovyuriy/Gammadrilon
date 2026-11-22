"""On-demand temperatures; no background process or timer is started here."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import math
import os
import sys
import time
from pathlib import Path

SAMPLE_FRESH = 5.0
_cached_cpu: tuple[float, float] | None = None


def _temperature(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0 < value <= 150 else None


def cpu_temperature() -> float | None:
    """Read only a fresh in-process or full-version, non-portable sample."""
    if _cached_cpu is not None and 0 <= time.monotonic() - _cached_cpu[1] <= SAMPLE_FRESH:
        return _cached_cpu[0]
    sample = Path(os.environ.get('ProgramData', r'C:\ProgramData')) / 'Gammadrilon' / 'sensors.json'
    try:
        age = time.time() - sample.stat().st_mtime
        if not 0 <= age <= SAMPLE_FRESH:
            return None
        data = json.loads(sample.read_text(encoding='utf-8'))
        if isinstance(data, dict) and data.get('ok') is True:
            return _temperature(data.get('cpu'))
    except (OSError, ValueError, TypeError):
        pass
    return None


def _nvml():
    import gd_nvml
    return gd_nvml.Nvml()


def gpu_temperature() -> float | None:
    """Take a single NVIDIA GPU sample, releasing NVML afterwards."""
    gpu = None
    try:
        gpu = _nvml()
        if not gpu.available():
            return None
        fn = gpu._lib.nvmlDeviceGetTemperature
        fn.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
        fn.restype = ctypes.c_int
        value = ctypes.c_uint()
        if fn(gpu._device, 0, ctypes.byref(value)) == 0:
            return _temperature(value.value)
    except (OSError, AttributeError, TypeError, ValueError):
        pass
    finally:
        if gpu is not None:
            gpu.shutdown()
    return None


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG),
        ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
        ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
        ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
        ('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
        ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
        ('dwHotKey', wintypes.DWORD), ('hIconOrMonitor', wintypes.HANDLE),
        ('hProcess', wintypes.HANDLE),
    ]


def _run_helper() -> int:
    """Run a fixed, bundled executable via UAC and wait; never pass paths to it."""
    helper = Path(__file__).resolve().parent / 'vendor' / 'lhm' / 'LiteSensors.exe'
    if not helper.is_file() and not getattr(sys, 'frozen', False):
        helper = helper.parents[2] / 'build' / 'sensor-run' / 'LiteSensors.exe'
    if not helper.is_file():
        raise RuntimeError('Нет модуля замера CPU. Запусти собранную версию Lite.')
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000400  # NOCLOSEPROCESS | NOASYNC
    info.lpVerb = 'runas'
    info.lpFile = str(helper)
    info.lpParameters = '--once'
    info.lpDirectory = str(helper.parent)
    info.nShow = 0
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == 1223:
            raise RuntimeError('Замер CPU отменён.')
        raise RuntimeError(f'Не удалось запустить замер CPU (код Windows {code}).')
    if not info.hProcess:
        raise RuntimeError('Windows не вернула дескриптор замерщика CPU.')
    try:
        if kernel.WaitForSingleObject(info.hProcess, 30000) != 0:
            raise RuntimeError('Не дождались замера CPU.')
        result = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(result)):
            raise RuntimeError('Не удалось прочитать результат замера CPU.')
        return result.value
    finally:
        kernel.CloseHandle(info.hProcess)


def cpu_measure() -> float | str:
    """Explicit, potentially UAC-prompting blocking action; call on a worker thread."""
    global _cached_cpu
    try:
        code = _run_helper()
    except (OSError, RuntimeError) as exc:
        return str(exc)
    if not 3001 <= code <= 4500:
        return 'Нет данных CPU. Проверь драйвер PawnIO.'
    value = (code - 3000) / 10.0
    _cached_cpu = (value, time.monotonic())
    return value
