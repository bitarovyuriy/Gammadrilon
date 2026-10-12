# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ смоук: живое окно и полный quit_app (сброс отложенной записи).

Файл настроек не трогается: save_config перехвачен счётчиком.
Запуск:  .venv\\Scripts\\python.exe _tmp_quit_smoke.py
"""
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

from PyQt5.QtWidgets import QApplication  # noqa: E402

import main as gd_main  # noqa: E402

SAVES: list = []
gd_main.save_config = lambda config: SAVES.append(
    dict(config.get("settings", {}).get("current", {}))) or True

app = QApplication([])
win = gd_main.MainWindow()

t0 = time.monotonic()
while (time.monotonic() - t0) < 1.2:
    app.processEvents()
    time.sleep(0.02)

FAILS = []
print("окно поднялось, сторож идёт:", win.gamma_watch_timer.isActive())
if not win.gamma_watch_timer.isActive():
    FAILS.append("сторож не запущен")

# правка пользователя: значение меняется, запись откладывается
win.brightness_slider.setValue(95)
app.processEvents()
print("после правки: таймер идёт =", win._config_save_timer.isActive(),
      ", pending =", win._config_pending, ", записей =", len(SAVES))
if not (win._config_save_timer.isActive() and win._config_pending and not SAVES):
    FAILS.append("запись не отложена")

win.quit_app()
print("после quit_app: pending =", win._config_pending,
      ", таймер идёт =", win._config_save_timer.isActive(),
      ", записей =", len(SAVES))
if win._config_pending or win._config_save_timer.isActive() or len(SAVES) != 1:
    FAILS.append("quit_app не сбросил отложенное")

print()
print("ИТОГ:", "смоук пройден" if not FAILS else f"провалов {len(FAILS)}: {FAILS}")
sys.exit(1 if FAILS else 0)
