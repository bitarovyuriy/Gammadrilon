# -*- coding: utf-8 -*-
"""Честность «Утилит»: таймер и bufferbloat не выдумывают значения.

Проба не ходит в сеть: подменяет tcp_latency/_download, API таймера, тосты и журнал.
Закрывает то, что раньше было неправдой:
  * bufferbloat считал idle-базу как ``median([0])`` → при полном отсутствии замеров
    «под нагрузкой» получался «класс C» из воздуха;
  * обрыв загрузки (нагрузки не было вовсе) проглатывался → возможен «класс A»;
  * тост таймера безусловно обещал «0,5 мс», даже если система дала 1 мс или отказала;
  * отказ очистки/сжатия ОЗУ попадал в журнал без причины (сжатие — не попадало вовсе).

Запуск: .venv\\\\Scripts\\\\python.exe _tmp_extra_guard.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import gd_extra  # noqa: E402
import gd_extra_ui  # noqa: E402
import gd_journal  # noqa: E402
import gd_toast  # noqa: E402
import gd_utils_ui  # noqa: E402
import gd_i18n as i18n  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(("ок      " if ok else "ПРОВАЛ  ") + name + (f" — {extra}" if extra else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


# -- единицы таймера ---------------------------------------------------------
check("100 нс → мс: 5000 = 0,5 мс", gd_extra.ms_from_100ns(5000) == 0.5)
check("100 нс → мс: 10000 = 1,0 мс", gd_extra.ms_from_100ns(10000) == 1.0)
check("100 нс → мс: 0 и None честно дают None, а не «0 мс»",
      gd_extra.ms_from_100ns(0) is None and gd_extra.ms_from_100ns(None) is None)


# -- bufferbloat: база и нагрузка -------------------------------------------
def fake_latency(values):
    seq = list(values)
    state = {"v": seq[-1] if seq else None}

    def f(_host, _port, _timeout=2.0):
        if seq:
            state["v"] = seq.pop(0)
        return state["v"]

    return f


def fake_download(got: int, err: str):
    return lambda _url, _size, _stop: (got, err)


real_latency, real_download = gd_extra.tcp_latency, gd_extra._download
try:
    gd_extra.tcp_latency = fake_latency([None, None, None, None, None, None, None])
    gd_extra._download = fake_download(0, "URLError: нет сети")
    bb = gd_extra.bufferbloat(timeout=0.01, samples=2)
    check("bufferbloat: связь пропала — никаких выдуманных нулей",
          bb["idle"] is None and bb["loaded"] is None and bb["increase"] is None
          and bb["grade"] == "—", repr(bb))

    gd_extra.tcp_latency = fake_latency([None] * 5 + [30.0, 30.0, 30.0])
    gd_extra._download = fake_download(5_000_000, "")
    bb = gd_extra.bufferbloat(timeout=0.01, samples=3)
    check("bufferbloat: база без нагрузки не измерена — класс не выдуман "
          "(раньше был «C» из нулевой базы)",
          bb["idle"] is None and bb["loaded"] == 30.0 and bb["increase"] is None
          and bb["grade"] == "—", repr(bb))

    gd_extra.tcp_latency = fake_latency([10.0] * 5 + [10.0, 10.0, 10.0])
    gd_extra._download = fake_download(0, "TimeoutError: загрузка не пошла")
    bb = gd_extra.bufferbloat(timeout=0.01, samples=3)
    check("bufferbloat: нагрузку создать не удалось — не «A», а «класс не посчитан»",
          bb["increase"] is None and bb["grade"] == "—" and bb["load_bytes"] == 0
          and "TimeoutError" in bb["load_error"], repr(bb))

    gd_extra.tcp_latency = fake_latency([10.0] * 5 + [60.0] * 5)
    gd_extra._download = fake_download(5_000_000, "")
    bb = gd_extra.bufferbloat(timeout=0.01, samples=5)
    check("bufferbloat: база и нагрузка измерены — класс считается как раньше",
          bb["idle"] == 10.0 and bb["loaded"] == 60.0 and bb["increase"] == 50.0
          and bb["grade"] == "B", repr(bb))
finally:
    gd_extra.tcp_latency, gd_extra._download = real_latency, real_download


# -- UI: таймер и сетевой чек -----------------------------------------------
from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])   # живёт до конца пробы
real_toast, real_record = gd_toast.show_toast, gd_journal.record
real_set, real_ms = gd_extra.set_timer_resolution, gd_extra.timer_ms

panel = gd_extra_ui.ExtraPanel(host=None)
toasts: list = []
records: list = []
gd_toast.show_toast = lambda text, kind="info": toasts.append((text, kind))
gd_journal.record = lambda *a, **kw: records.append((a, kw))
try:
    # 1. система дала 1 мс вместо 0,5
    gd_extra.set_timer_resolution = lambda _on=True: (True, 10000)
    gd_extra.timer_ms = lambda: 1.0
    toasts.clear()
    records.clear()
    panel._timer_toggle(True)
    check("таймер: 1 мс вместо 0,5 — статус называет фактическое значение",
          "вместо 0,5" in panel.lbl_net.text() and "1.0" in panel.lbl_net.text(),
          panel.lbl_net.text())
    check("таймер: вместо «0,5 мс» тост говорит фактическое значение с warn",
          bool(toasts) and toasts[-1][1] == "warn" and "1.0" in toasts[-1][0]
          and "0,5" in toasts[-1][0], repr(toasts[-1:]))
    check("таймер: в журнал попал факт (1.0), а не обещанные 0,5",
          bool(records) and records[-1][0][3] == 1.0, repr(records[-1:]))

    # 2. система отказала
    gd_extra.set_timer_resolution = lambda _on=True: (False, 0)
    gd_extra.timer_ms = lambda: 0.5
    toasts.clear()
    records.clear()
    panel._timer_toggle(True)
    check("таймер: отказ системы — статус объясняет отказ",
          "система отказала" in panel.lbl_net.text(), panel.lbl_net.text())
    check("таймер: отказ — тост warn без обещания «0,5 мс»",
          bool(toasts) and toasts[-1][1] == "warn" and "0,5" not in toasts[-1][0],
          repr(toasts[-1:]))
    check("таймер: отказ записан в журнал с причиной",
          bool(records) and "отказала" in records[-1][1].get("why", "")
          and records[-1][1].get("reversible") is False, repr(records[-1:]))

    # 3. запрос принят, но фактическое значение не читается
    gd_extra.set_timer_resolution = lambda _on=True: (True, 0)
    gd_extra.timer_ms = lambda: None
    toasts.clear()
    records.clear()
    panel._timer_toggle(True)
    check("таймер: значение не подтвердилось — успех не выдуман",
          "не подтвердилось" in panel.lbl_net.text() and toasts[-1][1] == "warn",
          panel.lbl_net.text())

    # 4. честные 0,5 мс
    gd_extra.set_timer_resolution = lambda _on=True: (True, 5000)
    gd_extra.timer_ms = lambda: 0.5
    toasts.clear()
    records.clear()
    panel._timer_toggle(True)
    check("таймер: 0,5 мс действительно дали — статус и тост это подтверждают",
          "0,5 мс" in panel.lbl_net.text() and toasts[-1][1] == "ok"
          and "0.5" in toasts[-1][0], panel.lbl_net.text())

    # 5. сетевой чек: три исхода bufferbloat
    panel._bb_done({"bufferbloat": {"idle": None, "loaded": 30.0, "increase": None,
                                    "grade": "—", "load_bytes": 5_000_000,
                                    "load_error": ""}})
    check("сеть: базу измерить не удалось — текст называет причину, а не класс",
          "базу без нагрузки" in panel.lbl_net.text()
          and "класс не посчитан" in panel.lbl_net.text(), panel.lbl_net.text())
    panel._bb_done({"bufferbloat": {"idle": 10.0, "loaded": 10.0, "increase": None,
                                    "grade": "—", "load_bytes": 12_000,
                                    "load_error": "URLError: нет сети"}})
    check("сеть: нагрузка не создалась — показано сколько скачано и почему",
          "12 КБ" in panel.lbl_net.text() and "URLError" in panel.lbl_net.text(),
          panel.lbl_net.text())
    panel._bb_done({"bufferbloat": {"idle": 10.0, "loaded": 14.0, "increase": 4.0,
                                    "grade": "A", "load_bytes": 5_000_000,
                                    "load_error": ""}})
    check("сеть: честный замер по-прежнему показывает класс",
          "класс A" in panel.lbl_net.text(), panel.lbl_net.text())

    # 6. отказы ОЗУ попадают в журнал с причиной
    utils = gd_utils_ui.UtilsWidget()
    records.clear()
    utils._purge_done({"ok": False, "error": "нужны права администратора"}, {}, auto=False)
    check("ОЗУ: отказ очистки записан в журнал вместе с причиной",
          bool(records) and "нужны права администратора" in records[-1][1].get("why", ""),
          repr(records[-1:]))
    records.clear()
    utils._empty_done({"ok": False, "error": "нет процесса"}, {}, want=2)
    check("ОЗУ: отказ сжатия тоже попадает в журнал — раньше не попадал вовсе",
          bool(records) and "нет процесса" in records[-1][1].get("why", "")
          and records[-1][0][1] == i18n.t("процессов: {v0}", v0=2), repr(records[-1:]))
finally:
    gd_toast.show_toast, gd_journal.record = real_toast, real_record
    gd_extra.set_timer_resolution, gd_extra.timer_ms = real_set, real_ms

print()
print("ИТОГ:", "все проверки пройдены" if not FAILS else f"провалов {len(FAILS)}: {FAILS}")
sys.exit(1 if FAILS else 0)

