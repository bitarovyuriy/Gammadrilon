# -*- coding: utf-8 -*-
"""On-demand NVIDIA power limit only."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

import gd_i18n as i18n

SUCCESS = 0
ERROR_NOT_SUPPORTED = 3


def _library_path() -> str | None:
    """Never load a DLL from a user-supplied directory in the elevated worker."""
    system = ctypes.create_unicode_buffer(32768)
    get_directory = ctypes.WinDLL('kernel32').GetSystemDirectoryW
    get_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    get_directory.restype = wintypes.UINT
    if not 0 < get_directory(system, len(system)) < len(system):
        return None
    path = Path(system.value) / 'nvml.dll'
    return str(path) if path.is_file() else None


class Nvml:
    """Тонкая обёртка: только то, что показывает вкладка GPU."""

    def __init__(self) -> None:
        self._lib = None
        self._device = ctypes.c_void_p()
        self._ready = False
        self._problem = ""
        self._bind()

    def _bind(self) -> None:
        path = _library_path()
        if path is None:
            self._problem = i18n.t("nvml.dll не найдена")
            return
        try:
            lib = ctypes.WinDLL(path)
        except OSError as exc:
            self._problem = i18n.t("nvml.dll не загрузилась: {v0}", v0=exc)
            return

        lib.nvmlInit_v2.restype = ctypes.c_int
        lib.nvmlShutdown.restype = ctypes.c_int
        lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint,
                                                      ctypes.POINTER(ctypes.c_void_p)]
        lib.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int
        lib.nvmlDeviceGetPowerManagementLimit.argtypes = [ctypes.c_void_p,
                                                          ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetPowerManagementLimit.restype = ctypes.c_int
        lib.nvmlDeviceGetPowerManagementLimitConstraints.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetPowerManagementLimitConstraints.restype = ctypes.c_int
        lib.nvmlDeviceGetPowerManagementDefaultLimit.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetPowerManagementDefaultLimit.restype = ctypes.c_int
        lib.nvmlDeviceSetPowerManagementLimit.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        lib.nvmlDeviceSetPowerManagementLimit.restype = ctypes.c_int

        if lib.nvmlInit_v2() != SUCCESS:
            self._problem = i18n.t("nvmlInit не удался (нет драйвера NVIDIA?)")
            return
        result = lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self._device))
        if result != SUCCESS:
            self._problem = i18n.t("видеокарта не открылась через NVML")
            lib.nvmlShutdown()
            return
        self._lib = lib
        self._ready = True

    # -- состояние ---------------------------------------------------------
    def available(self) -> bool:
        return self._ready

    def problem(self) -> str:
        return self._problem

    def set_power_limit(self, watts: float) -> tuple[bool, str]:
        """Установить лимит. Требует прав администратора — вызывать из компаньона."""
        if not self._ready:
            return False, self._problem or i18n.t("NVML недоступен")
        result = self._lib.nvmlDeviceSetPowerManagementLimit(
            self._device, int(round(float(watts) * 1000)))
        if result == SUCCESS:
            return True, i18n.t("лимит {v0} Вт установлен через NVML", v0=watts)
        if result == ERROR_NOT_SUPPORTED:
            return False, i18n.t("драйвер не поддерживает смену лимита через NVML")
        return False, i18n.t("NVML вернул код {v0}", v0=result)

    def shutdown(self) -> None:
        if self._ready and self._lib is not None:
            try:
                self._lib.nvmlShutdown()
            except Exception:  # noqa: BLE001 — при выходе уже всё равно
                pass
        self._ready = False
