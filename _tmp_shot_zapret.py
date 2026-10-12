# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ: снимки вкладки «Обход DPI» (offscreen, тёмная и светлая тема).

Запуск:  .venv\\Scripts\\python.exe _tmp_shot_zapret.py
Путь к zapret подставляется только в память — presets.json не меняется.
"""
from __future__ import annotations

import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                              os.path.join(_qt_plugins, "platforms"))
except Exception:  # noqa: BLE001
    pass

if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QFontDatabase  # noqa: E402
from PyQt5.QtWidgets import QApplication, QStyleFactory  # noqa: E402

import gd_theme as th  # noqa: E402
import main as gd_main  # noqa: E402

OUT = os.path.join(HERE, "build", "shots")
os.makedirs(OUT, exist_ok=True)

app = QApplication([])
if "windowsvista" in QStyleFactory.keys():
    app.setStyle("windowsvista")
_fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
for _fname in ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf", "bahnschrift.ttf"):
    _fpath = os.path.join(_fonts_dir, _fname)
    if os.path.isfile(_fpath):
        QFontDatabase.addApplicationFont(_fpath)
app.setStyleSheet(gd_main.build_app_stylesheet())


def pump(ms: int) -> None:
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.02)


def set_theme(name: str) -> None:
    th.apply(name)
    gd_main.APP_STYLESHEET = gd_main.build_app_stylesheet()
    app.setStyleSheet(gd_main.APP_STYLESHEET)
    win.setStyleSheet(gd_main.APP_STYLESHEET)
    page = win._zapret_page
    page.setStyleSheet(th.STYLESHEET)
    win.update()


win = gd_main.MainWindow()
win.tray.hide()
win.resize(1100, 760)
# путь — только в память: снимок с настоящими стратегиями, конфиг не трогаем
win.config.setdefault("settings", {})["zapret_path"] = r"D:\za\zapret-discord-youtube-1.10.2"

for theme in ("dark", "light"):
    set_theme(theme)
    win.tabs.setCurrentIndex(win._key_to_index["tab.zapret"])
    pump(1600)
    page = win._zapret_page
    # выбрать первую реальную стратегию для наглядности
    from PyQt5.QtCore import Qt as _Qt

    for i in range(page.list.count()):
        it = page.list.item(i)
        if it.data(_Qt.UserRole):
            page.list.setCurrentItem(it)
            break
    pump(400)
    path = os.path.join(OUT, f"{theme}-zapret-window.png")
    win.grab().save(path)
    print("shot:", path, flush=True)

try:
    import gd_metrics

    gd_metrics.stop()
    import gd_bg

    gd_bg.wait_all(1200)
except Exception:  # noqa: BLE001
    pass
print("DONE", flush=True)
