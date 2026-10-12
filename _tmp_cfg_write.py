# -*- coding: utf-8 -*-
"""Кто пишет presets.json при простом переключении вкладок (без действий пользователя).

Ничего не меняет на диске: save_config подменяется на лог с трассировкой.
"""
from __future__ import annotations

import os
import sys
import time
import traceback

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

import gd_i18n as i18n  # noqa: E402
import main as gd_main  # noqa: E402

writes: list[str] = []


def _log_save(data):
    lang = (data.get("settings") or {}).get("language")
    stack = [f"    {line.strip()}" for line in traceback.format_stack()[-4:-1]]
    writes.append(f"save_config: language={lang!r}\n" + "\n".join(stack))


gd_main.save_config = _log_save

app = QApplication([])
_real_load = gd_main.load_config


def _en_config():
    cfg = _real_load()
    cfg.setdefault("settings", {})["language"] = "en"
    return cfg


gd_main.load_config = _en_config
i18n.set_language("en")

win = gd_main.MainWindow()
win.resize(win.compact_size)
win.show()


def pump(ms):
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.02)


pump(800)
for key, index in sorted(win._key_to_index.items(), key=lambda kv: kv[1]):
    win.tabs.setCurrentIndex(index)
    pump(600)

print(f"записей конфига: {len(writes)}")
for w in writes:
    print(w)
print("RESULT:", "OK" if not writes else f"ПИШЕТ {len(writes)}")
