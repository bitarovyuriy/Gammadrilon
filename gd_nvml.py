# -*- coding: utf-8 -*-
"""NVIDIA Management Library: телеметрия и лимит без запуска `nvidia-smi.exe`.

Зачем: раньше каждое обновление вкладки порождало процесс `nvidia-smi.exe`
(~40 мс CPU за запуск). NVML — та же информация из драйвера, но вызовом
функции, без процесса и без консоли.

Модуль ничего не знает про Qt. Инициализация — один раз на процесс, при
выходе :meth:`Nvml.shutdown`. Если библиотеки нет (AMD/Intel, урезанный
драйвер), все методы честно говорят «недоступно», а вызывающий откатывается
на старый путь.
"""
from __future__ import annotations

import ctypes
import os

import gd_i18n as i18n

#: NVML_TEMPERATURE_GPU
TEMPERATURE_GPU = 0
#: NVML_CLOCK_GRAPHICS / NVML_CLOCK_MEM
CLOCK_GRAPHICS = 0
CLOCK_MEM = 2

SUCCESS = 0
ERROR_NOT_SUPPORTED = 3


class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _Memory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong)]


def _library_path() -> str | None:
    """Где лежит nvml.dll: System32, затем старая папка NVSMI."""
    candidates = [
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "NVIDIA Corporation", "NVSMI", "nvml.dll"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class Nvml:
    """Тонкая обёртка: только то, что показывает вкладка GPU."""

    def __init__(self, gpu_uuid: str | None = None) -> None:
        self._lib = None
        self._device = ctypes.c_void_p()
        self._ready = False
        self._problem = ""
        self._bind(gpu_uuid)

    def _bind(self, gpu_uuid: str | None) -> None:
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
        lib.nvmlDeviceGetHandleByUUID.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        lib.nvmlDeviceGetHandleByUUID.restype = ctypes.c_int
        lib.nvmlDeviceGetUUID.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
        lib.nvmlDeviceGetUUID.restype = ctypes.c_int
        lib.nvmlDeviceGetName.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
        lib.nvmlDeviceGetName.restype = ctypes.c_int
        lib.nvmlSystemGetDriverVersion.argtypes = [ctypes.c_char_p, ctypes.c_uint]
        lib.nvmlSystemGetDriverVersion.restype = ctypes.c_int
        lib.nvmlDeviceGetTemperature.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                 ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetTemperature.restype = ctypes.c_int
        lib.nvmlDeviceGetClockInfo.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                               ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetClockInfo.restype = ctypes.c_int
        lib.nvmlDeviceGetMaxClockInfo.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                  ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetMaxClockInfo.restype = ctypes.c_int
        lib.nvmlDeviceGetPowerUsage.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetPowerUsage.restype = ctypes.c_int
        lib.nvmlDeviceGetPowerManagementLimit.argtypes = [ctypes.c_void_p,
                                                          ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetPowerManagementLimit.restype = ctypes.c_int
        lib.nvmlDeviceGetPowerManagementLimitConstraints.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetPowerManagementLimitConstraints.restype = ctypes.c_int
        lib.nvmlDeviceGetPowerManagementDefaultLimit.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetPowerManagementDefaultLimit.restype = ctypes.c_int
        lib.nvmlDeviceGetUtilizationRates.argtypes = [ctypes.c_void_p,
                                                      ctypes.POINTER(_Utilization)]
        lib.nvmlDeviceGetUtilizationRates.restype = ctypes.c_int
        lib.nvmlDeviceGetMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Memory)]
        lib.nvmlDeviceGetMemoryInfo.restype = ctypes.c_int
        lib.nvmlDeviceGetFanSpeed.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetFanSpeed.restype = ctypes.c_int
        lib.nvmlDeviceSetPowerManagementLimit.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        lib.nvmlDeviceSetPowerManagementLimit.restype = ctypes.c_int

        if lib.nvmlInit_v2() != SUCCESS:
            self._problem = i18n.t("nvmlInit не удался (нет драйвера NVIDIA?)")
            return
        result = (lib.nvmlDeviceGetHandleByUUID(gpu_uuid.encode("ascii"), ctypes.byref(self._device))
                  if gpu_uuid else lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self._device)))
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

    def name(self) -> str:
        if not self._ready:
            return ""
        buffer = ctypes.create_string_buffer(96)
        if self._lib.nvmlDeviceGetName(self._device, buffer, len(buffer)) != SUCCESS:
            return ""
        return buffer.value.decode("utf-8", "replace").strip()

    def driver(self) -> str:
        if not self._ready:
            return ""
        buffer = ctypes.create_string_buffer(80)
        if self._lib.nvmlSystemGetDriverVersion(buffer, len(buffer)) != SUCCESS:
            return ""
        return buffer.value.decode("utf-8", "replace").strip()

    def uuid(self) -> str | None:
        if not self._ready:
            return None
        buffer = ctypes.create_string_buffer(96)
        if self._lib.nvmlDeviceGetUUID(self._device, buffer, len(buffer)) != SUCCESS:
            return None
        return buffer.value.decode("ascii")

    def _uint(self, fn, *args) -> int | None:
        value = ctypes.c_uint()
        if fn(self._device, *args, ctypes.byref(value)) != SUCCESS:
            return None
        return int(value.value)

    def read(self) -> dict | None:
        """Снимок в том же формате, что отдавал разбор `nvidia-smi`."""
        if not self._ready:
            return None
        power_mw = self._uint(self._lib.nvmlDeviceGetPowerUsage)
        limit_mw = self._uint(self._lib.nvmlDeviceGetPowerManagementLimit)
        default_mw = self._uint(self._lib.nvmlDeviceGetPowerManagementDefaultLimit)

        min_mw = max_mw = None
        low = ctypes.c_uint()
        high = ctypes.c_uint()
        if self._lib.nvmlDeviceGetPowerManagementLimitConstraints(
                self._device, ctypes.byref(low), ctypes.byref(high)) == SUCCESS:
            min_mw, max_mw = int(low.value), int(high.value)

        util = _Utilization()
        if self._lib.nvmlDeviceGetUtilizationRates(self._device, ctypes.byref(util)) != SUCCESS:
            util = None
        memory = _Memory()
        if self._lib.nvmlDeviceGetMemoryInfo(self._device, ctypes.byref(memory)) != SUCCESS:
            memory = None

        def watts(value):
            return value / 1000 if value is not None else None

        return {
            "name": self.name(),
            "driver": self.driver(),
            "uuid": self.uuid(),
            "pl": watts(limit_mw),
            "pl_min": watts(min_mw),
            "pl_max": watts(max_mw),
            "pl_default": watts(default_mw),
            "clock": self._uint(self._lib.nvmlDeviceGetClockInfo, CLOCK_GRAPHICS),
            "clock_max": self._uint(self._lib.nvmlDeviceGetMaxClockInfo, CLOCK_GRAPHICS),
            "clock_mem": self._uint(self._lib.nvmlDeviceGetClockInfo, CLOCK_MEM),
            "temp": self._uint(self._lib.nvmlDeviceGetTemperature, TEMPERATURE_GPU),
            "util": float(util.gpu) if util is not None else None,
            "mem_used": round(memory.used / 1024 ** 2) if memory is not None else None,
            "mem_total": round(memory.total / 1024 ** 2) if memory is not None else None,
            "fan": self._uint(self._lib.nvmlDeviceGetFanSpeed),
            "power": round(power_mw / 1000, 1) if power_mw is not None else None,
        }

    # -- изменение ---------------------------------------------------------
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
