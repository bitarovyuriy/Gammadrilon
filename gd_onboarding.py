# -*- coding: utf-8 -*-
"""Экран первого запуска (ТЗ 5): что утилита может менять и что будет без прав.

Показывается один раз; флаг хранится в presets.json (settings.first_run_done).
"""
from __future__ import annotations

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

from PyQt5.QtWidgets import (  # noqa: E402
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_theme as th  # noqa: E402
import gd_i18n as i18n  # noqa: E402

INTRO = (
    i18n.t("Gammadrilon меняет настройки системы — всё обратимо и записывается в журнал:\n\n• Питание CPU — схема и лимиты процессора (откат в один клик).\n• Питание GPU — лимит мощности и клоки через nvidia-smi (откат автоматический, если окно закрылось нештатно).\n• DNS — смена резолверов с сохранением прежней конфигурации.\n• ОЗУ — очистка standby list и сжатие рабочих наборов (кэш файлов наполнится заново).\n\nЧасть операций (лимит GPU, DNS, очистка ОЗУ) требует прав администратора. Без них приложение честно скажет «нужны права» и НЕ сделает вид, что сработало: чтение и замеры работают всегда, изменения — только с разрешением.")
)


class FirstRunDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("Gammadrilon — первый запуск"))
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.DIALOG_MARGINS)
        root.setSpacing(th.DIALOG_SPACING)

        title = QLabel(i18n.t("Что умеет Gammadrilon"))
        title.setObjectName("DialogTitle")
        root.addWidget(title)

        body = QLabel(INTRO)
        body.setWordWrap(True)
        body.setObjectName("Hint")
        root.addWidget(body)

        self.chk = QCheckBox(i18n.t("Больше не показывать"))
        self.chk.setChecked(True)
        root.addWidget(self.chk)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton(i18n.t("Понятно"))
        ok.setObjectName("PrimaryButton")
        ok.setMinimumWidth(140)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        root.addLayout(row)


def should_show(config: dict) -> bool:
    return not bool(config.get("settings", {}).get("first_run_done", False))


def mark_done(config: dict, save_fn=None) -> dict:
    config.setdefault("settings", {})["first_run_done"] = True
    if save_fn is not None:
        try:
            save_fn(config)
        except Exception:  # noqa: BLE001
            pass
    return config


def run(config: dict, save_fn=None, parent=None) -> bool:
    """Показать экран, если нужно. Возвращает True, если показывался."""
    if not should_show(config):
        return False
    dlg = FirstRunDialog(parent)
    dlg.exec_()
    if dlg.chk.isChecked():
        mark_done(config, save_fn)
    return True
