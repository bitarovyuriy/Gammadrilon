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
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
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
    def __init__(self, parent=None, startup_on: bool = False, startup_fn=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("Gammadrilon — первый запуск"))
        self.setMinimumWidth(460)
        self.startup_fn = startup_fn

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

        # тема и автозапуск: два решения, которые иначе ищут в настройках
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel(i18n.t("Тема:")))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(i18n.t("Тёмная"), "dark")
        self.theme_combo.addItem(i18n.t("Светлая"), "light")
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch(1)
        root.addLayout(theme_row)

        self.chk_startup = QCheckBox(i18n.t("Запускать вместе с Windows"))
        self.chk_startup.setChecked(bool(startup_on))
        root.addWidget(self.chk_startup)

        # Проверка окружения прямо здесь: на чужом ПК это первый вопрос —
        # «почему у меня не так», и ответ лучше показать фактами
        diag_row = QHBoxLayout()
        self.btn_diag = QPushButton(i18n.t("Проверить окружение"))
        self.btn_diag.clicked.connect(self._run_diag)
        diag_row.addWidget(self.btn_diag)
        diag_row.addStretch(1)
        root.addLayout(diag_row)

        self.diag = QPlainTextEdit()
        self.diag.setReadOnly(True)
        self.diag.setVisible(False)
        self.diag.setMinimumHeight(170)
        root.addWidget(self.diag)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton(i18n.t("Понятно"))
        ok.setObjectName("PrimaryButton")
        ok.setMinimumWidth(140)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        root.addLayout(row)

    def _run_diag(self):
        """Диагностика в фоне: netsh, sc query и GPU не должны морозить диалог."""
        import gd_bg
        import gd_diagnose

        self.btn_diag.setEnabled(False)
        self.btn_diag.setText(i18n.t("Проверяю…"))
        gd_bg.submit(gd_diagnose.report_text, self._diag_done, parent=self)

    def _diag_done(self, text):
        self.btn_diag.setEnabled(True)
        self.btn_diag.setText(i18n.t("Проверить окружение"))
        self.diag.setVisible(True)
        self.diag.setPlainText(text if isinstance(text, str) else str(text))


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


def run(config: dict, save_fn=None, parent=None, startup_fn=None) -> bool:
    """Показать экран, если нужно. Возвращает True, если показывался."""
    if not should_show(config):
        return False
    settings = config.setdefault("settings", {})
    dlg = FirstRunDialog(parent, bool(settings.get("start_with_windows")), startup_fn)
    dlg.exec_()

    # выбор темы и автозапуска применяем сразу: иначе «первый запуск» —
    # просто картинка, а человек ищет те же галочки в настройках
    settings["theme"] = dlg.theme_combo.currentData()
    want_startup = dlg.chk_startup.isChecked()
    if startup_fn is not None and want_startup != bool(settings.get("start_with_windows")):
        startup_fn(want_startup)
    else:
        settings["start_with_windows"] = want_startup

    if dlg.chk.isChecked():
        mark_done(config, save_fn)
    return True
