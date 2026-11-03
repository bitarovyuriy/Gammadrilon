# -*- coding: utf-8 -*-
"""Частота обновления монитора: смена герцовки без потери режима.

Главная ловушка `ChangeDisplaySettingsExW` — передать в структуру только
частоту и нулевое разрешение: Windows применит «свой» режим, и у игрока
слетит растянутое 4:3, разрядность цвета или масштабирование GPU.

Поэтому база всегда берётся из `EnumDisplaySettingsW(ENUM_CURRENT_SETTINGS)`,
а меняется ровно одно поле — `dmDisplayFrequency` с флагом
`DM_DISPLAYFREQUENCY`. Всё остальное (разрешение, битность, ориентация,
`dmDisplayFixedOutput`) остаётся как было.

`CDS_TEST` позволяет проверить режим, ничего не применяя: монитор может
не поддерживать частоту, и тогда лучше честно сказать об этом.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt

ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
DM_DISPLAYFREQUENCY = 0x00400000
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_BITSPERPEL = 0x00040000

CDS_UPDATEREGISTRY = 0x00000001
CDS_TEST = 0x00000002

DISP_CHANGE_SUCCESSFUL = 0
DISP_CHANGE_RESTART = 1
DISP_CHANGE_FAILED = -1
DISP_CHANGE_BADMODE = -2
DISP_CHANGE_NOTUPDATED = -3
DISP_CHANGE_BADFLAGS = -4
DISP_CHANGE_BADPARAM = -5

CODES = {
    DISP_CHANGE_SUCCESSFUL: "режим применён",
    DISP_CHANGE_RESTART: "нужна перезагрузка",
    DISP_CHANGE_FAILED: "драйвер отказал в режиме",
    DISP_CHANGE_BADMODE: "монитор не поддерживает такую частоту",
    DISP_CHANGE_NOTUPDATED: "режим не записан в реестр",
    DISP_CHANGE_BADFLAGS: "неверные флаги вызова",
    DISP_CHANGE_BADPARAM: "неверные параметры режима",
}

_user32 = ctypes.WinDLL("user32", use_last_error=True)


class DEVMODEW(ctypes.Structure):
    """Полная структура: `dmSize` должен совпадать, иначе драйвер откажет."""

    _fields_ = [
        ("dmDeviceName", wt.WCHAR * 32),
        ("dmSpecVersion", wt.WORD),
        ("dmDriverVersion", wt.WORD),
        ("dmSize", wt.WORD),
        ("dmDriverExtra", wt.WORD),
        ("dmFields", wt.DWORD),
        ("dmPositionX", wt.LONG),
        ("dmPositionY", wt.LONG),
        ("dmDisplayOrientation", wt.DWORD),
        ("dmDisplayFixedOutput", wt.DWORD),
        ("dmColor", wt.SHORT),
        ("dmDuplex", wt.SHORT),
        ("dmYResolution", wt.SHORT),
        ("dmTTOption", wt.SHORT),
        ("dmCollate", wt.SHORT),
        ("dmFormName", wt.WCHAR * 32),
        ("dmLogPixels", wt.WORD),
        ("dmBitsPerPel", wt.DWORD),
        ("dmPelsWidth", wt.DWORD),
        ("dmPelsHeight", wt.DWORD),
        ("dmDisplayFlags", wt.DWORD),
        ("dmDisplayFrequency", wt.DWORD),
        ("dmICMMethod", wt.DWORD),
        ("dmICMIntent", wt.DWORD),
        ("dmMediaType", wt.DWORD),
        ("dmDitherType", wt.DWORD),
        ("dmReserved1", wt.DWORD),
        ("dmReserved2", wt.DWORD),
        ("dmPanningWidth", wt.DWORD),
        ("dmPanningHeight", wt.DWORD),
    ]


_user32.EnumDisplaySettingsW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(DEVMODEW)]
_user32.EnumDisplaySettingsW.restype = wt.BOOL
_user32.ChangeDisplaySettingsExW.argtypes = [wt.LPCWSTR, ctypes.POINTER(DEVMODEW),
                                             wt.HWND, wt.DWORD, ctypes.c_void_p]
_user32.ChangeDisplaySettingsExW.restype = wt.LONG


def current(device: str | None = None) -> DEVMODEW | None:
    """Текущий режим монитора. None — Windows не отдала режим."""
    devmode = DEVMODEW()
    devmode.dmSize = ctypes.sizeof(DEVMODEW)
    if not _user32.EnumDisplaySettingsW(device, ENUM_CURRENT_SETTINGS, ctypes.byref(devmode)):
        return None
    return devmode


def refresh_rate(device: str | None = None) -> int | None:
    devmode = current(device)
    return int(devmode.dmDisplayFrequency) if devmode is not None else None


def supported_rates(device: str | None = None) -> list[int]:
    """Частоты для текущего разрешения и битности — чтобы не предлагать чужое."""
    base = current(device)
    if base is None:
        return []
    rates: set[int] = set()
    devmode = DEVMODEW()
    devmode.dmSize = ctypes.sizeof(DEVMODEW)
    index = 0
    while _user32.EnumDisplaySettingsW(device, index, ctypes.byref(devmode)):
        if (devmode.dmPelsWidth == base.dmPelsWidth
                and devmode.dmPelsHeight == base.dmPelsHeight
                and devmode.dmBitsPerPel == base.dmBitsPerPel):
            rates.add(int(devmode.dmDisplayFrequency))
        index += 1
        if index > 1024:      # защита от драйвера, который перечисляет бесконечно
            break
    return sorted(rates)


def set_refresh(device: str | None, hertz: int, test_only: bool = False) -> tuple[bool, str]:
    """Сменить только частоту. Остальные поля берутся из текущего режима."""
    base = current(device)
    if base is None:
        return False, "текущий режим не читается"

    hertz = int(hertz)
    if hertz <= 0:
        return False, "частота должна быть положительной"

    devmode = DEVMODEW()
    ctypes.pointer(devmode)[0] = base      # полная копия, поле в поле
    devmode.dmDisplayFrequency = hertz
    devmode.dmFields = DM_DISPLAYFREQUENCY
    devmode.dmSize = ctypes.sizeof(DEVMODEW)

    flags = CDS_TEST if test_only else CDS_UPDATEREGISTRY
    code = _user32.ChangeDisplaySettingsExW(device, ctypes.byref(devmode), None, flags, None)
    if code == DISP_CHANGE_SUCCESSFUL:
        return True, CODES[code]
    return False, CODES.get(int(code), f"код {code}")


def current_mode_summary(device: str | None = None) -> str:
    devmode = current(device)
    if devmode is None:
        return "режим не читается"
    return (f"{devmode.dmPelsWidth}×{devmode.dmPelsHeight} "
            f"@{devmode.dmDisplayFrequency} Гц, {devmode.dmBitsPerPel} бит")
