# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ: точная геометрия QSpinBox в утилитах + кропы по этим координатам."""
import os
import sys
import time

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
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.makedirs(os.path.join(HERE, "build", "shots"), exist_ok=True)
LOG = open(os.path.join(HERE, "build", "shots", "spin.txt"), "w", encoding="utf-8")


def log(msg):
    LOG.write(str(msg) + "\n")
    LOG.flush()


from PyQt5.QtCore import QPoint, Qt  # noqa: E402
from PyQt5.QtGui import QFontDatabase, QImage  # noqa: E402
from PyQt5.QtWidgets import QApplication, QSpinBox  # noqa: E402

app = QApplication([])
_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
for _f in ("segoeui.ttf", "segoeuib.ttf", "bahnschrift.ttf", "arial.ttf", "consola.ttf"):
    if os.path.isfile(os.path.join(_fonts, _f)):
        QFontDatabase.addApplicationFont(os.path.join(_fonts, _f))

import gd_theme as th  # noqa: E402
import main as gd_main  # noqa: E402

for theme in ("dark", "light"):
    th.apply(theme)
    gd_main.APP_STYLESHEET = gd_main.build_app_stylesheet()
    app.setStyleSheet(gd_main.APP_STYLESHEET)
    win = gd_main.MainWindow()
    win.resize(win.compact_size)
    win.show()
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < 1200:
        app.processEvents()
        time.sleep(0.02)
    win.tabs.setCurrentIndex(win._key_to_index["tab.utils"])
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < 1500:
        app.processEvents()
        time.sleep(0.02)
    page = win._utils_page
    pm = page.grab()
    out = os.path.join(HERE, "build", "shots", f"_probe-utils-{theme}.png")
    pm.save(out)
    for spin in page.findChildren(QSpinBox):
        pos = spin.mapTo(page, QPoint(0, 0))
        log(f"{theme}: QSpinBox '{spin.text()}' в ({pos.x()},{pos.y()}) "
            f"{spin.width()}x{spin.height()} hint={spin.sizeHint().width()}x"
            f"{spin.sizeHint().height()} style_len={len(spin.styleSheet())}")
        crop = pm.copy(pos.x() - 6, pos.y() - 6, spin.width() + 12, spin.height() + 12)
        crop = crop.scaled(crop.width() * 5, crop.height() * 5, Qt.KeepAspectRatio,
                           Qt.FastTransformation)
        crop.save(os.path.join(HERE, "build", "shots",
                               f"_probe-spin-{theme}-{pos.y()}.png"))
    win.close()
    app.processEvents()

log("готово")
LOG.close()
os._exit(0)
