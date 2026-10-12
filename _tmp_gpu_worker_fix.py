# -*- coding: utf-8 -*-
"""ВРЕМЕННАЯ проверка: guard'ы фоновых воркеров не падают на удалённом QThread.

Воспроизводит ошибку из лога приложения
(gd_gpu_ui.py: _on_apply → _apply → RuntimeError: wrapped C/C++ object of
type _Worker has been deleted) и подтверждает, что после правки повторное
«Применить» на вкладке GPU работает.

Запуск:  .venv\\Scripts\\python.exe _tmp_gpu_worker_fix.py

Ничего не применяет: клиент хелпера подменён заглушкой, UAC не поднимается.
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

from PyQt5.QtCore import QCoreApplication, QEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import gd_bg  # noqa: E402
import gd_metrics  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    line = ("ок      " if ok else "ПРОВАЛ  ") + name + (f" — {extra}" if extra else "")
    print(line, flush=True)
    if not ok:
        FAILS.append(name)


def pump(ms: int) -> None:
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        time.sleep(0.02)


# --- 1. gd_bg.is_running: None / живой / удалённый воркер -------------------
check("is_running(None) == False", gd_bg.is_running(None) is False)

w = gd_bg.submit(lambda: time.sleep(0.6), lambda r: None)
pump(100)
check("is_running(живой воркер) == True", gd_bg.is_running(w) is True)

pump(1200)      # поток завершился, deleteLater доставлен событийной петлёй
deleted = False
try:
    w.isRunning()
except RuntimeError:
    deleted = True
check("после finished C++-объект удалён (deleteLater)", deleted)
check("is_running(удалённый воркер) == False (а голый isRunning() падал)",
      gd_bg.is_running(w) is False)


# --- 2. Вкладка GPU: повторное «Применить» не падает ------------------------
from gd_gpu_ui import GpuWidget  # noqa: E402


class _FakeClient:
    """Заглушка хелпера: мгновенный честный отказ — без UAC и nvidia-smi."""

    def call(self, obj, timeout=15.0):
        return {"ok": False, "error": "тест: заглушка хелпера"}

    def shutdown_async(self, restore=True):
        pass


gpu = GpuWidget(standalone=False)
gpu.client = _FakeClient()

gpu._on_apply()             # первый клик: воркер уходит в фон
pump(400)                   # воркер завершился, deleteLater доставлен
try:
    gpu._on_apply()         # второй клик: до правки — RuntimeError
    ok, err = True, ""
except RuntimeError as exc:
    ok, err = False, str(exc)
check("повторное «Применить» не бросает RuntimeError", ok, err)
pump(300)
check("статус показал отказ хелпера", "тест" in gpu.bottom_status.text(),
      gpu.bottom_status.text())

# --- итог --------------------------------------------------------------------
gd_metrics.stop()
gd_bg.wait_all(1500)
print()
print("ИТОГ:", "все проверки пройдены" if not FAILS else f"провалов {len(FAILS)}: {FAILS}")
sys.exit(1 if FAILS else 0)
