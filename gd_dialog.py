# -*- coding: utf-8 -*-
"""Диалоги «спросить» и «сообщить» в оформлении «Пульта» (ТЗ 5, 7).

Раньше эти роли играл QMessageBox: он рисовал системную иконку (в тёмной теме
это единственный цветной пиксель во всём окне), ставил свои отступы и заголовок
кнопок «Yes/No» мимо словаря. Здесь те же две роли — своим окном: заголовок,
текст, ряд кнопок. Опасное действие помечается пунктиром (условность проекта),
а не цветом; отмена — тихая кнопка, подтверждение — главная.

    if confirm(self, i18n.t("Сбросить все изменения"), i18n.t("...?"),
               confirm_text=i18n.t("Сбросить всё"), destructive=True):
        ...

    notify(self, i18n.t("Невозможно удалить"), i18n.t("Нельзя удалить последний пресет."))
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

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_i18n as i18n  # noqa: E402
import gd_theme as th  # noqa: E402

TEXT_WIDTH = 420   # предел строки текста: длинный абзац не растягивает окно


class _Dialog(QDialog):
    """Общая основа: заголовок, текст, ряд кнопок справа."""

    def __init__(self, text: str, title: str = "", parent=None):
        super().__init__(parent)
        # системный «?» в заголовке окна — лишняя кнопка, которую мы не обслуживаем
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(title or i18n.t("tray.title"))
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(th.SPACE_XL, th.SPACE_L, th.SPACE_XL, th.SPACE_L)
        root.setSpacing(th.SPACE_M)

        if title:
            head = QLabel(title)
            head.setObjectName("DialogTitle")
            head.setWordWrap(True)
            root.addWidget(head)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setMaximumWidth(TEXT_WIDTH)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(body)

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(th.SPACE_S)
        self.buttons.addStretch(1)
        root.addLayout(self.buttons)

    def add_button(self, text: str, *, primary: bool = False, danger: bool = False,
                   slot=None) -> QPushButton:
        btn = QPushButton(text)
        if danger:
            btn.setObjectName("DangerButton")
        elif primary:
            btn.setObjectName("PrimaryButton")
        else:
            btn.setObjectName("Segment")
        btn.clicked.connect(slot or self.reject)
        self.buttons.addWidget(btn)
        return btn


class ConfirmDialog(_Dialog):
    """Вопрос с двумя ответами. ``exec_()`` → Accepted, если подтверждено."""

    def __init__(self, text: str, title: str = "", parent=None, *,
                 confirm_text: str = "", cancel_text: str = "", destructive: bool = False):
        super().__init__(text, title, parent)
        cancel = self.add_button(cancel_text or i18n.t("common.cancel"), slot=self.reject)
        ok_text = confirm_text or i18n.t("common.ok")
        ok = self.add_button(ok_text, primary=not destructive, danger=destructive,
                             slot=self.accept)
        # по умолчанию — «отмена»: Enter не должен подтверждать опасное действие
        default = cancel if destructive else ok
        default.setAutoDefault(True)
        default.setDefault(True)
        default.setFocus()
        self.cancel_button = cancel
        self.confirm_button = ok


class MessageDialog(_Dialog):
    """Сообщение с одним ответом (бывший QMessageBox.warning/information)."""

    def __init__(self, text: str, title: str = "", parent=None, *, ok_text: str = ""):
        super().__init__(text, title, parent)
        ok = self.add_button(ok_text or i18n.t("common.ok"), primary=True,
                             slot=self.accept)
        ok.setAutoDefault(True)
        ok.setDefault(True)
        ok.setFocus()
        self.ok_button = ok


def confirm(parent, title: str, text: str, *, confirm_text: str = "",
            cancel_text: str = "", destructive: bool = False) -> bool:
    """Спросить подтверждение. True — человек согласился."""
    dlg = ConfirmDialog(text, title, parent, confirm_text=confirm_text,
                        cancel_text=cancel_text, destructive=destructive)
    return dlg.exec_() == QDialog.Accepted


def notify(parent, title: str, text: str, *, ok_text: str = "") -> None:
    """Показать сообщение: один ответ, никаких иконок."""
    MessageDialog(text, title, parent, ok_text=ok_text).exec_()
