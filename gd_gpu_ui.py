# -*- coding: utf-8 -*-
"""Gammadrilon v3 — вкладка «Питание GPU» (NVIDIA).

Чтение состояния — напрямую через nvidia-smi (без прав администратора).
Изменение лимита мощности — через elevated-хелпер (`GpuClient`), с обязательным
«временем на подтверждение» (ТЗ 4.3): если изменение не подтвердить за
CONFIRM_SECONDS, прежнее значение возвращается автоматически.
"""
from __future__ import annotations

import os
import sys

# --- PyQt5 и не-ASCII путь (как в main.py) ---------------------------------
try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
except Exception:  # noqa: BLE001
    pass

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_bg  # noqa: E402
import gd_gpu  # noqa: E402
import gd_agent  # noqa: E402
import gd_theme as th
import gd_i18n as i18n  # noqa: E402
from gd_power_ui import make_card  # noqa: E402

CONFIRM_SECONDS = 12        # время на подтверждение изменения (ТЗ 4.3: 10–15 с)
PROFILE_ORDER = ("quiet", "default", "game", "max")


def _delta_watts(cur, old):
    if cur is None or old is None:
        return ""
    d = cur - old
    return i18n.t(" (Δ {v0:+.0f} Вт)", v0=d)


class GpuWidget(QWidget):
    def __init__(self, standalone: bool = False):
        super().__init__()
        self.setObjectName("PanelRoot")
        self._standalone = standalone
        if standalone:
            self.setWindowTitle(i18n.t("Gammadrilon — Питание GPU"))
            self.resize(470, 680)
        self.setStyleSheet(th.STYLESHEET)

        self.client = gd_gpu.GpuClient()
        self._state = None
        self._pending = None          # применённое, но не подтверждённое значение
        self._confirm_left = 0
        self._active_profile = None   # имя применённого профиля (для трея/палитры)
        self._apply_worker = None     # фоновое применение лимита (UAC + nvidia-smi)
        self._restore_worker = None   # фоновый откат

        self._build()

        self._sensor_timer = QTimer(self)
        self._sensor_timer.timeout.connect(self._tick)
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setInterval(1000)
        self._confirm_timer.timeout.connect(self._confirm_tick)

        if standalone:
            self.activate()

    # -- активация (ленивая вкладка) ---------------------------------------
    def activate(self):
        self._tick()
        self._sensor_timer.start(1000)

    def deactivate(self):
        self._sensor_timer.stop()
        if self._confirm_timer.isActive():
            self._confirm_timer.stop()

    # -- построение --------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel(i18n.t("Питание GPU"))
        title.setObjectName("Title")
        title.setVisible(self._standalone)
        head.addWidget(title)
        head.addStretch(1)
        self.lbl_gpu = QLabel("—")
        self.lbl_gpu.setObjectName("Hint")
        head.addWidget(self.lbl_gpu)
        root.addLayout(head)

        # Доступность GPU и конфликт с вендорским софтом определяются в фоне:
        # nvidia-smi (timeout 6 с) и psutil.process_iter в GUI-потоке давали паузу.
        import gd_metrics

        self._pending_gpu_probe = gd_metrics.gpu() is None and not gd_metrics.gpu_unavailable()
        self.conflict = None
        self._gpu_warn = QLabel("")
        self._gpu_warn.setObjectName("Hint")
        self._gpu_warn.setWordWrap(True)
        self._gpu_warn.setVisible(False)
        root.addWidget(self._gpu_warn)
        self._conflict_note = QLabel("")
        self._conflict_note.setObjectName("Hint")
        self._conflict_note.setWordWrap(True)
        self._conflict_note.setVisible(False)
        root.addWidget(self._conflict_note)
        self._conflict_worker = gd_bg.submit(self._detect_conflict, self._conflict_done, parent=self)

        # --- профиль ---
        card, lay = make_card(
            i18n.t("ПРОФИЛЬ GPU"),
            i18n.t("Профиль — ярлык над слайдером лимита, а не отдельная настройка: «Тихий» ставит 150 Вт, «Дефолт» — номинал, «Игровой» — 93 % максимума, «Макс» — максимум драйвера."),
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self.profile_group = QButtonGroup(self)
        self.profile_btns = {}
        for pid in PROFILE_ORDER:
            b = QPushButton(gd_gpu.GPU_PROFILES[pid]["label"])
            b.setObjectName("Segment")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, p=pid: self._ui_profile(p))
            self.profile_group.addButton(b)
            self.profile_btns[pid] = b
            row.addWidget(b)
        lay.addLayout(row)
        self.profile_desc = QLabel(i18n.t("Профиль не применён."))
        self.profile_desc.setObjectName("Hint")
        self.profile_desc.setWordWrap(True)
        lay.addWidget(self.profile_desc)
        root.addWidget(card)

        # --- сейчас ---
        card2, lay2 = make_card(
            i18n.t("СЕЙЧАС"),
            i18n.t("Температура, частота ядра, лимит мощности и загрузка — прямо из драйвера (nvidia-smi), без прав администратора."),
        )
        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self.lbl_temp = QLabel("—")
        self.lbl_clk = QLabel("—")
        self.lbl_pl = QLabel("—")
        self.lbl_util = QLabel("—")
        for lbl, cap in ((self.lbl_temp, i18n.t("температура")), (self.lbl_clk, i18n.t("частота")),
                         (self.lbl_pl, i18n.t("лимит")), (self.lbl_util, i18n.t("загрузка"))):
            col = QVBoxLayout()
            col.setSpacing(2)
            lbl.setObjectName("Value")
            c = QLabel(cap)
            c.setObjectName("Hint")
            col.addWidget(lbl)
            col.addWidget(c)
            metrics.addLayout(col)
        metrics.addStretch(1)
        lay2.addLayout(metrics)
        self.lbl_mem = QLabel("—")
        self.lbl_mem.setObjectName("Hint")
        lay2.addWidget(self.lbl_mem)
        root.addWidget(card2)

        # --- лимит мощности ---
        card3, lay3 = make_card(
            i18n.t("ЛИМИТ МОЩНОСТИ"),
            i18n.t("Установка лимита требует прав администратора — при первом применении будет запрос UAC. Изменение нужно подтвердить, иначе прежнее значение вернётся само."),
        )
        self.lbl_range = QLabel("—")
        self.lbl_range.setObjectName("Hint")
        self.lbl_range.setWordWrap(True)
        lay3.addWidget(self.lbl_range)

        srow = QHBoxLayout()
        srow.setSpacing(12)
        self.sl_pl = QSlider(Qt.Horizontal)
        self.sl_pl.setRange(100, 366)
        self.lbl_pl_val = QLabel("—")
        self.lbl_pl_val.setObjectName("Value")
        srow.addWidget(self.sl_pl, 1)
        srow.addWidget(self.lbl_pl_val)
        lay3.addLayout(srow)
        self.sl_pl.valueChanged.connect(self._on_slider)

        brow = QHBoxLayout()
        brow.setSpacing(8)
        self.btn_apply = QPushButton(i18n.t("Применить"))
        self.btn_apply.setObjectName("PrimaryButton")
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_keep = QPushButton(i18n.t("Оставить"))
        self.btn_keep.setObjectName("Segment")
        self.btn_keep.setVisible(False)
        self.btn_keep.clicked.connect(self._on_keep)
        brow.addWidget(self.btn_apply)
        brow.addWidget(self.btn_keep)
        lay3.addLayout(brow)
        root.addWidget(card3)

        self.btn_restore = QPushButton(i18n.t("Вернуть как было"))
        self.btn_restore.setObjectName("Segment")
        self.btn_restore.clicked.connect(self._ui_restore)
        root.addWidget(self.btn_restore)

        root.addStretch(1)
        self.bottom_status = QLabel("")
        self.bottom_status.setObjectName("Hint")
        self.bottom_status.setWordWrap(True)
        root.addWidget(self.bottom_status)

    def _detect_conflict(self) -> str | None:
        """MSI Afterburner / RTSS — известные «перебиватели» (ТЗ 4.5)."""
        try:
            import psutil

            names = {p.info.get("name", "").lower() for p in psutil.process_iter(["name"])}
        except Exception:  # noqa: BLE001
            return None
        if any(n.startswith("msiafterburner") for n in names):
            return "MSI Afterburner"
        return None

    def _conflict_done(self, name) -> None:
        """Показать предупреждение о вендорском софте (проверка шла в фоне)."""
        if not name:
            return
        self.conflict = name
        try:
            self._conflict_note.setText(i18n.t(
                "Обнаружен {v0}: он тоже управляет лимитом мощности и частотами. Ярлыками пользоваться не стоит — меняйте лимит вручную и следите, чтобы значения не «перебивались».",
                v0=name))
            self._conflict_note.setVisible(True)
        except RuntimeError:      # виджет уже удалён
            pass

    def _set_controls_enabled(self, on: bool) -> None:
        """Включить/заблокировать управление лимитом (например, GPU недоступен)."""
        for btn in getattr(self, "profile_btns", {}).values():
            btn.setEnabled(on)
        for attr in ("sl_pl", "btn_apply", "btn_restore"):
            w = getattr(self, attr, None)
            if w is not None:
                w.setEnabled(on)

    # -- обновление --------------------------------------------------------
    def _tick(self):
        import gd_metrics

        st = gd_metrics.gpu() or self._state
        if st is None:
            self.lbl_gpu.setText(i18n.t("GPU не отвечает"))
            if not self._pending_gpu_probe and gd_metrics.gpu_unavailable():
                # nvidia-smi молчит несколько попыток подряд — карты нет
                self._gpu_warn.setText(i18n.t(
                    "NVIDIA GPU или nvidia-smi не найдены. Управление недоступно: для этой карты/системы публичного способа менять лимит мощности нет."))
                self._gpu_warn.setVisible(True)
                self._set_controls_enabled(False)
            return
        if self._pending_gpu_probe:
            self._pending_gpu_probe = False      # первый ответ пришёл — вкладка живая
            self._gpu_warn.setVisible(False)
        self._state = st
        self.lbl_gpu.setText(st["name"])
        self.lbl_temp.setText(f"{st['temp']:.0f} °C" if st["temp"] is not None else "—")
        self.lbl_clk.setText(i18n.t("{v0:.0f} МГц", v0=st['clock']) if st["clock"] is not None else "—")
        self.lbl_pl.setText(i18n.t("{v0:.0f}/{v1:.0f} Вт", v0=st['pl'], v1=st['pl_max']) if st["pl"] is not None else "—")
        self.lbl_util.setText(f"{st['util']:.0f} %" if st["util"] is not None else "—")
        mem = ""
        if st["clock_mem"]:
            mem = i18n.t("память {v0:.0f} МГц", v0=st['clock_mem'])
        if st["mem_used"] and st["mem_total"]:
            mem += ("  ·  " if mem else "") + i18n.t("VRAM {v0:.1f}/{v1:.1f} ГБ", v0=st['mem_used'] / 1024, v1=st['mem_total'] / 1024)
        self.lbl_mem.setText(mem or "—")

        if st["pl_min"] is not None and st["pl_max"] is not None:
            self.sl_pl.setRange(int(st["pl_min"]), int(st["pl_max"]))
        if self._pending is None and st["pl"] is not None:
            self.sl_pl.blockSignals(True)
            self.sl_pl.setValue(int(st["pl"]))
            self.sl_pl.blockSignals(False)
        self.lbl_pl_val.setText(i18n.t("{v0} Вт", v0=self.sl_pl.value()))
        self._update_range_label()

    def _update_range_label(self):
        st = self._state
        if not st:
            return
        cur = st["pl"]
        nom = st["pl_default"]
        lo, hi = st["pl_min"], st["pl_max"]
        txt = (i18n.t("Номинал {v0:.0f} Вт · диапазон {v1:.0f}–{v2:.0f} Вт · сейчас {v3:.0f} Вт", v0=nom, v1=lo, v2=hi, v3=cur))
        if self._pending and self._pending.get("pl") is not None:
            txt += i18n.t(" → {v0:.0f} Вт{v1}", v0=self._pending['pl'], v1=_delta_watts(self._pending['pl'], cur))
        self.lbl_range.setText(txt)

    def _on_slider(self, value):
        self._pending = None
        self.lbl_pl_val.setText(i18n.t("{v0} Вт", v0=value))

    def _tick_later(self, delay_ms: int = 800) -> None:
        """Обновить показания после операции: пусть кэш метрик пере-снимет GPU."""
        import gd_metrics

        gd_metrics.refresh_now("gpu")
        QTimer.singleShot(delay_ms, self._tick)

    # -- действия ----------------------------------------------------------
    def _on_profile(self, name):
        import gd_metrics

        st = self._state or gd_metrics.gpu()
        if not st:
            gd_agent.finish(getattr(self, "_ext_done", None), False,
                            i18n.t("нет данных GPU (nvidia-smi недоступен)"))
            self._ext_done = None
            return
        target = gd_gpu.profile_target(st, name)
        pl = target.get("pl")
        if pl is None:
            gd_agent.finish(getattr(self, "_ext_done", None), False,
                            i18n.t("драйвер не сообщил лимит для этого профиля"))
            self._ext_done = None
            return
        self.sl_pl.setValue(int(pl))
        self.profile_desc.setText(
            i18n.t("Профиль «{v0}» → лимит {v1} Вт. Применяю…", v0=gd_gpu.GPU_PROFILES[name]['label'], v1=pl)
        )
        self._apply({"pl": pl}, profile_name=name)

    def _on_apply(self):
        self._source = "UI"
        self._apply({"pl": self.sl_pl.value()})

    def _ui_profile(self, name: str):
        """Клик по профилю в вкладке: источник — UI, а не трей/агент."""
        self._source = "UI"
        self._on_profile(name)

    def _apply(self, payload: dict, profile_name=None):
        # запуск хелпера (UAC) и запись nvidia-smi долгие — не морозим GUI
        if gd_bg.is_running(self._apply_worker):
            gd_agent.finish(getattr(self, "_ext_done", None), False,
                            i18n.t("занято: операция уже выполняется"))
            self._ext_done = None
            return
        self.btn_apply.setEnabled(False)
        self.profile_desc.setText(i18n.t("Применяю…"))

        self._apply_worker = gd_bg.submit(
            lambda: self.client.call({"v": 1, "cmd": "apply", **payload}),
            lambda resp: self._apply_done(resp, payload, profile_name),
            parent=self,
        )

    def _apply_done(self, resp, payload: dict, profile_name):
        self.btn_apply.setEnabled(True)
        if not resp.get("ok"):
            self._set_status(i18n.t("Драйвер не принял изменение: {v0}", v0=resp.get('error')), ok=False)
            self.profile_desc.setText(i18n.t("Изменение не применено."))
            gd_agent.finish(getattr(self, "_ext_done", None), False, resp.get("error"))
            self._ext_done = None
            return
        self._pending = payload
        self._active_profile = profile_name
        self._confirm_left = CONFIRM_SECONDS
        self.btn_keep.setVisible(True)
        self._confirm_timer.start()
        self._set_status(
            i18n.t("Применено: лимит {v0} Вт. Подтвердите за {v1} с, иначе вернётся прежнее значение.", v0=payload['pl'], v1=self._confirm_left), ok=True)
        self.profile_desc.setText(i18n.t("Лимит {v0} Вт — ожидает подтверждения.", v0=payload['pl']))
        self._journal("gpu_pl", i18n.t("{v0} Вт", v0=payload['pl']),
                      before=(self._state or {}).get("pl"), after=payload["pl"],
                      why=i18n.t("лимит мощности GPU"))
        gd_agent.finish(getattr(self, "_ext_done", None), True)
        self._ext_done = None

    def _confirm_tick(self):
        self._confirm_left -= 1
        if self._confirm_left <= 0:
            self._confirm_timer.stop()
            self._pending = None
            self.btn_keep.setVisible(False)
            self.profile_desc.setText(i18n.t("Подтверждение не получено — прежнее значение возвращено."))
            self._set_status(i18n.t("Время подтверждения вышло — возвращаю прежний лимит…"), ok=True)
            self._restore_async(self._auto_restore_done)
            return
        pl_val = (self._pending or {}).get("pl")
        self._set_status(
            i18n.t("Применено: лимит {v0} Вт. Подтвердите за {v1} с.", v0=pl_val, v1=self._confirm_left),
            ok=True)

    def _auto_restore_done(self, resp):
        self._set_status(
            i18n.t("Время подтверждения вышло — прежний лимит возвращён.")
            if resp.get("ok") else i18n.t("Откат не удался: {v0}", v0=resp.get('error')),
            ok=resp.get("ok", False))
        self._tick_later()

    def _restore_async(self, on_done) -> None:
        """Откат лимита в фоне (общий путь для таймера и кнопки)."""
        if gd_bg.is_running(self._restore_worker):
            gd_agent.finish(getattr(self, "_ext_done", None), False,
                            i18n.t("занято: операция уже выполняется"))
            self._ext_done = None
            return

        self._restore_worker = gd_bg.submit(
            lambda: self.client.call({"v": 1, "cmd": "restore"}), on_done, parent=self
        )

    def _on_keep(self):
        self._confirm_timer.stop()
        self.btn_keep.setVisible(False)
        pl = (self._pending or {}).get("pl")
        self._pending = None
        self.profile_group.setExclusive(False)
        for b in self.profile_btns.values():
            b.setChecked(False)
        self.profile_group.setExclusive(True)
        self.profile_desc.setText(i18n.t("Лимит {v0} Вт оставлен.", v0=pl))
        self._set_status(i18n.t("Изменение оставлено: лимит {v0} Вт.", v0=pl), ok=True)
        self._tick_later()

    def _ui_restore(self):
        """Клик «Вернуть исходный лимит» в вкладке: источник — UI."""
        self._source = "UI"
        self._on_restore()

    def _on_restore(self):
        self._confirm_timer.stop()
        self.btn_keep.setVisible(False)
        self._pending = None
        self._active_profile = None
        self.btn_restore.setEnabled(False)
        self._set_status(i18n.t("Возвращаю исходный лимит…"), ok=True)
        self._restore_async(self._restore_done)

    def _restore_done(self, resp):
        self.btn_restore.setEnabled(True)
        if resp.get("ok"):
            self._set_status(i18n.t("Исходный лимит возвращён."), ok=True)
            self.profile_desc.setText(i18n.t("Профиль не применён."))
            self._journal("restore", i18n.t("лимит GPU"), why=i18n.t("возврат исходного лимита"))
            self.profile_group.setExclusive(False)
            for b in self.profile_btns.values():
                b.setChecked(False)
            self.profile_group.setExclusive(True)
        else:
            self._set_status(i18n.t("Откат не удался: {v0}", v0=resp.get('error')), ok=False)
        gd_agent.finish(getattr(self, "_ext_done", None), resp.get("ok"), resp.get("error"))
        self._ext_done = None
        self._tick_later()

    def _set_status(self, text: str, ok: bool = True):
        self.bottom_status.setStyleSheet(f"color: {th.MUTED if ok else th.WARN};")
        self.bottom_status.setText(text)

    def _journal(self, kind: str, target: str, before=None, after=None, why: str = ""):
        try:
            import gd_journal

            gd_journal.record(kind, target, before, after, why=why,
                              source=getattr(self, "_source", "UI"))
            import gd_toast

            gd_toast.show_toast(i18n.t("toast.gpu", target=target),
                                "info" if str(kind).startswith("restore") else "ok")
        except Exception:  # noqa: BLE001
            pass

    # -- внешнее управление (трей, палитра команд, локальный API) -----------
    def apply_profile(self, name: str, source: str = i18n.t("трей"), on_done=None):
        """Применить профиль GPU извне (или вернуть исходный лимит).

        `on_done(result)` — хук внешних вызовов (локальный API, ТЗ этап 8):
        один раз получает ``{"ok", "error"}`` по завершении, в том числе при
        отказе «занято» или отсутствии данных nvidia-smi.
        """
        if gd_bg.is_running(self._apply_worker) or gd_bg.is_running(self._restore_worker):
            gd_agent.finish(on_done, False, i18n.t("занято: операция уже выполняется"))
            return
        self._source = source
        self._ext_done = on_done
        if name == "restore":
            self._on_restore()
            return
        self._on_profile(name)

    def apply_limit(self, watts: int, source: str = i18n.t("трей"), on_done=None):
        """Лимит мощности в ваттах извне: как движение слайдера + «Применить».

        Диапазон берём у драйвера (min_limit/max_limit из nvidia-smi), а не из
        слайдера: вкладка могла ещё не открываться, и границы были бы чужими.
        """
        if gd_bg.is_running(self._apply_worker) or gd_bg.is_running(self._restore_worker):
            gd_agent.finish(on_done, False, i18n.t("занято: операция уже выполняется"))
            return
        import gd_metrics

        st = self._state or gd_metrics.gpu() or gd_gpu.read_state() or {}
        lo = st.get("min_limit")
        hi = st.get("max_limit")
        if lo and hi and not (float(lo) <= float(watts) <= float(hi)):
            gd_agent.finish(on_done, False, i18n.t(
                "лимит {v0} Вт вне диапазона драйвера {v1}..{v2} Вт",
                v0=watts, v1=int(lo), v2=int(hi)))
            return
        self._source = source
        self._ext_done = on_done
        self.sl_pl.setValue(int(watts))
        self._apply({"pl": int(watts)})

    def current_profile(self):
        return getattr(self, "_active_profile", None)

    # -- отдельное окно ----------------------------------------------------
    def closeEvent(self, event):
        if self._standalone:
            try:
                self.client.shutdown_async(restore=True)
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyleSheet(th.STYLESHEET)
    w = GpuWidget(standalone=True)
    w.show()
    sys.exit(app.exec_())
