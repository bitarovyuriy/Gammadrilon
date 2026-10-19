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
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_journal  # noqa: E402
import gd_theme as th  # noqa: E402
import gd_i18n as i18n  # noqa: E402


class JournalDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("Gammadrilon — журнал изменений"))
        self.resize(620, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.DIALOG_MARGINS)
        root.setSpacing(th.DIALOG_SPACING)

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
        # правый клик по записи: скопировать её или путь к файлу журнала —
        # отсюда строки уходят в письмо или отчёт без перепечатки руками
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        root.addWidget(self.list, 1)

        self.empty = QLabel(i18n.t("Пока пусто — изменений не было."))
        self.empty.setObjectName("Hint")
        root.addWidget(self.empty)

        row = QHBoxLayout()
        btn_copy = QPushButton(i18n.t("Скопировать путь"))
        btn_copy.setObjectName("Segment")
        btn_copy.setToolTip(i18n.t(
            "Путь к файлу журнала в буфер обмена — его можно вставить в проводник или письмо."))
        btn_copy.clicked.connect(self._copy_path)
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
        row.addWidget(btn_copy)
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

    # -- копирование: строка записи или путь к файлу --------------------------
    def _copy(self, text: str) -> str:
        """Положить текст в буфер и подтвердить это строкой состояния."""
        QApplication.clipboard().setText(text)
        return text

    def _copy_path(self) -> str:
        path = self._copy(str(gd_journal.journal_path()))
        self._show_note(i18n.t("Путь журнала скопирован: {v0}", v0=path), ok=True)
        return path

    def _copy_entry(self, item) -> None:
        if item is None:
            return
        self._copy(item.text())
        self._show_note(i18n.t("Запись скопирована в буфер обмена."), ok=True)

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        menu = QMenu(self)
        act_entry = menu.addAction(i18n.t("Скопировать запись"))
        act_entry.setEnabled(item is not None)
        act_entry.triggered.connect(lambda: self._copy_entry(item))
        act_path = menu.addAction(i18n.t("Скопировать путь журнала"))
        act_path.triggered.connect(self._copy_path)
        menu.exec_(self.list.viewport().mapToGlobal(pos))

    def _clear(self):
        cleared = gd_journal.clear()
        self.reload()
        if cleared:
            self._show_note(i18n.t("Журнал очищен."), ok=True)
        else:
            self._show_note(i18n.t("не удалось очистить журнал: {v0}",
                                   v0=gd_journal.write_error()), ok=False)
