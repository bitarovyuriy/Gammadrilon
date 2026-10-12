# -*- coding: utf-8 -*-
"""Состояние HDR (advanced color) активных мониторов — для честных сообщений.

Windows не разрешает менять гамму (SetDeviceGammaRamp), пока на мониторе
включён HDR: API возвращает FALSE, и без объяснения это выглядит как поломка.
Состояние читается через DisplayConfig API
(DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO, Windows 10 1803+).

Возврат: True — HDR включён хотя бы на одном мониторе, False — точно выключен,
None — система не дала ответ (старая или нестандартная конфигурация).
"""
from __future__ import annotations

import ctypes

_QDC_ONLY_ACTIVE_PATHS = 0x00000002
_GET_ADVANCED_COLOR_INFO = 9

# размеры и смещения фиксированы заголовком Windows (displayconfig.h)
_PATH_INFO_SIZE = 72          # sourceInfo(20) + targetInfo(48) + flags(4)
_MODE_INFO_SIZE = 64          # infoType(4) + id(4) + adapterId(8) + union(48)
_TARGET_ADAPTER_OFFSET = 20   # LUID адаптера внутри DISPLAYCONFIG_PATH_INFO
_TARGET_ID_OFFSET = 28        # id таргета внутри DISPLAYCONFIG_PATH_INFO


class _PathInfo(ctypes.Structure):
    _fields_ = [("raw", ctypes.c_ubyte * _PATH_INFO_SIZE)]


class _ModeInfo(ctypes.Structure):
    _fields_ = [("raw", ctypes.c_ubyte * _MODE_INFO_SIZE)]


class _DeviceInfoHeader(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("size", ctypes.c_uint32),
        ("adapterId", ctypes.c_ubyte * 8),
        ("id", ctypes.c_uint32),
    ]


class _AdvancedColorInfo(ctypes.Structure):
    _fields_ = [
        ("header", _DeviceInfoHeader),
        ("value", ctypes.c_uint32),
    ]


def hdr_active() -> bool | None:
    """HDR включён хотя бы на одном активном мониторе? None — не удалось узнать."""
    try:
        return _hdr_active_impl()
    except Exception:  # noqa: BLE001 — диагностика не должна мешать работе
        return None


def _hdr_active_impl() -> bool | None:
    user32 = ctypes.WinDLL("user32")
    user32.GetDisplayConfigBufferSizes.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    user32.GetDisplayConfigBufferSizes.restype = ctypes.c_int32
    user32.QueryDisplayConfig.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    user32.QueryDisplayConfig.restype = ctypes.c_int32
    user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
    user32.DisplayConfigGetDeviceInfo.restype = ctypes.c_int32

    path_count = ctypes.c_uint32(0)
    mode_count = ctypes.c_uint32(0)
    rc = user32.GetDisplayConfigBufferSizes(
        _QDC_ONLY_ACTIVE_PATHS, ctypes.byref(path_count), ctypes.byref(mode_count)
    )
    if rc != 0 or not path_count.value:
        return None

    paths = (_PathInfo * path_count.value)()
    modes = (_ModeInfo * mode_count.value)()
    rc = user32.QueryDisplayConfig(
        _QDC_ONLY_ACTIVE_PATHS,
        ctypes.byref(path_count),
        ctypes.cast(paths, ctypes.c_void_p),
        ctypes.byref(mode_count),
        ctypes.cast(modes, ctypes.c_void_p),
        None,
    )
    if rc != 0:
        return None

    answered = False
    for i in range(path_count.value):
        raw = paths[i].raw
        info = _AdvancedColorInfo()
        info.header.type = _GET_ADVANCED_COLOR_INFO
        info.header.size = ctypes.sizeof(_AdvancedColorInfo)
        ctypes.memmove(info.header.adapterId, ctypes.addressof(raw) + _TARGET_ADAPTER_OFFSET, 8)
        info.header.id = ctypes.c_uint32.from_buffer(raw, _TARGET_ID_OFFSET).value
        if user32.DisplayConfigGetDeviceInfo(ctypes.byref(info)) != 0:
            continue
        answered = True
        if (info.value >> 1) & 1:   # advancedColorEnabled
            return True
    return False if answered else None
