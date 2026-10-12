"""Живой снимок окна Gammadrilon: PrintWindow -> PNG (без внешних зависимостей).

Нужен, чтобы отличить реальный рендер (windowsvista, системные шрифты) от offscreen.
Пишет build/shots/live-window.png и печатает геометрию окна.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import sys
from pathlib import Path

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

APP_NAME = "Gammadrilon"
PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", w.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", w.WORD),
        ("biBitCount", w.WORD),
        ("biCompression", w.DWORD),
        ("biSizeImage", w.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", w.DWORD),
        ("biClrImportant", w.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", w.DWORD * 3)]


def find_windows(title: str) -> list[int]:
    found: list[int] = []
    proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value == title:
            found.append(int(hwnd))
        return True

    user32.EnumWindows(proc(cb), 0)
    return found


def capture(hwnd: int, path: Path) -> tuple[int, int]:
    rect = w.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
    gdi32.SelectObject(hdc_mem, hbmp)
    user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)

    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height  # top-down
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = BI_RGB

    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf, ctypes.byref(info), DIB_RGB_COLORS)

    from PyQt5.QtGui import QImage

    img = QImage(bytes(buf.raw), width, height, width * 4, QImage.Format_ARGB32)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = img.save(str(path))

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    nonblack = 0
    for y in range(0, height, 7):
        for x in range(0, width, 7):
            if img.pixel(x, y) & 0x00FFFFFF:
                nonblack += 1

    print(f"saved={ok} size={width}x{height} rect=({rect.left},{rect.top}) sampled_nonblack={nonblack}")
    return width, height


def main() -> int:
    wins = find_windows(APP_NAME)
    if not wins:
        print(f"window '{APP_NAME}' not found")
        return 1
    name = sys.argv[1] if len(sys.argv) > 1 else "live-window.png"
    out = Path(__file__).resolve().parent / "build" / "shots" / name
    for hwnd in wins:
        print(f"hwnd={hwnd}")
        capture(hwnd, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
