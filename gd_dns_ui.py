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

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
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

import gd_dns  # noqa: E402
import gd_journal  # noqa: E402
import gd_theme as th
import gd_i18n as i18n  # noqa: E402
from gd_power_ui import make_card  # noqa: E402


class MeasureWorker(QThread):
    done = pyqtSignal(dict)

    def run(self):
        try:
            self.done.emit(gd_dns.measure_resolvers())
        except Exception:  # noqa: BLE001
            self.done.emit({})


class DnsWidget(QWidget):
    def __init__(self, standalone: bool = False):
        super().__init__()
        self.setObjectName("PanelRoot")
        self._standalone = standalone
        self.setStyleSheet(th.STYLESHEET)
        self.client = None
        self._results: dict = {}
        self._worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

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
        for i, r in enumerate(gd_dns.RESOLVERS):
            self.table.setItem(i, 0, QTableWidgetItem(r["label"]))
            self.table.setItem(i, 1, QTableWidgetItem(", ".join(r["servers"]) or "—"))
            self.table.setItem(i, 2, QTableWidgetItem(i18n.t("не замерялось")))
            self.table.setItem(i, 3, QTableWidgetItem(""))
        self.table.setCurrentCell(0, 0)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        self.btn_measure = QPushButton(i18n.t("Замерить латентность"))
        self.btn_measure.setObjectName("PrimaryButton")
        self.btn_measure.clicked.connect(self._measure)
        self.btn_apply = QPushButton(i18n.t("Применить выбранный"))
        self.btn_apply.setObjectName("Segment")
        self.btn_apply.clicked.connect(self._apply)
        self.btn_restore = QPushButton(i18n.t("Вернуть как было"))
        self.btn_restore.setObjectName("Segment")
        self.btn_restore.clicked.connect(self._restore)
        row.addWidget(self.btn_measure)
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_restore)
        row.addStretch(1)
        lay.addLayout(row)

        crow = QHBoxLayout()
        crow.addWidget(QLabel(i18n.t("Свой сервер:")))
        self.ed_custom = QLineEdit()
        self.ed_custom.setPlaceholderText(i18n.t("например 192.168.0.1 — заполняет строку «Свой»"))
        self.ed_custom.editingFinished.connect(self._fill_custom)
        crow.addWidget(self.ed_custom, 1)
        lay.addLayout(crow)

        self.chk_doh = QCheckBox(i18n.t("Шифровать DNS по HTTPS (DoH)"))
        self.chk_doh.setToolTip(
            i18n.t("Доступно в Windows 11. Включается для адресов выбранного резолвера.")
        )
        if not gd_dns.doh_supported():
            self.chk_doh.setVisible(False)
        lay.addWidget(self.chk_doh)

        sign = QLabel(gd_dns.SIGNATURE)
        sign.setObjectName("Hint")
        sign.setWordWrap(True)
        lay.addWidget(sign)
        root.addWidget(card)

        root.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self._refresh_now()

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

    def _refresh_now(self):
        st = gd_dns.current_state()
        src = i18n.t("от DHCP") if st.get("mode") == "dhcp" else i18n.t("заданы вручную")
        self.lbl_now.setText(
            i18n.t("{v0}: {v1} ({v2})", v0=st.get('iface') or '—', v1=', '.join(st.get('servers') or []) or 'нет', v2=src)
        )
        self._saved = gd_dns.saved_state()

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
        if self._worker is not None and self._worker.isRunning():
            return
        self.btn_measure.setEnabled(False)
        self._set_status(i18n.t("Замеряю — реальные DNS-запросы по каждому серверу…"))
        self._worker = MeasureWorker(self)
        self._worker.done.connect(self._measure_done)
        self._worker.start()

    def _measure_done(self, results: dict):
        self._results = results or {}
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
            self._set_status(i18n.t("Замер готов. Быстрее всех: {v0} ({v1:.1f} мс).", v0=label, v1=self._results[best]['median']))
        else:
            self._set_status(i18n.t("Ни один резолвер не ответил."), ok=False)

    # -- смена --------------------------------------------------------------
    def _apply(self):
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

        before = gd_dns.current_state()
        if not self._saved or self._saved.get("iface") != before.get("iface"):
            gd_dns.save_state()          # запомнить прежнюю конфигурацию ДО смены
        resp = self._ensure().call({
            "cmd": "dns_apply", "mode": "static",
            "servers": servers, "iface": before.get("iface"),
        })
        if not resp.get("ok"):
            self._set_status(i18n.t("DNS не изменён: {v0}", v0=resp.get('error')), ok=False)
            return
        doh_msg = ""
        if self.chk_doh.isChecked():
            d = self._ensure().call({"cmd": "doh", "servers": servers,
                                     "template": r.get("doh"), "enable": True})
            doh_msg = f" DoH: {d.get('data') or d.get('error')}"
        self._refresh_now()
        self._set_status(
            i18n.t("DNS переключён на «{v0}» ({v1}) — было {v2}.{v3} Сравните латентность замером.", v0=r['label'], v1=', '.join(servers), v2=', '.join(before.get('servers') or ['DHCP']), v3=doh_msg), ok=True)
        gd_journal.record("dns", r["label"], ", ".join(before.get("servers") or ["DHCP"]),
                          ", ".join(servers), why=i18n.t("смена DNS-серверов"), source=i18n.t("утилиты"))
        import gd_toast
        import gd_i18n as i18n

        gd_toast.show_toast(i18n.t("toast.dns_apply", name=r["label"]), "ok")

    def _restore(self):
        st = self._saved or gd_dns.saved_state() or gd_dns.current_state()
        gd_dns.save_state()
        resp = self._ensure().call({
            "cmd": "dns_apply",
            "mode": st.get("mode") or "dhcp",
            "servers": st.get("servers") or [],
            "iface": st.get("iface"),
        })
        if not resp.get("ok"):
            self._set_status(i18n.t("Откат не удался: {v0}", v0=resp.get('error')), ok=False)
            return
        self._refresh_now()
        self._set_status(
            i18n.t("Конфигурация DNS возвращена ({v0}).", v0='DHCP' if st.get('mode') == 'dhcp' else ', '.join(st.get('servers') or [])),
            ok=True)
        gd_journal.record("dns", i18n.t("возврат"), None, None,
                          why=i18n.t("возврат прежней конфигурации DNS"), source=i18n.t("утилиты"))
        import gd_toast
        import gd_i18n as i18n

        gd_toast.show_toast(i18n.t("toast.dns_restore"), "info")
