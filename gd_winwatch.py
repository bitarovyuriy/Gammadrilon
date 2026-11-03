# -*- coding: utf-8 -*-
"""Смена активного окна: подписка на `EVENT_SYSTEM_FOREGROUND`.

Зачем: сторож гаммы и автопрофили должны срабатывать в момент перехода в игру,
а не «когда-нибудь на следующем тике таймера». Таймер остаётся страховкой, но
событие приходит сразу.

Требования к хосту Qt (важно, иначе `0xC0000409`):
  * хук ставится из GUI-потока — `SetWinEventHook` с `WINEVENT_OUTOFCONTEXT`
    доставляет колбэк в очередь сообщений того потока, который его поставил;
  * ссылка на `WINFUNCTYPE`-обёртку живёт в `self._proc`, пока хук не снят —
    иначе сборщик мусора освободит её, а WinAPI продолжит звать по адресу.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
WINEVENT_SKIPOWNTHREAD = 0x0001

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, ctypes.c_void_p,
                                    wt.DWORD, wt.DWORD, wt.DWORD]
_user32.SetWinEventHook.restype = wt.HANDLE
_user32.UnhookWinEvent.argtypes = [wt.HANDLE]
_user32.UnhookWinEvent.restype = wt.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_user32.GetWindowThreadProcessId.restype = wt.DWORD
_user32.GetForegroundWindow.restype = wt.HWND

_kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                 ctypes.POINTER(wt.DWORD)]
_kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.CloseHandle.restype = wt.BOOL

_WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG,
                                   wt.LONG, wt.DWORD, wt.DWORD)


def window_pid(hwnd: int) -> int:
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def process_path(pid: int) -> str:
    """Полный путь процесса по pid. Пустая строка — нет доступа/процесс умер."""
    if not pid:
        return ""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        _kernel32.CloseHandle(handle)


def foreground() -> tuple[int, int]:
    """(hwnd, pid) активного окна."""
    hwnd = int(_user32.GetForegroundWindow() or 0)
    return hwnd, window_pid(hwnd) if hwnd else 0


class ForegroundWatcher:
    """Зовёт `on_change(hwnd, pid)` при каждой смене активного окна.

    Свои окна отфильтрованы на уровне WinAPI (`WINEVENT_SKIPOWNPROCESS`),
    повторный вызов для того же hwnd — тоже (бывает при смене фокуса внутри).
    """

    def __init__(self, on_change) -> None:
        self._on_change = on_change
        self._hook = None
        self._proc = None
        self._last_hwnd = 0

    def start(self) -> bool:
        if self._hook:
            return True

        def _callback(_hook, _event, hwnd, _id_object, _id_child, _thread, _time):
            value = int(hwnd or 0)
            if not value or value == self._last_hwnd:
                return
            self._last_hwnd = value
            try:
                self._on_change(value, window_pid(value))
            except Exception:  # noqa: BLE001 — падать в колбэке WinAPI нельзя
                pass

        self._proc = _WINEVENTPROC(_callback)
        self._hook = _user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, self._proc,
            0, 0, WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
        if not self._hook:
            self._proc = None
            return False
        return True

    def stop(self) -> None:
        if self._hook:
            _user32.UnhookWinEvent(self._hook)
            self._hook = None
        self._proc = None
        self._last_hwnd = 0

    @property
    def active(self) -> bool:
        return bool(self._hook)
