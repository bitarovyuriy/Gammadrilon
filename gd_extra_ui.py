# -*- coding: utf-8 -*-
"""Панель прочих утилит (ТЗ 6.3): таймер, сетевой чек, сброс всего, профили, тихий час."""
from __future__ import annotations

import json
import os
import sys

try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
except Exception:  # noqa: BLE001
    pass

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_bg  # noqa: E402
import gd_dialog  # noqa: E402
import gd_extra  # noqa: E402
import gd_journal  # noqa: E402
import gd_profiles  # noqa: E402
import gd_theme as th  # noqa: E402
import gd_i18n as i18n  # noqa: E402
from gd_power_ui import make_card  # noqa: E402


class ProbeWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, mode="probe", parent=None):
        super().__init__(parent)
        self.mode = mode

    def run(self):
        try:
            if self.mode == "probe":
                self.done.emit({"probe": gd_extra.probe(),
                                "retrans": gd_extra.tcp_retransmit_rate()})
            else:
                self.done.emit({"bufferbloat": gd_extra.bufferbloat()})
        except Exception as exc:  # noqa: BLE001
            self.done.emit({"error": str(exc)})


class ExtraPanel(QWidget):
    """Набор карточек для вкладки «Утилиты». host — главное окно (для сброса и гаммы)."""

    def __init__(self, host=None, parent=None):
        super().__init__(parent)
        self.host = host
        self._workers: list[ProbeWorker] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # --- таймер ---------------------------------------------------------
        card, c = make_card(i18n.t("ТОЧНОСТЬ СИСТЕМНОГО ТАЙМЕРА"))
        row = QHBoxLayout()
        self.lbl_timer = QLabel("—")
        self.lbl_timer.setObjectName("Metric")
        self.chk_timer = QCheckBox(i18n.t("Запросить 0.5 мс"))
        self.chk_timer.toggled.connect(self._timer_toggle)
        row.addWidget(self.lbl_timer)
        row.addStretch(1)
        row.addWidget(self.chk_timer)
        c.addLayout(row)
        tip = QLabel(gd_extra.TIMER_SIGNATURE)
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        c.addWidget(tip)
        lay.addWidget(card)

        # --- сетевой чек ----------------------------------------------------
        card2, c2 = make_card(i18n.t("СЕТЕВОЙ ЧЕК — ЧТО РЕАЛЬНО ОБЪЯСНЯЕТ ЛАГИ"))
        self.table = QTableWidget(len(gd_extra.TCP_PROBES), 4)
        self.table.setHorizontalHeaderLabels([i18n.t("Узел"), i18n.t("Медиана"), i18n.t("Джиттер"), i18n.t("Потери")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)   # строки разделяют линии из QSS, а не сетка
        self.table.setMaximumHeight(130)
        for i, (h, p) in enumerate(gd_extra.TCP_PROBES):
            self.table.setItem(i, 0, QTableWidgetItem(f"{h}:{p}"))
            for col in (1, 2, 3):
                self.table.setItem(i, col, QTableWidgetItem("—"))
        c2.addWidget(self.table)
        r2 = QHBoxLayout()
        self.btn_probe = QPushButton(i18n.t("Замерить задержку"))
        self.btn_probe.setObjectName("Segment")
        self.btn_probe.clicked.connect(self._probe)
        self.btn_bb = QPushButton(i18n.t("Проверить bufferbloat"))
        self.btn_bb.setObjectName("Segment")
        self.btn_bb.clicked.connect(self._bufferbloat)
        r2.addWidget(self.btn_probe)
        r2.addWidget(self.btn_bb)
        r2.addStretch(1)
        c2.addLayout(r2)
        self.lbl_net = QLabel("—")
        self.lbl_net.setObjectName("Hint")
        self.lbl_net.setWordWrap(True)
        c2.addWidget(self.lbl_net)
        tip2 = QLabel(gd_extra.NET_SIGNATURE)
        tip2.setObjectName("Hint")
        tip2.setWordWrap(True)
        c2.addWidget(tip2)
        lay.addWidget(card2)

        # --- сброс всего ----------------------------------------------------
        card3, c3 = make_card(i18n.t("СБРОСИТЬ ВСЕ ИЗМЕНЕНИЯ GAMMADRILON"))
        r3 = QHBoxLayout()
        desc = QLabel(i18n.t("Питание CPU и GPU, DNS, правила, автоочистка, точность таймера, гамма — всё возвращается к состоянию системы по умолчанию."))
        desc.setObjectName("Hint")
        desc.setWordWrap(True)
        r3.addWidget(desc, 1)
        self.btn_reset = QPushButton(i18n.t("Сбросить всё"))
        self.btn_reset.setObjectName("DangerButton")
        self.btn_reset.clicked.connect(self._reset_all)
        r3.addWidget(self.btn_reset)
        c3.addLayout(r3)
        lay.addWidget(card3)

        # --- профили --------------------------------------------------------
        card4, c4 = make_card(i18n.t("НАСТРОЙКИ И ПРОФИЛИ — ЭКСПОРТ / ИМПОРТ"))
        r4 = QHBoxLayout()
        self.btn_export = QPushButton(i18n.t("Экспорт в JSON…"))
        self.btn_export.setObjectName("Segment")
        self.btn_export.clicked.connect(self._export)
        self.btn_import = QPushButton(i18n.t("Импорт из JSON…"))
        self.btn_import.setObjectName("Segment")
        self.btn_import.clicked.connect(self._import)
        r4.addWidget(self.btn_export)
        r4.addWidget(self.btn_import)
        r4.addStretch(1)
        c4.addLayout(r4)
        self.lbl_prof = QLabel(i18n.t("Один файл переносит настройки приложения, пресеты гаммы, правила, «тихий час» и автоочистку; профили CPU/GPU встроенные. Настройки применятся после перезапуска."))
        self.lbl_prof.setObjectName("Hint")
        self.lbl_prof.setWordWrap(True)
        c4.addWidget(self.lbl_prof)
        lay.addWidget(card4)

        # --- тихий час ------------------------------------------------------
        card5, c5 = make_card(i18n.t("ТИХИЙ ЧАС — ЭКО И МИНИМУМ ЯРКОСТИ ПО РАСПИСАНИЮ"))
        r5 = QHBoxLayout()
        self.chk_quiet = QCheckBox(i18n.t("Включён"))
        self.chk_quiet.toggled.connect(self._quiet_changed)
        r5.addWidget(self.chk_quiet)
        r5.addWidget(QLabel(i18n.t("с")))
        self.ed_start = QLineEdit()
        self.ed_start.setMaximumWidth(70)
        self.ed_start.setPlaceholderText("23:00")
        self.ed_start.editingFinished.connect(self._quiet_changed)
        r5.addWidget(self.ed_start)
        r5.addWidget(QLabel(i18n.t("до")))
        self.ed_end = QLineEdit()
        self.ed_end.setMaximumWidth(70)
        self.ed_end.setPlaceholderText("08:00")
        self.ed_end.editingFinished.connect(self._quiet_changed)
        r5.addWidget(self.ed_end)
        r5.addStretch(1)
        c5.addLayout(r5)
        self.lbl_quiet = QLabel("—")
        self.lbl_quiet.setObjectName("Hint")
        self.lbl_quiet.setWordWrap(True)
        c5.addWidget(self.lbl_quiet)
        lay.addWidget(card5)

        self.refresh()

    # -- данные --------------------------------------------------------------
    def refresh(self):
        ms = gd_extra.timer_ms()
        self.lbl_timer.setText(i18n.t("сейчас: {v0} мс", v0=ms) if ms else i18n.t("н/д"))
        q = gd_profiles.load_quiet()
        self.chk_quiet.blockSignals(True)
        self.ed_start.blockSignals(True)
        self.ed_end.blockSignals(True)
        self.chk_quiet.setChecked(bool(q.get("enabled")))
        self.ed_start.setText(q.get("start", "23:00"))
        self.ed_end.setText(q.get("end", "08:00"))
        self.chk_quiet.blockSignals(False)
        self.ed_start.blockSignals(False)
        self.ed_end.blockSignals(False)
        active = gd_profiles.in_window(q)
        self.lbl_quiet.setText(
            (i18n.t("Сейчас окно «тихого часа» — применены ЭКО и минимум яркости.") if q.get("applied")
             else i18n.t("Сейчас окно «тихого часа» активно.") if active
             else i18n.t("Вне окна «тихого часа».")))

    def _set_status(self, label, text, ok=True):
        label.setStyleSheet(f"color: {th.MUTED if ok else th.WARN};")
        label.setText(text)

    # -- таймер --------------------------------------------------------------
    def _timer_toggle(self, on: bool):
        before = gd_extra.timer_ms()
        ok, actual = gd_extra.set_timer_resolution(bool(on))
        # факт берём из ответа вызова, а не только из повторного чтения: между ними
        # значение могло измениться (например, другой процесс его отпустил)
        ms = gd_extra.ms_from_100ns(actual)
        if ms is None:
            ms = gd_extra.timer_ms()
        self.lbl_timer.setText(i18n.t("сейчас: {v0} мс", v0=ms) if ms else i18n.t("н/д"))
        import gd_toast

        if not ok:
            # запрос не принят системой: тост «0,5 мс» был бы выдуманным успехом
            self._set_status(
                self.lbl_net,
                i18n.t("Не удалось изменить точность таймера: система отказала "
                       "(сейчас {v0}).",
                       v0=i18n.t("{v0} мс", v0=ms) if ms else i18n.t("н/д")),
                ok=False,
            )
            gd_journal.record("timer", i18n.t("точность системного таймера"),
                              gd_extra.ms_from_100ns(before) if before else None,
                              ms, why=i18n.t("система отказала в запросе точности таймера"),
                              source=i18n.t("утилиты"), reversible=False)
            gd_toast.show_toast(i18n.t("toast.timer_failed"), "warn")
            return

        if ms is None:
            # запрос мог пройти, но фактическое значение не читается: успех подтвердить нечем
            self._set_status(self.lbl_net, i18n.t(
                "Точность таймера: запрос принят, но фактическое значение не подтвердилось — "
                "считайте, что оно не изменилось."), ok=False)
            gd_journal.record("timer", i18n.t("точность системного таймера"),
                              gd_extra.ms_from_100ns(before) if before else None, None,
                              why=i18n.t("не удалось прочитать фактическую точность таймера"),
                              source=i18n.t("утилиты"), reversible=False)
            gd_toast.show_toast(i18n.t("toast.timer_failed"), "warn")
            return

        if on:
            if abs(ms - 0.5) > 0.01:
                # система приняла запрос, но фактическое значение другое — показываем факт
                self._set_status(
                    self.lbl_net,
                    i18n.t("Система дала {v0} мс вместо 0,5 — платформа не поддерживает "
                           "запрошенное значение. Действует, пока приложение запущено.",
                           v0=ms),
                    ok=False,
                )
                gd_toast.show_toast(i18n.t("toast.timer_other", ms=ms), "warn")
            else:
                self._set_status(
                    self.lbl_net,
                    i18n.t("Точность таймера: 0,5 мс — действует, пока приложение "
                           "запущено, и сбрасывается при выходе."),
                    ok=True,
                )
                gd_toast.show_toast(i18n.t("toast.timer_on", ms=ms), "ok")
        else:
            self._set_status(
                self.lbl_net,
                i18n.t("Точность таймера: по умолчанию ({v0} мс).",
                       v0=ms if ms else i18n.t("н/д")),
                ok=True,
            )
            gd_toast.show_toast(i18n.t("toast.timer_off"), "ok")

        gd_journal.record("timer", i18n.t("точность системного таймера"),
                          gd_extra.ms_from_100ns(before) if before else None, ms,
                          why=i18n.t("точность системного таймера"), source=i18n.t("утилиты"))

    # -- сеть ----------------------------------------------------------------
    def _probe(self):
        self.btn_probe.setEnabled(False)
        self._set_status(self.lbl_net, i18n.t("Замеряю задержку до узлов…"))
        w = ProbeWorker("probe", self)
        w.done.connect(self._probe_done)
        w.finished.connect(lambda: self.btn_probe.setEnabled(True))
        self._workers.append(w)
        w.start()

    def _probe_done(self, data: dict):
        if data.get("error"):
            self._set_status(self.lbl_net, i18n.t("Ошибка замера: {v0}", v0=data['error']), ok=False)
            return
        for i, (h, p) in enumerate(gd_extra.TCP_PROBES):
            res = data.get("probe", {}).get(f"{h}:{p}", {})
            if res.get("median") is not None:
                self.table.setItem(i, 1, QTableWidgetItem(i18n.t("{v0:.1f} мс", v0=res['median'])))
                self.table.setItem(i, 2, QTableWidgetItem(i18n.t("±{v0:.1f} мс", v0=res['jitter'])))
                self.table.setItem(i, 3, QTableWidgetItem(f"{res['loss_pct']}%"))
            else:
                self.table.setItem(i, 1, QTableWidgetItem(i18n.t("нет ответа")))
                self.table.setItem(i, 2, QTableWidgetItem(""))
                self.table.setItem(i, 3, QTableWidgetItem("100%"))
        rt = data.get("retrans")
        if rt:
            self._set_status(self.lbl_net,
                             i18n.t("Повторно передано сегментов TCP с момента загрузки: {v0}% ({v1} из {v2}). Активных соединений: {v3}.", v0=rt['pct'], v1=rt['retrans_segs'], v2=rt['out_segs'], v3=rt['curr_estab']))
        else:
            self._set_status(self.lbl_net, i18n.t("Замер готов (счётчики TCP недоступны)."))

    def _bufferbloat(self):
        self.btn_bb.setEnabled(False)
        self._set_status(self.lbl_net, i18n.t("Качаю ~5 МБ и параллельно мерю задержку…"))
        w = ProbeWorker("bb", self)
        w.done.connect(self._bb_done)
        w.finished.connect(lambda: self.btn_bb.setEnabled(True))
        self._workers.append(w)
        w.start()

    def _bb_done(self, data: dict):
        if data.get("error"):
            self._set_status(self.lbl_net, i18n.t("Ошибка: {v0}", v0=data['error']), ok=False)
            return
        bb = data.get("bufferbloat", {})
        if bb.get("loaded") is None:
            self._set_status(self.lbl_net, i18n.t(
                "Bufferbloat: под нагрузкой узлы не отвечали — класс не посчитан."), ok=False)
            return
        if bb.get("idle") is None:
            self._set_status(self.lbl_net, i18n.t(
                "Bufferbloat: базу без нагрузки измерить не удалось (узлы не отвечали) — "
                "класс не посчитан."), ok=False)
            return
        if bb.get("increase") is None:
            # нагрузка не создалась: считать рост задержки не с чем сравнивать
            err = bb.get("load_error") or ""
            self._set_status(self.lbl_net, i18n.t(
                "Bufferbloat: задержка {v0} → {v1} мс, но нагрузку создать не удалось "
                "(скачано {v2} КБ{v3}) — класс не посчитан.",
                v0=bb["idle"], v1=bb["loaded"],
                v2=round((bb.get("load_bytes") or 0) / 1024),
                v3=f" — {err}" if err else ""), ok=False)
            return
        self._set_status(
            self.lbl_net,
            i18n.t("Bufferbloat до ближайшего узла: {v0} → {v1} мс (+{v2} мс), класс {v3}. ", v0=bb['idle'], v1=bb['loaded'], v2=bb['increase'], v3=bb['grade'])
            + (i18n.t("Задержка под нагрузкой почти не растёт.") if bb["grade"] == "A"
               else i18n.t("Задержка заметно растёт под нагрузкой — возможны лаги в звонках/играх.")))

    # -- сброс / профили -----------------------------------------------------
    def _reset_all(self):
        if self.host is None or not hasattr(self.host, "reset_everything"):
            self._set_status(self.lbl_prof, i18n.t("Сброс доступен из главного окна."), ok=False)
            return
        ans = gd_dialog.confirm(
            self, i18n.t("Сбросить все изменения"),
            i18n.t("Вернуть питание CPU/GPU, DNS, правила, автоочистку, таймер и гамму к состоянию системы по умолчанию?"),
            confirm_text=i18n.t("Сбросить всё"), destructive=True)
        if not ans:
            return
        report = self.host.reset_everything()
        self._set_status(self.lbl_prof, i18n.t("Сброшено: ") + (", ".join(report) if report else i18n.t("нечего сбрасывать")) + ".")
        self.refresh()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, i18n.t("Экспорт настроек"), "gammadrilon-settings.json",
                                              "JSON (*.json)")
        if not path:
            return
        # Снимок конфига берём здесь, в GUI-потоке: в фоне читать его нельзя —
        # окно может менять настройки прямо в этот момент (слайдеры, галочки).
        settings = None
        config = getattr(self.host, "config", None) if self.host is not None else None
        if isinstance(config, dict):
            try:
                settings = json.loads(json.dumps(config))
            except (TypeError, ValueError):
                settings = None
        # сбор снимка читает DNS/GPU/правила — уводим в фон, чтобы окно не замирало
        self._set_status(self.lbl_prof, i18n.t("Экспортирую настройки…"))
        gd_bg.submit(lambda: gd_profiles.export_all(path, app_settings=settings),
                     self._export_done, parent=self)

    def _export_done(self, res):
        ok, msg = res if isinstance(res, tuple) else (False, str(res))
        self._set_status(self.lbl_prof, msg, ok=ok)

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, i18n.t("Импорт настроек"), "", "JSON (*.json)")
        if not path:
            return
        # Настройки приложения применяет окно: только оно знает путь конфига и
        # умеет проверить чужой файл по структуре умолчаний.
        apply_settings = getattr(self.host, "import_app_settings", None) if self.host is not None else None
        ok, msg = gd_profiles.import_all(path, apply_settings=apply_settings)
        self._set_status(self.lbl_prof, msg, ok=ok)
        if ok:
            self.refresh()

    # -- тихий час -----------------------------------------------------------
    def _quiet_changed(self, *_):
        q = gd_profiles.load_quiet()
        q["enabled"] = self.chk_quiet.isChecked()
        q["start"] = self.ed_start.text().strip() or "23:00"
        q["end"] = self.ed_end.text().strip() or "08:00"
        q["applied"] = False
        saved = gd_profiles.save_quiet(q)
        self.refresh()
        if not saved:
            # refresh() уже вернул на экран фактические значения из файла —
            # честно объясняем, почему правка не удержалась
            self._set_status(self.lbl_quiet, i18n.t(
                "Настройка «тихий час» не сохранена: файл недоступен для записи."), ok=False)
