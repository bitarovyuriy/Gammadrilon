# -*- coding: utf-8 -*-
"""Настройка горячих клавиш: назначить, сменить и убрать сочетание.

Отдельный экран, потому что до него сочетания жили только в `presets.json`:
человек видел подсказку в сайдбаре, но не мог ни поменять клавишу, ни
отвязать её совсем. Кнопка «Убрать» пишет пустую строку — `HotkeyManager`
такое сочетание просто не регистрирует, и клавиша остаётся системе.

Модуль без системных вызовов: разбор строки живёт в `gd_hotkeys`, а панель
только собирает значения и отдаёт их хосту.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import gd_hotkeys  # noqa: E402
import gd_i18n as i18n  # noqa: E402
import gd_theme as th  # noqa: E402
from gd_power_ui import make_card  # noqa: E402

#: действие → подпись. Порядок — как в сайдбаре и настройках, чтобы человек
#: искал знакомое место, а не алфавит.
ACTIONS = (
    ("next_preset", "Следующий пресет"),
    ("prev_preset", "Предыдущий пресет"),
    ("reset", "Сбросить гамму"),
    ("cat_vision", "Кошачье зрение"),
    ("palette", "Палитра команд"),
    ("perf_toggle", "Переключатель производительности"),
    ("reset_everything", "Вернуть всё как было"),
)

#: значения по умолчанию — те же, что в DEFAULT_CONFIG; кнопка «Сбросить»
#: возвращает именно их, а не пустоту: пустое сочетание = «не назначено».
DEFAULTS = {
    "next_preset": "page up",
    "prev_preset": "page down",
    "reset": "ctrl+alt+z",
    "cat_vision": "scroll lock",
    "palette": "ctrl+alt+g",
    "perf_toggle": "ctrl+alt+p",
    "reset_everything": "ctrl+alt+r",
}


class HotkeysPanel(QWidget):
    """Таблица «действие → сочетание» с кнопками «Убрать» и «Сохранить»."""

    def __init__(self, config=None, save_fn=None, apply_fn=None, parent=None):
        super().__init__(parent)
        self._config = config if isinstance(config, dict) else {}
        self._save_fn = save_fn
        self._apply_fn = apply_fn
        self._fields: dict[str, QLineEdit] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(th.PAGE_SPACING)

        card, lay = make_card(
            i18n.t("ГОРЯЧИЕ КЛАВИШИ"),
            tip=i18n.t(
                "Сочетание работает в любом приложении: Windows сама присылает "
                "нажатие окну Gammadrilon. Пустое поле — клавиша не занята, "
                "и система получает её как обычно. Занятое другой программой "
                "сочетание не зарегистрируется — в статусе будет причина."
            ),
        )

        self.table = QTableWidget(len(ACTIONS), 3)
        self.table.setHorizontalHeaderLabels(
            [i18n.t("Действие"), i18n.t("Сочетание"), ""]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        # семь действий должны быть видны целиком: прокрутка внутри таблицы
        # прячет последние строки и кнопки «Убрать»
        self.table.verticalHeader().setDefaultSectionSize(30)
        # минимум, а не максимум: раскладка сжимала таблицу до пяти строк
        row_h = 7 * 30 + 32
        self.table.setMinimumHeight(row_h)
        self.table.setMaximumHeight(row_h)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 200)
        self.table.setFrameShape(self.table.NoFrame)

        hotkeys = self._settings().get("hotkeys", {})
        for row, (key, label) in enumerate(ACTIONS):
            self.table.setItem(row, 0, QTableWidgetItem(i18n.t(label)))

            edit = QLineEdit()
            edit.setPlaceholderText(i18n.t("не назначено"))
            edit.setText(str(hotkeys.get(key, DEFAULTS.get(key, "")) or ""))
            edit.editingFinished.connect(self._mark_changed)
            # Ctrl+клик по полю не должен открывать контекстное меню Windows —
            # сочетания почти всегда начинаются с Ctrl
            edit.setContextMenuPolicy(Qt.NoContextMenu)
            self._fields[key] = edit
            self.table.setCellWidget(row, 1, edit)

            clear = QPushButton(i18n.t("Убрать"))
            clear.setObjectName("Segment")
            clear.setToolTip(i18n.t("Освободить сочетание: клавиша вернётся системе."))
            clear.clicked.connect(lambda _=False, k=key: self._clear(k))
            self.table.setCellWidget(row, 2, clear)

        lay.addWidget(self.table)

        row = QHBoxLayout()
        self.btn_save = QPushButton(i18n.t("Сохранить и применить"))
        self.btn_save.setObjectName("PrimaryButton")
        self.btn_save.clicked.connect(self._save)
        self.btn_defaults = QPushButton(i18n.t("Вернуть умолчания"))
        self.btn_defaults.setObjectName("Segment")
        self.btn_defaults.clicked.connect(self._restore_defaults)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_defaults)
        row.addStretch(1)
        lay.addLayout(row)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("Hint")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        root.addWidget(card)
        root.addStretch(1)

    # -- данные --------------------------------------------------------------
    def _settings(self) -> dict:
        settings = self._config.get("settings")
        if not isinstance(settings, dict):
            settings = {}
            self._config["settings"] = settings
        return settings

    def values(self) -> dict[str, str]:
        return {key: edit.text().strip() for key, edit in self._fields.items()}

    def _mark_changed(self):
        self._status(i18n.t("Есть несохранённые изменения."), ok=True)

    def _status(self, text: str, ok: bool = True):
        self.lbl_status.setStyleSheet(f"color: {th.MUTED if ok else th.WARN};")
        self.lbl_status.setText(text)

    # -- действия ------------------------------------------------------------
    def _clear(self, key: str):
        self._fields[key].clear()
        self._status(i18n.t("Сочетание убрано — не забудь сохранить."), ok=True)

    def _restore_defaults(self):
        for key, value in DEFAULTS.items():
            if key in self._fields:
                self._fields[key].setText(value)
        self._status(i18n.t("Вернул умолчания — нажми «Сохранить и применить»."))

    def _validate(self) -> tuple[bool, str]:
        """Проверить каждое непустое сочетание до записи в конфиг."""
        seen: dict[str, str] = {}
        for key, label in ACTIONS:
            spec = self._fields[key].text().strip().lower()
            if not spec:
                continue          # пусто — законное «не назначено»
            if gd_hotkeys.parse(spec) is None:
                return False, i18n.t(
                    "«{v0}»: не понимаю сочетание «{v1}». Примеры: ctrl+alt+k, page up, scroll lock.",
                    v0=i18n.t(label), v1=spec,
                )
            if spec in seen:
                return False, i18n.t(
                    "«{v0}» уже занято действием «{v1}».",
                    v0=spec, v1=i18n.t(seen[spec]),
                )
            seen[spec] = label
        return True, ""

    def _save(self):
        ok, reason = self._validate()
        if not ok:
            self._status(reason, ok=False)
            return

        values = {key: self._fields[key].text().strip() for key, _ in ACTIONS}
        self._settings()["hotkeys"] = values

        if callable(self._save_fn):
            try:
                saved = self._save_fn()
            except Exception as exc:  # noqa: BLE001
                self._status(i18n.t("Не удалось сохранить настройки: {v0}", v0=exc), ok=False)
                return
            if saved is False:
                # MainWindow.save_settings() возвращает False, когда файл не записался
                self._status(
                    i18n.t("Настройки не сохранены: файл настроек недоступен для записи. "
                           "После перезапуска сочетания вернутся к прежним."),
                    ok=False)
                return

        applied = True
        if callable(self._apply_fn):
            try:
                self._apply_fn()
            except Exception as exc:  # noqa: BLE001
                applied = False
                self._status(i18n.t("Сохранено, но применить не удалось: {v0}", v0=exc), ok=False)

        if applied:
            empty = [self._fields[key].text().strip() for key, _ in ACTIONS].count("")
            if empty:
                self._status(
                    i18n.t("Сохранено. Свободных сочетаний: {v0} из {v1}.",
                           v0=empty, v1=len(ACTIONS))
                )
            else:
                self._status(i18n.t("Сохранено и применено."))
