# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ: увеличенные кропы подозрительных мест на снимках."""
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

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QImage  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
SHOTS = os.path.join(HERE, "build", "shots")

# имя, файл, x, y, w, h, масштаб
CROPS = [
    ("crop9-rightstrip", "dark-cpu-window.png", 878, 300, 32, 90, 6),
    ("crop10-header-white", "dark-utils-full.png", 420, 650, 260, 70, 4),
    ("crop11-dns-rightstrip", "dark-dns-window.png", 845, 225, 65, 70, 6),
    ("crop12-spinbox-vista", "dark-utils-full.png", 130, 372, 120, 45, 6),
    ("crop13-list-indicator", "dark-dialog-processpicker.png", 0, 40, 120, 70, 5),
]

for name, fn, x, y, w, h, scale in CROPS:
    img = QImage(os.path.join(SHOTS, fn))
    if img.isNull():
        print("!! нет файла", fn)
        continue
    part = img.copy(x, y, w, h)
    part = part.scaled(w * scale, h * scale, Qt.KeepAspectRatio,
                       Qt.FastTransformation)
    out = os.path.join(SHOTS, name + ".png")
    part.save(out)
    print(f"{name}: из {fn} ({x},{y},{w}x{h}) x{scale} -> {part.width()}x{part.height()}")
