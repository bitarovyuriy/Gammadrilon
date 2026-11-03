# -*- coding: utf-8 -*-
"""Горячие клавиши: разбор сочетаний, регистрация и запись.

`user32` подменён заглушкой: тест не ставит настоящих глобальных сочетаний
на машине. Проверяем ровно то, что зависит от нашего кода: разбор строки,
передачу модификаторов и VK в RegisterHotKey, раздачу WM_HOTKEY по id.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_hotkeys  # noqa: E402
import main as gd_main  # noqa: E402


class FakeUser32:
    """Заглушка user32: запоминает вызовы, не трогает систему."""

    def __init__(self, result: int = 1) -> None:
        self.registered: list[tuple] = []
        self.unregistered: list[tuple] = []
        self.result = result

    def RegisterHotKey(self, hwnd, hotkey_id, modifiers, vk):  # noqa: N802
        self.registered.append((hwnd, hotkey_id, modifiers, vk))
        return self.result

    def UnregisterHotKey(self, hwnd, hotkey_id):  # noqa: N802
        self.unregistered.append((hwnd, hotkey_id))
        return 1


def test_parse_modifiers_keys_and_rejects_two_keys():
    parsed = gd_hotkeys.parse("ctrl+alt+z")
    assert parsed is not None
    mods, vk = parsed
    assert mods == gd_hotkeys.MOD_CONTROL | gd_hotkeys.MOD_ALT
    assert vk == ord("Z")

    assert gd_hotkeys.parse("page up") == (0, 0x21)
    assert gd_hotkeys.parse("scroll lock") == (0, 0x91)
    assert gd_hotkeys.parse("shift+f12") == (gd_hotkeys.MOD_SHIFT, 0x7B)
    assert gd_hotkeys.parse("windows+s") == (gd_hotkeys.MOD_WIN, ord("S"))


def test_parse_rejects_garbage_instead_of_silent_binding():
    for bad in ("", "   ", "ctrl", "ctrl+", "a+b", "ctrl+не_клавиша"):
        assert gd_hotkeys.parse(bad) is None, bad


def test_describe_roundtrip():
    assert gd_hotkeys.describe(gd_hotkeys.parse("ctrl+alt+z")) == "ctrl+alt+z"
    assert gd_hotkeys.describe(gd_hotkeys.parse("page up")) == "page up"


def test_registrar_sends_modifiers_and_dispatches(monkeypatch):
    fake = FakeUser32()
    monkeypatch.setattr(gd_hotkeys, "user32", fake)

    registrar = gd_hotkeys.Registrar()
    registrar.set_window(42)
    fired: list = []
    hotkey_id = registrar.register("ctrl+alt+z", lambda: fired.append(1))

    assert hotkey_id == 1
    hwnd, sent_id, modifiers, vk = fake.registered[0]
    assert (hwnd, sent_id) == (42, 1)
    assert modifiers == gd_hotkeys.MOD_CONTROL | gd_hotkeys.MOD_ALT | gd_hotkeys.MOD_NOREPEAT
    assert vk == ord("Z")

    assert registrar.dispatch(hotkey_id) is True
    assert fired == [1]
    assert registrar.dispatch(999) is False, "чужой id не должен ничего вызывать"

    registrar.clear()
    assert fake.unregistered == [(42, 1)]


def test_registrar_reports_busy_combination(monkeypatch):
    fake = FakeUser32(result=0)
    monkeypatch.setattr(gd_hotkeys, "user32", fake)
    monkeypatch.setattr(gd_hotkeys.ctypes, "get_last_error", lambda: 1409)

    registrar = gd_hotkeys.Registrar()
    registrar.set_window(1)

    assert registrar.register("ctrl+alt+z", lambda: None) is None
    assert "занято" in registrar.errors[0]


def test_registrar_refuses_without_window():
    registrar = gd_hotkeys.Registrar()

    assert registrar.register("ctrl+alt+z", lambda: None) is None
    assert registrar.errors, "отказ обязан быть объяснён"


def test_hotkey_defaults_cover_actions():
    """У каждой клавиши действий есть умолчание — иначе действие без клавиши."""
    hotkeys = gd_main.DEFAULT_CONFIG["settings"]["hotkeys"]

    assert hotkeys["reset"] == "ctrl+alt+z", "аварийный сброс гаммы остаётся на Z"
    assert hotkeys["reset_everything"] == "ctrl+alt+r", "стоп-кран — на R"
    assert hotkeys["reset"] != hotkeys["reset_everything"], "клавиши не должны совпадать"


def test_manager_registers_stop_crane_and_fires(monkeypatch):
    """Стоп-кран обязан попасть в регистрацию и вызвать своё действие."""
    fake = FakeUser32()
    monkeypatch.setattr(gd_hotkeys, "user32", fake)

    manager = gd_main.HotkeyManager()
    manager.set_window(7)
    fired: list = []
    manager.resetEverythingRequested.connect(lambda: fired.append(True))

    manager.register({"settings": {}, "presets": []})

    codes = {vk for _hwnd, _id, _mods, vk in fake.registered}
    assert ord("R") in codes and ord("Z") in codes

    hotkey_id = next(i for _hwnd, i, _mods, vk in fake.registered if vk == ord("R"))
    assert manager.dispatch(hotkey_id) is True
    assert fired == [True]


def test_config_overrides_stop_crane(monkeypatch):
    """Человек задал свою клавишу — работает она, а не умолчание."""
    fake = FakeUser32()
    monkeypatch.setattr(gd_hotkeys, "user32", fake)

    manager = gd_main.HotkeyManager()
    manager.set_window(7)
    manager.register({"settings": {"hotkeys": {"reset_everything": "ctrl+shift+f12"}},
                      "presets": []})

    codes = {vk for _hwnd, _id, _mods, vk in fake.registered}
    assert 0x7B in codes, "F12 должен быть зарегистрирован"
    assert ord("R") not in codes, "старое умолчание не должно остаться"


def test_stop_crane_strings_translated():
    """Строки стоп-крана есть на обоих языках: диалог, палитра, трей."""
    import gd_i18n as i18n

    assert "pal.stop_crane" in i18n.STRINGS["ru"] and "pal.stop_crane" in i18n.STRINGS["en"]
    assert "Вернуть всё как было (стоп-кран)" in i18n.DIRECT, "нет перевода подписи в диалоге"


def test_hotkey_recorder_builds_registerable_strings():
    """Запись клавиши даёт строку, которую понимает наш разбор."""
    from PyQt5.QtCore import Qt

    mods = Qt.ControlModifier | Qt.AltModifier
    cases = [
        (Qt.Key_Z, mods, "ctrl+alt+z"),
        (Qt.Key_1, mods, "ctrl+alt+1"),
        (Qt.Key_PageUp, Qt.NoModifier, "page up"),
        (Qt.Key_ScrollLock, Qt.NoModifier, "scroll lock"),
        (Qt.Key_F12, Qt.ShiftModifier, "shift+f12"),
        (Qt.Key_S, Qt.MetaModifier, "windows+s"),
        (Qt.Key_Exclam, Qt.ShiftModifier, "shift+1"),
    ]
    for key, modifiers, expected in cases:
        value = gd_main.hotkey_from_key(key, modifiers)
        assert value == expected, (key, value)
        assert gd_hotkeys.parse(value) is not None, f"строка {value!r} не разбирается"

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
