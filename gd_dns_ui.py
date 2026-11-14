# -*- coding: utf-8 -*-
"""Вкладка «DNS» (ТЗ 6.2).

Замер — реальные DNS-запросы. Смена — только из явного списка, с сохранением прежней
конфигурации (включая DHCP) для точного отката. DoH — только если ОС поддерживает.
"""
from __future__ import annotations

import os
import sys

# --- PyQt5 и не-ASCII путь к venv (exit 127 без traceback) ---
try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
except Exception:  # noqa: BLE001
    pass

from PyQt5.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
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
import gd_dns  # noqa: E402
import gd_journal  # noqa: E402
import gd_theme as th  # noqa: E402
import gd_i18n as i18n  # noqa: E402
from gd_power_ui import make_card  # noqa: E402


class DnsWidget(QWidget):
    def __init__(self, standalone: bool = False):
        super().__init__()
        self.setObjectName("PanelRoot")
        self._standalone = standalone
        self.client = None
        self._results: dict = {}
        self._worker = None
        self._apply_worker = None     # фоновая смена/возврат DNS (UAC + netsh)
        self._changing = False
        self._resetting = False
        self._refresh_worker = None   # фоновое чтение текущего состояния DNS
        self._refresh_generation = 0
        self._doh_worker = None       # фоновая проверка поддержки DoH

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.PAGE_MARGINS)
        root.setSpacing(th.PAGE_SPACING)

        head = QHBoxLayout()
        title = QLabel("DNS")
        title.setObjectName("Title")
        title.setVisible(self._standalone)
        head.addWidget(title)
        head.addStretch(1)
        self.lbl_now = QLabel("—")
        self.lbl_now.setObjectName("Hint")
        head.addWidget(self.lbl_now)
        root.addLayout(head)

        card, lay = make_card(i18n.t("РЕЗОЛВЕРЫ"))
        self.table = QTableWidget(len([r for r in gd_dns.RESOLVERS if True]), 4)
        self.table.setHorizontalHeaderLabels([i18n.t("Резолвер"), i18n.t("Сервер"), i18n.t("Медиана"), i18n.t("Разброс / потери")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(2, 150)
        self.table.setMinimumHeight(230)
        # сетка сеткой, а разделяют строки линии-подчёркивания из QSS (`QTableWidget::item`)
        self.table.setShowGrid(False)
        for i, r in enumerate(gd_dns.RESOLVERS):
            self.table.setItem(i, 0, QTableWidgetItem(i18n.t(r["label"])))
            self.table.setItem(i, 1, QTableWidgetItem(", ".join(r["servers"]) or "—"))
            self.table.setItem(i, 2, QTableWidgetItem(i18n.t("не замерялось")))
            self.table.setItem(i, 3, QTableWidgetItem(""))
        self.table.setCurrentCell(0, 0)
        lay.addWidget(self.table)
        lay.addSpacing(8)

        row = QGridLayout()
        self.btn_measure = QPushButton(i18n.t("Замерить латентность"))
        self.btn_measure.setObjectName("PrimaryButton")
        self.btn_measure.clicked.connect(self._measure)
        self.btn_apply = QPushButton(i18n.t("Применить выбранный"))
        self.btn_apply.setObjectName("Segment")
        self.btn_apply.clicked.connect(lambda: self._apply())
        self.btn_restore = QPushButton(i18n.t("Вернуть как было"))
        self.btn_restore.setObjectName("Segment")
        self.btn_restore.clicked.connect(self._restore)
        row.addWidget(self.btn_measure, 0, 0)
        row.addWidget(self.btn_apply, 0, 1)
        row.addWidget(self.btn_restore, 1, 0)
        row.setColumnStretch(2, 1)
        lay.addLayout(row)

        crow = QHBoxLayout()
        crow.addWidget(QLabel(i18n.t("Свой сервер:")))
        self.ed_custom = QLineEdit()
        self.ed_custom.setPlaceholderText(i18n.t("например 192.168.0.1 — заполняет строку «Свой»"))
        self.ed_custom.editingFinished.connect(self._fill_custom)
        crow.addWidget(self.ed_custom, 1)
        lay.addLayout(crow)

        self.chk_doh = QCheckBox(i18n.t("Включить DoH для выбранного резолвера"))
        self.chk_doh.setToolTip(
            i18n.t("Windows 11 применит DoH для этих адресов на всех адаптерах. "
                   "Без галочки действующие настройки DoH сохраняются.")
        )
        # покажем после фоновой проверки поддержки — netsh в GUI-потоке недопустим
        self.chk_doh.setVisible(False)
        self.chk_doh.setEnabled(False)
        self.table.currentCellChanged.connect(self._sync_doh_selection)
        lay.addWidget(self.chk_doh)

        sign = QLabel(i18n.t(gd_dns.SIGNATURE))
        sign.setObjectName("Hint")
        sign.setWordWrap(True)
        lay.addWidget(sign)
        root.addWidget(card)

        root.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        # Ничего не читаем при сборке: периодического опроса DNS нет, а само открытие
        # вкладки и есть запрос (см. activate()). Отдельное окно открывается сразу —
        # для него запрос делаем здесь.
        self._doh_checked = False
        if self._standalone:
            self.activate()

    # -- вспомогательное ----------------------------------------------------
    def _ensure(self):
        if self.client is None:
            import gd_gpu

            self.client = gd_gpu.GpuClient()
        return self.client

    def _row_of(self, key: str) -> int:
        for i, r in enumerate(gd_dns.RESOLVERS):
            if r["key"] == key:
                return i
        return -1

    def _selected_key(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        return gd_dns.RESOLVERS[row]["key"]

    def activate(self):
        """Открытие вкладки — это и есть запрос на чтение.

        Периодического опроса DNS нет (см. ``gd_metrics``: читается только по
        запросу), поэтому всё, что стоит внешних процессов, запускается отсюда:
        текущая конфигурация (``netsh``) и разовая проверка поддержки DoH.
        """
        self._refresh_now()
        if not self._doh_checked:
            self._doh_checked = True
            self._doh_worker = gd_bg.submit_network_read(gd_dns.doh_supported, self._doh_done, parent=self)

    def _refresh_now(self):
        """Показать текущее DNS-состояние: чтение (netsh) — в фоне, по запросу."""
        self._refresh_generation += 1
        generation = self._refresh_generation

        def done(state):
            if generation == self._refresh_generation:
                self._refresh_done(state)

        self._refresh_worker = gd_bg.submit_network_read(gd_dns.current_state, done, parent=self)

    def _refresh_done(self, st):
        if not isinstance(st, dict) or "servers" not in st:
            return                    # фоновая ошибка — оставляем прошлую строку
        import gd_metrics

        # вкладка уже прочитала конфигурацию — отдаём её в общий кэш, чтобы
        # статусбар не запускал netsh второй раз
        gd_metrics.set_dns(st)
        mode = st.get("mode")
        if mode is None:
            self.lbl_now.setText(i18n.t("Не удалось прочитать конфигурацию DNS"))
            return
        src = i18n.t("от DHCP") if mode == "dhcp" else i18n.t("заданы вручную")
        self.lbl_now.setText(
            i18n.t("{v0} · IPv4: {v1} ({v2})", v0=st.get('iface') or '—',
                   v1=', '.join(st.get('servers') or []) or i18n.t('нет'), v2=src)
        )

    def _doh_done(self, ok):
        """Показать галочку DoH, если ОС поддерживает (проверка шла в фоне)."""
        if not ok:
            return
        try:
            self.chk_doh.setVisible(True)
            self._sync_doh_selection()
        except RuntimeError:          # виджет уже удалён
            pass

    def _sync_doh_selection(self):
        key = self._selected_key()
        enabled = any(r["key"] == key and r["doh"] for r in gd_dns.RESOLVERS)
        self.chk_doh.setEnabled(enabled)

    def _set_status(self, text, ok=True):
        self.status.setStyleSheet(f"color: {th.MUTED if ok else th.WARN};")
        self.status.setText(text)

    def _fill_custom(self):
        val = self.ed_custom.text().strip()
        if not val:
            return
        row = self._row_of("custom")
        if row >= 0:
            self.table.setItem(row, 1, QTableWidgetItem(val))
            self.table.setCurrentCell(row, 0)

    # -- замер --------------------------------------------------------------
    def _measure(self):
        if gd_bg.is_running(self._worker):
            return
        self.btn_measure.setEnabled(False)
        self._set_status(i18n.t("Замеряю — реальные DNS-запросы по каждому серверу…"))
        self._worker = gd_bg.submit_network_read(gd_dns.measure_resolvers, self._measure_done, parent=self)

    def _measure_done(self, results: dict):
        self._results = results if isinstance(results, dict) and results.get("ok") is not False else {}
        self.btn_measure.setEnabled(True)
        for key, res in self._results.items():
            row = self._row_of(key)
            if row < 0:
                continue
            if res.get("ok"):
                self.table.setItem(row, 2, QTableWidgetItem(i18n.t("{v0:.1f} мс", v0=res['median'])))
                self.table.setItem(row, 3, QTableWidgetItem(
                    i18n.t("±{v0:.1f} мс · потери {v1}%", v0=res['jitter'], v1=res['loss_pct'])))
            else:
                self.table.setItem(row, 2, QTableWidgetItem(i18n.t("нет ответа")))
                self.table.setItem(row, 3, QTableWidgetItem(i18n.t("потери {v0}%", v0=res.get('loss_pct'))))
        good = [k for k, v in self._results.items() if v.get("ok")]
        if good:
            best = min(good, key=lambda k: self._results[k]["median"])
            label = next((r["label"] for r in gd_dns.RESOLVERS if r["key"] == best), best)
            self._set_status(i18n.t("Замер готов. Быстрее всех: {v0} ({v1:.1f} мс).",
                                   v0=i18n.t(label), v1=self._results[best]['median']))
        else:
            self._set_status(i18n.t("Ни один резолвер не ответил."), ok=False)

    # -- смена --------------------------------------------------------------
    def set_resetting(self, resetting: bool):
        self._resetting = resetting
        self._sync_change_buttons()
        if not resetting:
            self._refresh_now()

    def _sync_change_buttons(self):
        enabled = not (self._changing or self._resetting)
        self.btn_apply.setEnabled(enabled)
        self.btn_restore.setEnabled(enabled)

    def set_provider(self, key: str, source: str = i18n.t("трей")):
        """Смена DNS на резолвер из списка (трей, палитра)."""
        row = self._row_of(key)
        if row < 0 or key == "custom" or self._changing or self._resetting:
            return
        self.table.setCurrentCell(row, 0)
        self._apply(source=source)

    def _apply(self, source=None):
        if self._changing or self._resetting:
            return
        self._source = source or i18n.t("утилиты")

        key = self._selected_key()
        if key is None:
            return
        r = next((x for x in gd_dns.RESOLVERS if x["key"] == key), None)
        if r is None:
            return
        servers = list(r["servers"])
        if key == "custom":
            val = self.ed_custom.text().strip()
            if not val:
                self._set_status(i18n.t("Укажите адрес для строки «Свой»."), ok=False)
                return
            servers = [s.strip() for s in val.replace(";", ",").split(",") if s.strip()]
        if not servers:
            self._set_status(i18n.t("У резолвера нет адресов."), ok=False)
            return

        client = self._ensure()
        template = r["doh"] if self.chk_doh.isChecked() else None
        self._changing = True
        self._sync_change_buttons()
        self._set_status(i18n.t("Применяю DNS…"))

        self._apply_worker = gd_bg.submit(
            lambda: client.call({"cmd": "dns_apply", "mode": "static", "servers": servers,
                                 "template": template}, timeout=None),
            lambda resp: self._apply_done(resp, r, servers), parent=self
        )

    def _apply_done(self, resp, r, servers):
        self._changing = False
        self._sync_change_buttons()
        if not resp.get("ok"):
            self._refresh_now()
            self._set_status(i18n.t("DNS изменён частично: {v0}", v0=resp.get('error'))
                             if resp.get("partial") else
                             i18n.t("DNS: ошибка применения, проверьте текущее состояние: {v0}",
                                    v0=resp.get('error')), ok=False)
            return
        before = resp.get("before") or {}
        doh_msg = f" {resp['doh']}." if resp.get("doh") else ""
        label = i18n.t(r["label"])
        self._refresh_now()
        self._set_status(
            i18n.t("DNS переключён на «{v0}» ({v1}) — было {v2}.{v3} Сравните латентность замером.",
                   v0=label, v1=", ".join(servers), v2=", ".join(before.get("servers") or ["DHCP"]), v3=doh_msg),
            ok=True)
        gd_journal.record("dns", label, ", ".join(before.get("servers") or ["DHCP"]),
                          ", ".join(servers), why=i18n.t("смена DNS-серверов"),
                          source=getattr(self, "_source", i18n.t("утилиты")),
                          reversible=True)
        import gd_toast

        gd_toast.show_toast(i18n.t("toast.dns_apply", name=label), "ok")

    def _restore(self):
        if self._changing or self._resetting:
            return
        aliases = gd_dns.legacy_snapshot_aliases()
        if aliases:
            import gd_dialog

            confirmed = gd_dialog.confirm(
                self, i18n.t("Восстановить старый снимок DNS"),
                i18n.t("Снимок хранит только имя адаптера «{v0}». Убедитесь, что это тот же адаптер, "
                       "затем подтвердите восстановление IPv4.", v0="», «".join(aliases)),
                confirm_text=i18n.t("Вернуть как было"), destructive=True)
            if not confirmed or self._changing or self._resetting:
                return
        client = self._ensure()
        self._changing = True
        self._sync_change_buttons()
        self._set_status(i18n.t("Возвращаю прежнюю конфигурацию DNS…"))

        request = {"cmd": "dns_restore"}
        if aliases:
            request["confirmed_aliases"] = aliases
        self._apply_worker = gd_bg.submit(
            lambda: client.call(request, timeout=None), self._restore_done, parent=self)

    def _restore_done(self, resp):
        self._changing = False
        self._sync_change_buttons()
        self._refresh_now()
        if not resp.get("ok"):
            self._set_status(i18n.t("Откат не удался: {v0}", v0=resp.get('error')), ok=False)
            return
        if not resp.get("changed"):
            self._set_status(i18n.t("Нет сохранённых изменений DNS."))
            return
        self._set_status(i18n.t("Прежняя конфигурация DNS восстановлена."))
        gd_journal.record("dns", i18n.t("возврат"), None, None,
                          why=i18n.t("возврат прежней конфигурации DNS"), source=i18n.t("утилиты"))
        import gd_toast

        gd_toast.show_toast(i18n.t("toast.dns_restore"), "info")
