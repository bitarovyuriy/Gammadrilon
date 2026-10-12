# -*- coding: utf-8 -*-
"""Окно «Журнал изменений» (ТЗ 5.5): что менялось, было → стало, чем вызвано."""
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

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_journal  # noqa: E402
import gd_theme as th
import gd_i18n as i18n  # noqa: E402


class JournalDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("Gammadrilon — журнал изменений"))
        self.resize(620, 480)
        self.setStyleSheet(th.STYLESHEET)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel(i18n.t("Что произошло"))
        title.setObjectName("Title")
        root.addWidget(title)

        hint = QLabel(
            i18n.t("Каждое изменение питания: что было, что стало и чем вызвано (вкладка, трей или горячая клавиша).")
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # честное состояние самого журнала: путь, число записей, доступность записи
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("Hint")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setVisible(False)
        root.addWidget(self.lbl_status)

        self.list = QListWidget()
        self.list.setObjectName("JournalList")
        self.list.setAlternatingRowColors(False)
        self.list.setSelectionMode(QListWidget.NoSelection)
        root.addWidget(self.list, 1)

        self.empty = QLabel(i18n.t("Пока пусто — изменений не было."))
        self.empty.setObjectName("Hint")
        root.addWidget(self.empty)

        row = QHBoxLayout()
        btn_refresh = QPushButton(i18n.t("Обновить"))
        btn_refresh.setObjectName("Segment")
        btn_refresh.clicked.connect(self.reload)
        btn_clear = QPushButton(i18n.t("Очистить журнал"))
        btn_clear.setObjectName("Segment")
        btn_clear.clicked.connect(self._clear)
        btn_close = QPushButton(i18n.t("Закрыть"))
        btn_close.setObjectName("PrimaryButton")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_refresh)
        row.addWidget(btn_clear)
        row.addStretch(1)
        row.addWidget(btn_close)
        root.addLayout(row)

        self.reload()

    def reload(self):
        self.list.clear()
        report = gd_journal.read_report(200)
        items = report["items"]
        for e in items:
            it = QListWidgetItem(gd_journal.human(e))
            it.setToolTip(
                i18n.t("причина: {v0}\nисточник: {v1}\nоткат: {v2}", v0=e.get('why') or '—',
                       v1=e.get('source') or '—',
                       v2=i18n.t('доступен') if e.get('reversible') else i18n.t('нет'))
            )
            self.list.addItem(it)
        self.empty.setVisible(not items)
        self.list.setVisible(bool(items))

        ok, err = gd_journal.probe()
        if not ok:
            self._show_note(i18n.t(
                "Журнал недоступен для записи: {v0}. Новые изменения в него не попадут.",
                v0=err), ok=False)
        elif report["broken"]:
            self._show_note(i18n.t("повреждено строк: {v0} — они пропущены.",
                                   v0=report["broken"]), ok=False)
        else:
            self._show_note(i18n.t("файл журнала: {v0} · записей: {v1}",
                                   v0=str(gd_journal.journal_path()),
                                   v1=report["total"]), ok=True)

    def _show_note(self, text: str, ok: bool = True):
        self.lbl_status.setStyleSheet(f"color: {th.MUTED if ok else th.WARN};")
        self.lbl_status.setText(text)
        self.lbl_status.setVisible(bool(text))

    def _clear(self):
        cleared = gd_journal.clear()
        self.reload()
        if cleared:
            self._show_note(i18n.t("Журнал очищен."), ok=True)
        else:
            self._show_note(i18n.t("не удалось очистить журнал: {v0}",
                                   v0=gd_journal.write_error()), ok=False)
