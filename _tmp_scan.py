# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ: поиск светлых (>=200) областей на снимке — где некрашеный фон таблиц."""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import sysconfig as _sc

    _site = _sc.get_paths().get("purelib", "")
    _qt = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt)
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", os.path.join(_qt, "platforms"))
except Exception:  # noqa: BLE001
    pass
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from PyQt5.QtGui import QImage  # noqa: E402

SHOTS = os.path.join(HERE, "build", "shots")
FILES = ["dark-utils-full.png", "dark-dns-window.png", "dark-rules-window.png"]

for fn in FILES:
    img = QImage(os.path.join(SHOTS, fn))
    img = img.convertToFormat(QImage.Format_RGB32)
    data = img.constBits().asstring(img.byteCount())
    w, h = img.width(), img.height()
    print(f"\n=== {fn} ({w}x{h}) — светлые пиксели (все каналы >= 200) ===")
    runs = 0
    for y in range(h):
        row = data[y * w * 4:(y + 1) * w * 4]
        xs = [x for x in range(w)
              if row[x * 4] >= 200 and row[x * 4 + 1] >= 200 and row[x * 4 + 2] >= 200]
        if len(xs) < 12:  # одиночные светлые пиксели — антиалиасинг текста
            continue
        runs += 1
        if runs <= 45 or runs % 10 == 0:
            print(f"  y={y:4d}: {len(xs):4d} px, x от {min(xs)} до {max(xs)}")
    print(f"  строк со светлыми полосами: {runs}")
