"""Software brightness, based on the GDI ramp backend from 3.6.6."""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
from ctypes import wintypes as w

MINIMUM = 50
MAXIMUM = 150
_Ramp = ctypes.c_ushort * 768


def scale_ramp(original: tuple[int, ...], percent: int) -> tuple[int, ...]:
    if not MINIMUM <= percent <= MAXIMUM:
        raise ValueError('Яркость должна быть от 50 до 150 %.')
    return tuple(min(65535, value * percent // 100) for value in original)


class GdiRamp:
    def __init__(self):
        self.user = ctypes.WinDLL('user32')
        self.gdi = ctypes.WinDLL('gdi32')
        self.user.GetDC.argtypes = [w.HWND]
        self.user.GetDC.restype = w.HDC
        self.user.ReleaseDC.argtypes = [w.HWND, w.HDC]
        self.user.ReleaseDC.restype = ctypes.c_int
        for function in (self.gdi.GetDeviceGammaRamp, self.gdi.SetDeviceGammaRamp):
            function.argtypes = [w.HDC, ctypes.c_void_p]
            function.restype = w.BOOL

    @contextmanager
    def dc(self):
        dc = self.user.GetDC(None)
        if not dc:
            raise OSError('Не удалось открыть основной экран.')
        try:
            yield dc
        finally:
            self.user.ReleaseDC(None, dc)

    def read(self) -> tuple[int, ...]:
        ramp = _Ramp()
        with self.dc() as dc:
            if not self.gdi.GetDeviceGammaRamp(dc, ramp):
                raise OSError('Драйвер не отдаёт таблицу яркости.')
        return tuple(ramp)

    def write(self, values: tuple[int, ...]) -> tuple[int, ...]:
        ramp, actual = _Ramp(*values), _Ramp()
        with self.dc() as dc:
            ok = self.gdi.SetDeviceGammaRamp(dc, ramp) and self.gdi.GetDeviceGammaRamp(dc, actual)
        # Some drivers quantize the ramp or claim success without changing it.
        if not ok or max(abs(a - b) for a, b in zip(ramp, actual)) > 257:
            raise OSError('Драйвер не подтвердил яркость. Проверь, выключен ли HDR.')
        return tuple(actual)


class Brightness:
    def __init__(self, api=None):
        self.api = api
        self.original = None
        self.applied = None

    def apply(self, percent: int) -> None:
        if self.api is None:
            self.api = GdiRamp()
        if self.original is None:
            if percent == 100:
                return
            self.original = self.api.read()
        target = scale_ramp(self.original, percent)
        self.applied = self.api.write(target)
        if percent == 100:
            self.applied = self.original = None

    def restore(self) -> None:
        if (self.original is not None and self.api is not None and self.applied is not None
                and self.api.read() == self.applied):
            self.api.write(self.original)
        self.applied = self.original = None
