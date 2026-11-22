"""Launch only the current app and wait on its process handle (no IPC listener)."""
from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes as w
from pathlib import Path

from gd_lite_actions import validate_action


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ('cbSize', w.DWORD), ('fMask', w.ULONG), ('hwnd', w.HWND),
        ('lpVerb', w.LPCWSTR), ('lpFile', w.LPCWSTR), ('lpParameters', w.LPCWSTR),
        ('lpDirectory', w.LPCWSTR), ('nShow', ctypes.c_int), ('hInstApp', w.HINSTANCE),
        ('lpIDList', ctypes.c_void_p), ('lpClass', w.LPCWSTR), ('hkeyClass', w.HKEY),
        ('dwHotKey', w.DWORD), ('hIcon', w.HANDLE), ('hProcess', w.HANDLE),
    ]


def elevated_apply(section: str, value: str) -> None:
    validate_action(section, value)
    _launch(['--apply', section, value])


def elevated_zapret(settings) -> None:
    cfg = settings.validated()
    _launch(['--zapret-settings', cfg.strategy, cfg.game, cfg.tcp, cfg.udp, cfg.ipset])


def _launch(args: list[str]) -> None:
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = w.BOOL
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.GetExitCodeProcess.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    system = ctypes.create_unicode_buffer(32768)
    kernel.GetSystemDirectoryW.argtypes = [w.LPWSTR, w.UINT]
    kernel.GetSystemDirectoryW.restype = w.UINT
    if not 0 < kernel.GetSystemDirectoryW(system, len(system)) < len(system):
        raise ctypes.WinError(ctypes.get_last_error())

    if not getattr(sys, 'frozen', False):
        args[:0] = ['-E', '-s', str(Path(__file__).resolve().with_name('main.py'))]
    info = _ShellExecuteInfo(cbSize=ctypes.sizeof(_ShellExecuteInfo), fMask=0x40 | 0x100 | 0x400,
                             lpVerb='runas', lpFile=sys.executable, lpParameters=subprocess.list2cmdline(args),
                             lpDirectory=system.value, nShow=0)
    ole = ctypes.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, w.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 2 | 4)
    if initialized < 0:
        raise OSError(f'Не удалось инициализировать COM: {initialized & 0xFFFFFFFF:#x}')
    try:
        launched = shell.ShellExecuteExW(ctypes.byref(info))
        error = ctypes.get_last_error()
    finally:
        ole.CoUninitialize()
    if not launched:
        if error == 1223:
            raise RuntimeError('Запрос прав администратора отменён.')
        raise ctypes.WinError(error)
    if not info.hProcess:
        raise RuntimeError('Windows не вернула дескриптор процесса.')
    try:
        if kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF) != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = w.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if code.value == 3:
            raise RuntimeError('Windows не выдала права администратора.')
        if code.value:
            raise RuntimeError(f'Изменение не подтверждено (код {code.value}).')
    finally:
        kernel.CloseHandle(info.hProcess)
