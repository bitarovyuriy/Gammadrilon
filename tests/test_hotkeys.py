# -*- coding: utf-8 -*-
"""Горячие клавиши: умолчания, стоп-кран и перекрытие настройками.

`keyboard` подменён заглушкой: тест не ставит глобальных хуков на машине.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as gd_main  # noqa: E402 — путь проекта добавлен выше


def _fake_keyboard(calls: list):
    return types.SimpleNamespace(
        add_hotkey=lambda combo, callback, suppress=False: calls.append((combo, callback)) or object(),
        remove_hotkey=lambda handle: None,
    )


def test_hotkey_defaults_cover_actions():
    """У каждой клавиши действий есть умолчание — иначе действие без клавиши."""
    hotkeys = gd_main.DEFAULT_CONFIG["settings"]["hotkeys"]

    assert hotkeys["reset"] == "ctrl+alt+z", "аварийный сброс гаммы остаётся на Z"
    assert hotkeys["reset_everything"] == "ctrl+alt+r", "стоп-кран — на R"
    assert hotkeys["reset"] != hotkeys["reset_everything"], "клавиши не должны совпадать"


def test_manager_registers_stop_crane_and_fires(monkeypatch):
    """Стоп-кран обязан попасть в глобальные хуки и вызвать своё действие."""
    calls: list = []
    monkeypatch.setitem(sys.modules, "keyboard", _fake_keyboard(calls))

    manager = gd_main.HotkeyManager()
    fired: list = []
    manager.resetEverythingRequested.connect(lambda: fired.append(True))

    manager.register({"settings": {}, "presets": []})   # пустой конфиг → умолчания

    combos = [combo for combo, _cb in calls]
    assert "ctrl+alt+r" in combos and "ctrl+alt+z" in combos, combos
    dict(calls)["ctrl+alt+r"]()
    assert fired == [True]


def test_config_overrides_stop_crane(monkeypatch):
    """Человек задал свою клавишу — работает она, а не умолчание."""
    calls: list = []
    monkeypatch.setitem(sys.modules, "keyboard", _fake_keyboard(calls))

    manager = gd_main.HotkeyManager()
    manager.register({"settings": {"hotkeys": {"reset_everything": "ctrl+shift+f12"}},
                      "presets": []})

    combos = [combo for combo, _cb in calls]
    assert "ctrl+shift+f12" in combos
    assert "ctrl+alt+r" not in combos


def test_stop_crane_strings_translated():
    """Строки стоп-крана есть на обоих языках: диалог, палитра, трей."""
    import gd_i18n as i18n

    assert "pal.stop_crane" in i18n.STRINGS["ru"] and "pal.stop_crane" in i18n.STRINGS["en"]
    assert "Вернуть всё как было (стоп-кран)" in i18n.DIRECT, "нет перевода подписи в диалоге"


def test_hotkey_recorder_builds_keyboard_strings():
    """Запись клавиши даёт ровно ту строку, которую понимает keyboard."""
    from PyQt5.QtCore import Qt

    mods = Qt.ControlModifier | Qt.AltModifier
    assert gd_main.hotkey_from_key(Qt.Key_Z, mods) == "ctrl+alt+z"
    assert gd_main.hotkey_from_key(Qt.Key_1, mods) == "ctrl+alt+1"
    assert gd_main.hotkey_from_key(Qt.Key_PageUp, Qt.NoModifier) == "page up"
    assert gd_main.hotkey_from_key(Qt.Key_ScrollLock, Qt.NoModifier) == "scroll lock"
    assert gd_main.hotkey_from_key(Qt.Key_F12, Qt.ShiftModifier) == "shift+f12"
    assert gd_main.hotkey_from_key(Qt.Key_S, Qt.MetaModifier) == "windows+s"
    # Shift меняет Qt-код на символ: shift+1 не должен стать «shift+!»
    assert gd_main.hotkey_from_key(Qt.Key_Exclam, Qt.ShiftModifier) == "shift+1"
    # один модификатор — это ещё не сочетание
    assert gd_main.hotkey_from_key(Qt.Key_Control, Qt.ControlModifier) == ""


def test_hotkey_edit_records_and_clears():
    """Поле само ловит нажатие: вручную название клавиши не пишется."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    edit = gd_main.HotkeyEdit("ctrl+alt+z")
    edit.show()
    edit.setFocus()
    app.processEvents()

    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier | Qt.AltModifier)
    assert edit.text() == "ctrl+alt+a"

    edit.setFocus()
    app.processEvents()
    QTest.keyClick(edit, Qt.Key_Backspace, Qt.NoModifier)
    assert edit.text() == ""

    edit.setFocus()
    app.processEvents()
    QTest.keyClick(edit, Qt.Key_F5, Qt.NoModifier)
    assert edit.text() == "f5"
