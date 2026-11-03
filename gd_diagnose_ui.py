# -*- coding: utf-8 -*-
"""Вкладка «Диагностика»: что в системе есть, чего не хватает и что делать.

Проверки живут в gd_diagnose (без Qt), здесь — только показ и выгрузка отчёта.
Тяжёлый прогон (WMI, реестр) идёт в фоне: окно не должно мерзнуть.
"""
from __future__ import annotations

import json
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
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_bg  # noqa: E402
import gd_diagnose  # noqa: E402
import gd_theme as th  # noqa: E402
import gd_i18n as i18n  # noqa: E402
from gd_power_ui import make_card  # noqa: E402

STATUS_COLOR = {
    gd_diagnose.OK: "#69B36B",
    gd_diagnose.WARN: "#D8A657",
    gd_diagnose.FAIL: "#D65C5C",
    gd_diagnose.SKIP: "#8A8A8A",
}


class DiagnoseWidget(QWidget):
    """Список проверок со светофором и выгрузкой обезличенного отчёта."""

    def __init__(self, standalone: bool = False, host=None):
        super().__init__()
        self.setObjectName("PanelRoot")
        self._standalone = standalone
        self.host = host
        self._worker = None
        self._items: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.PAGE_MARGINS)
        root.setSpacing(th.PAGE_SPACING)

        head = QHBoxLayout()
        title = QLabel(i18n.t("Диагностика"))
        title.setObjectName("Title")
        title.setVisible(self._standalone)
        head.addWidget(title)
        head.addStretch(1)
        root.addLayout(head)

        card, lay = make_card(
            i18n.t("ДИАГНОСТИКА ОКРУЖЕНИЯ"),
            i18n.t("Проверяет Windows, права, пути, видеокарту, датчики, .NET, "
                   "zapret, прокси и UAC. Отчёт обезличен: имя пользователя, "
                   "IP и MAC заменяются."),
        )
        row = QHBoxLayout()
        self.btn_check = QPushButton(i18n.t("Проверить"))
        self.btn_check.setObjectName("PrimaryButton")
        self.btn_check.clicked.connect(self.run_checks)
        row.addWidget(self.btn_check)
        self.btn_copy = QPushButton(i18n.t("Копировать отчёт"))
        self.btn_copy.setObjectName("Segment")
        self.btn_copy.clicked.connect(self.copy_report)
        row.addWidget(self.btn_copy)
        self.btn_save = QPushButton(i18n.t("Сохранить…"))
        self.btn_save.setObjectName("Segment")
        self.btn_save.clicked.connect(self.save_report)
        row.addWidget(self.btn_save)

        # один файл вместо переписки «пришлите лог, отчёт и версию»
        self.btn_archive = QPushButton(i18n.t("Архив для поддержки"))
        self.btn_archive.setObjectName("Segment")
        self.btn_archive.clicked.connect(self.save_archive)
        row.addWidget(self.btn_archive)
        row.addStretch(1)
        lay.addLayout(row)

        self.list = QListWidget()
        self.list.setObjectName("PlainList")
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setMinimumHeight(260)
        lay.addWidget(self.list)

        self.lbl_status = QLabel(i18n.t("Проверки ещё не запускались."))
        self.lbl_status.setObjectName("Hint")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)
        root.addWidget(card)
        root.addStretch(1)

    # -- действия ------------------------------------------------------------
    def run_checks(self):
        """Прогон в фоне: WMI и реестр могут занять секунды."""
        if gd_bg.is_running(self._worker):
            return
        self.btn_check.setEnabled(False)
        self.lbl_status.setText(i18n.t("Проверяю…"))
        self._worker = gd_bg.submit(gd_diagnose.checks, self._show_items, parent=self)

    def _show_items(self, items):
        self.btn_check.setEnabled(True)
        self._items = list(items or [])
        self.list.clear()
        for item in self._items:
            mark = gd_diagnose.MARKS.get(item["status"], "?")
            line = f"{mark}  {item['title']}: {item['detail']}"
            if item.get("action"):
                line += f"  →  {item['action']}"
            row = QListWidgetItem(line)
            row.setForeground(QColor(STATUS_COLOR.get(item["status"], "#C4C4C4")))
            self.list.addItem(row)
        bad = sum(1 for item in self._items
                  if item["status"] in (gd_diagnose.FAIL, gd_diagnose.WARN))
        self.lbl_status.setText(
            i18n.t("Проверок: {v0}. Требуют внимания: {v1}.", v0=len(self._items), v1=bad)
            if bad else i18n.t("Проверок: {v0}. Всё в порядке.", v0=len(self._items))
        )

    def report_text(self) -> str:
        return gd_diagnose.report_text(self._items) if self._items else ""

    def copy_report(self):
        if not self._items:
            self.lbl_status.setText(i18n.t("Сначала выполните проверку."))
            return
        from PyQt5.QtWidgets import QApplication

        QApplication.clipboard().setText(self.report_text())
        self.lbl_status.setText(i18n.t("Отчёт скопирован в буфер обмена."))

    def save_report(self):
        if not self._items:
            self.lbl_status.setText(i18n.t("Сначала выполните проверку."))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.t("Сохранить отчёт"),
            os.path.join(os.path.expanduser("~"), "Gammadrilon-diagnostika.txt"),
            i18n.t("Текст (*.txt);;JSON (*.json)"),
        )
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                text = json.dumps(gd_diagnose.report_json(self._items),
                                  ensure_ascii=False, indent=2)
            else:
                text = self.report_text()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            self.lbl_status.setText(i18n.t("Не удалось сохранить: {v0}", v0=exc))
            return
        self.lbl_status.setText(i18n.t("Отчёт сохранён: {v0}", v0=path))

    def save_archive(self):
        """Диагностика + журналы одним zip: фоном, файл рядом с логами."""
        import gd_support

        if gd_bg.is_running(getattr(self, "_archive_worker", None)):
            return
        self.btn_archive.setEnabled(False)
        self.lbl_status.setText(i18n.t("Собираю архив…"))
        self._archive_worker = gd_bg.submit(
            lambda: gd_support.build(items=self._items or None),
            self._archive_done, parent=self)

    def _archive_done(self, result):
        self.btn_archive.setEnabled(True)
        ok, message = result if isinstance(result, tuple) else (False, str(result))
        if not ok:
            self.lbl_status.setText(i18n.t("Архив не собрался: {v0}", v0=message))
            return
        self.lbl_status.setText(i18n.t("Архив готов: {v0}", v0=message))
