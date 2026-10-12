# -*- coding: utf-8 -*-
"""ВРЕМЕННАЯ проверка вкладки GPU: подтверждение и «не возвращать лимит при выходе».

Проверяем без UAC и без записи в драйвер (клиент хелпера подменён заглушкой):
  1. галочка настроек читается из config (по умолчанию — включена) и пишется обратно;
  2. переключение галочки уходит хелперу командой keep (только в живую сессию);
  3. подтверждение видно: кнопка «Оставить (N с)» становится главной;
  4. выход приложения: keep=True → лимит не откатывается; неподтверждённое
     изменение откатывается всегда (force);
  5. откат по таймауту попадает в журнал (раньше — молча);
  6. apply_limit берёт диапазон из pl_min/pl_max (старый код читал min_limit —
     проверка диапазона не срабатывала никогда);
  7. слайдер держит цель пользователя: тик метрик не возвращает ползунок на
     значение драйвера, пока изменение не применено («ползунок сам возвращается
     на 300», живой разбор 12.10.2026);
  8. автолимит при запуске: план (settings.gpu_auto_pl) возвращается на карту
     без ожидания подтверждения, повторный вызов молчит, слетевший лимит без
     галочки — только журнал и статус, совпадение с планом — тишина.

Запуск:  .venv\\Scripts\\python.exe _tmp_gpu_ui_keep.py
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

import gd_journal  # noqa: E402
import gd_metrics  # noqa: E402
import gd_toast  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(("ок      " if ok else "ПРОВАЛ  ") + name + (f" — {extra}" if extra else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def pump(ms: int) -> None:
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        time.sleep(0.02)


# --- журнал и тосты: перехватываем, чтобы не сорить в настоящие файлы --------
JOURNAL: list[tuple] = []
TOASTS: list[tuple] = []
gd_journal.record = lambda kind, target, before=None, after=None, why="", source="": \
    JOURNAL.append((kind, target, before, after, why, source))
gd_toast.show_toast = lambda text, kind="ok": TOASTS.append((text, kind))

from gd_gpu_ui import CONFIRM_SECONDS, GpuWidget  # noqa: E402


class _FakeClient:
    """Заглушка хелпера: пишет вызовы, отвечает «ок» без UAC и nvidia-smi."""

    def __init__(self):
        self.calls: list[dict] = []
        self.shutdowns: list[dict] = []

    def call(self, obj, timeout=15.0):
        self.calls.append(dict(obj))
        if obj.get("cmd") == "apply":
            return {"ok": True, "data": {"state": {"pl": obj.get("pl")}}}
        return {"ok": True, "data": {"state": {"pl": 300}}}

    def call_existing(self, obj, timeout=5.0):
        self.calls.append(dict(obj))
        return {"ok": True, "data": {}}

    def shutdown_async(self, restore=True, force=False):
        self.shutdowns.append({"restore": restore, "force": force})


config = {"settings": {}, "presets": []}
saved: list[dict] = []

gpu = GpuWidget(standalone=False, config=config, save_fn=saved.append)
client = _FakeClient()
gpu.client = client
gpu._state = {"pl": 300, "pl_min": 100, "pl_max": 300, "pl_default": 300,
              "name": "RTX", "temp": 40, "clock": 1500, "util": 5,
              "mem_used": 1, "mem_total": 11, "clock_mem": 7000,
              "fan": 30, "driver": "617.14"}

# --- 1. настройка по умолчанию ----------------------------------------------
check("галочка есть и включена по умолчанию", gpu.chk_keep.isChecked())
check("keep_on_exit() == True", gpu.keep_on_exit() is True)

# --- 2. переключение галочки ------------------------------------------------
gpu.chk_keep.setChecked(False)
pump(200)
check("настройка записана в config",
      config["settings"].get("gpu_keep_on_exit") is False,
      str(config["settings"].get("gpu_keep_on_exit")))
check("config сохранён через save_fn", bool(saved))
check("хелперу ушла команда keep(on=False)",
      any(c.get("cmd") == "keep" and c.get("on") is False for c in client.calls),
      str(client.calls[-1] if client.calls else None))
check("статус объясняет новое поведение",
      "вернётся" in gpu.bottom_status.text(), gpu.bottom_status.text())
gpu.chk_keep.setChecked(True)
pump(200)
check("возврат галочки: keep(on=True)",
      any(c.get("cmd") == "keep" and c.get("on") is True for c in client.calls))

# --- 3. подтверждение видно -------------------------------------------------
gpu._on_apply()
pump(400)
check("статус просит подтвердить", "Подтвердите" in gpu.bottom_status.text(),
      gpu.bottom_status.text())
check("кнопка показывает отсчёт",
      gpu.btn_keep.text() == f"Оставить ({CONFIRM_SECONDS} с)", gpu.btn_keep.text())
check("кнопка стала главной", gpu.btn_keep.objectName() == "PrimaryButton",
      gpu.btn_keep.objectName())
check("тост — предупреждение с отсчётом",
      bool(TOASTS) and TOASTS[-1][1] == "warn", str(TOASTS[-1] if TOASTS else None))
check("в журнал ушло применение",
      bool(JOURNAL) and JOURNAL[-1][0] == "gpu_pl", str(JOURNAL[-1] if JOURNAL else None))

# --- 4. выход приложения ----------------------------------------------------
gpu.shutdown_for_exit()          # изменение не подтверждено → обязательный откат
check("неподтверждённое: выход форсирует откат",
      client.shutdowns[-1] == {"restore": True, "force": True}, str(client.shutdowns[-1]))

gpu._on_keep()                   # пользователь нажал «Оставить»
pump(200)
check("кнопка вернулась во второстепенный вид",
      gpu.btn_keep.objectName() == "Segment" and gpu.btn_keep.isHidden(),
      gpu.btn_keep.objectName())
gpu.shutdown_for_exit()          # подтверждено и keep=True → лимит остаётся
check("подтверждённое + keep=True: отката нет",
      client.shutdowns[-1] == {"restore": False, "force": False}, str(client.shutdowns[-1]))

gpu.chk_keep.setChecked(False)
pump(200)
gpu.shutdown_for_exit()          # подтверждено, но keep=False → откат при выходе
check("keep=False: откат при выходе",
      client.shutdowns[-1] == {"restore": True, "force": False}, str(client.shutdowns[-1]))
gpu.chk_keep.setChecked(True)
pump(200)

# --- 5. откат по таймауту попадает в журнал ---------------------------------
JOURNAL.clear()
gpu.sl_pl.setValue(212)          # как будто пользователь двинул слайдер
gpu._on_apply()
pump(400)
for _ in range(CONFIRM_SECONDS):
    gpu._confirm_tick()
pump(600)
restores = [j for j in JOURNAL if j[0] == "restore"]
check("таймаут: откат записан в журнал", bool(restores), str(restores))
check("таймаут: причина названа честно",
      bool(restores) and "подтверждение не получено" in restores[-1][4],
      str(restores[-1][4] if restores else None))
check("таймаут: видно, из чего и во что вернули",
      bool(restores) and restores[-1][2] == 212 and restores[-1][3] == 300,
      str(restores[-1][2:4] if restores else None))
check("таймаут: статус «прежний лимит возвращён»",
      "возвращён" in gpu.bottom_status.text(), gpu.bottom_status.text())

# --- 6. диапазон драйвера берётся из pl_min/pl_max --------------------------
client.calls.clear()
gpu.apply_limit(500, source="тест")
pump(300)
check("лимит вне диапазона отклонён без записи в драйвер",
      not any(c.get("cmd") == "apply" for c in client.calls), str(client.calls))
check("отказ назвал диапазон драйвера",
      "вне диапазона" in gpu.bottom_status.text(), gpu.bottom_status.text())
gpu.apply_limit(212, source="тест")
pump(400)
check("лимит в диапазоне применён",
      any(c.get("cmd") == "apply" and c.get("pl") == 212 for c in client.calls),
      str(client.calls))

# --- 7. слайдер держит цель пользователя до «Применить» ---------------------
# Живой разбор 12.10.2026 («ползунок сам возвращается на 300»): тик метрик раз в
# секунду переписывал слайдер значением драйвера, пока изменение не применено,
# и в журнал уходило применение прежних 300 Вт — вкладка выглядела нерабочей.
gpu._confirm_timer.stop()        # отсчёт из блока 6 больше не нужен
gpu._show_confirm(False)
gpu._pending = None
gpu._pl_dirty = False
gpu._state["pl"] = 300           # карта сейчас на 300 Вт
JOURNAL.clear()
gpu._tick()                      # первый тик: слайдер = состояние карты
check("слайдер показывает состояние карты", gpu.sl_pl.value() == 300, str(gpu.sl_pl.value()))

gpu.sl_pl.setValue(212)          # пользователь сдвинул ползунок (как мышью)
pump(50)
check("сдвиг слайдера отмечен как цель пользователя", gpu._pl_dirty is True)
for _ in range(3):               # три тика подряд — как три секунды на вкладке
    gpu._tick()
    pump(30)
check("тик не возвращает ползунок на 300", gpu.sl_pl.value() == 212, str(gpu.sl_pl.value()))
check("подпись держит значение пользователя", "212" in gpu.lbl_pl_val.text(),
      gpu.lbl_pl_val.text())

client.calls.clear()
gpu._on_apply()
pump(400)
check("в драйвер ушло значение с ползунка, а не 300",
      any(c.get("cmd") == "apply" and c.get("pl") == 212 for c in client.calls),
      str([c for c in client.calls if c.get("cmd") == "apply"]))
check("в журнал ушло применение 212 Вт",
      bool(JOURNAL) and JOURNAL[-1][0] == "gpu_pl" and "212" in JOURNAL[-1][1],
      str(JOURNAL[-1] if JOURNAL else None))
check("после применения цель считается достигнутой", gpu._pl_dirty is False)

gpu.sl_pl.setValue(240)          # сдвиг во время ожидания подтверждения
pump(50)
check("сдвиг после применения не отменяет ожидание (ТЗ 4.3)",
      gpu._pending == {"pl": 212}, str(gpu._pending))
gpu._confirm_tick()
check("отсчёт называет применённое значение, а не «None Вт»",
      "212" in gpu.bottom_status.text(), gpu.bottom_status.text())
gpu._confirm_timer.stop()
gpu._show_confirm(False)
gpu._pending = None
gpu._pl_dirty = False

# --- 8. автолимит при запуске (settings.gpu_auto_pl) ------------------------
# План — последнее подтверждённое значение. Галочка «применять при запуске»
# решает: вернуть план на карту (silent — без ожидания подтверждения) или
# только сказать, что лимит слетел.
import gd_gpu  # noqa: E402

_real_read_state = gd_gpu.read_state
_FAKE_STATE = {"pl": 300, "pl_min": 100, "pl_max": 300, "pl_default": 300,
               "name": "RTX", "temp": 40, "clock": 1500, "util": 5,
               "mem_used": 1, "mem_total": 11, "clock_mem": 7000,
               "fan": 30, "driver": "617.14"}
gd_gpu.read_state = lambda: dict(_FAKE_STATE)

config2 = {"settings": {"gpu_auto_pl": 212, "gpu_apply_on_start": True}, "presets": []}
saved2: list[dict] = []
gpu2 = GpuWidget(standalone=False, config=config2, save_fn=saved2.append)
client2 = _FakeClient()
gpu2.client = client2
gpu2._state = dict(_FAKE_STATE)

JOURNAL.clear()
TOASTS.clear()
gpu2.ensure_autostart_pl()
pump(600)
check("автозапуск: план 212 вернулся на карту",
      any(c.get("cmd") == "apply" and c.get("pl") == 212 for c in client2.calls),
      str(client2.calls))
check("автозапуск: подтверждение не запрашивается",
      not gpu2._confirm_timer.isActive() and gpu2._pending is None
      and gpu2.btn_keep.isHidden())
check("автозапуск: статус объясняет возврат",
      "возвращён при запуске" in gpu2.bottom_status.text(), gpu2.bottom_status.text())
check("автозапуск: в журнале причина «запуск приложения»",
      bool(JOURNAL) and JOURNAL[-1][0] == "gpu_pl" and "запуск" in JOURNAL[-1][4],
      str(JOURNAL[-1] if JOURNAL else None))
check("автозапуск: план сохранён (212)", config2["settings"]["gpu_auto_pl"] == 212,
      str(config2["settings"]["gpu_auto_pl"]))

calls_before = len(client2.calls)
gpu2.ensure_autostart_pl()
pump(200)
check("автозапуск: повторный вызов ничего не делает",
      len(client2.calls) == calls_before, str(client2.calls))

# галочка выключена: драйвер не трогаем, но честно говорим о слетевшем лимите
config3 = {"settings": {"gpu_auto_pl": 212, "gpu_apply_on_start": False}, "presets": []}
gpu3 = GpuWidget(standalone=False, config=config3, save_fn=lambda _c: None)
client3 = _FakeClient()
gpu3.client = client3
gpu3._state = dict(_FAKE_STATE)
JOURNAL.clear()
TOASTS.clear()
gpu3.ensure_autostart_pl()
pump(600)
check("слетевший лимит без галочки: драйвер не тронут",
      not any(c.get("cmd") == "apply" for c in client3.calls), str(client3.calls))
check("слетевший лимит без галочки: сказано в статусе",
      "слетел" in gpu3.bottom_status.text(), gpu3.bottom_status.text())
check("слетевший лимит без галочки: тост-предупреждение",
      bool(TOASTS) and TOASTS[-1][1] == "warn", str(TOASTS[-1] if TOASTS else None))
check("слетевший лимит без галочки: журнал с причиной",
      bool(JOURNAL) and "перезапуск" in JOURNAL[-1][4],
      str(JOURNAL[-1] if JOURNAL else None))

# факт совпал с планом — тишина
config4 = {"settings": {"gpu_auto_pl": 300, "gpu_apply_on_start": True}, "presets": []}
gpu4 = GpuWidget(standalone=False, config=config4, save_fn=lambda _c: None)
client4 = _FakeClient()
gpu4.client = client4
gpu4._state = dict(_FAKE_STATE)
JOURNAL.clear()
TOASTS.clear()
gpu4.ensure_autostart_pl()
pump(500)
check("план совпал: драйвер не тронут и тишина",
      not client4.calls and not TOASTS, f"{client4.calls} / {TOASTS}")

# выход с «возвращать при выходе»: план сбрасывается
gpu2.chk_keep.setChecked(False)
pump(200)
gpu2.shutdown_for_exit()
check("выход с откатом: план сброшен", config2["settings"]["gpu_auto_pl"] is None,
      str(config2["settings"]["gpu_auto_pl"]))

gd_gpu.read_state = _real_read_state

# --- итог -------------------------------------------------------------------
gd_metrics.stop()
import gd_bg  # noqa: E402

gd_bg.wait_all(1500)
print()
print("ИТОГ:", "все проверки пройдены" if not FAILS else f"провалов {len(FAILS)}: {FAILS}")
sys.exit(1 if FAILS else 0)
