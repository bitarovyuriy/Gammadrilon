# -*- coding: utf-8 -*-
"""Глобальные горячие клавиши через `RegisterHotKey`.

Почему не библиотека `keyboard`: она ставит `WH_KEYBOARD_LL` — низкоуровневый
хук на всю клавиатуру. Антивирусы считают такие хуки подозрительными, а при
нагрузке они задерживают ввод. `RegisterHotKey` — штатный механизм Windows:
система сама присылает `WM_HOTKEY` только на зарегистрированное сочетание,
чужой ввод приложение не видит.

Модуль без Qt: разбор строки и регистрацию можно проверить в тестах, а окно
передаёт только свой HWND и вызывает :meth:`Registrar.dispatch` из nativeEvent.
"""
from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes as wt

import gd_i18n as i18n

user32 = ctypes.WinDLL("user32", use_last_error=True)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

#: имена модификаторов, как их пишет рекордер и старые настройки
MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "meta": MOD_WIN,
}

#: имена клавиш → виртуальные коды (VK_*)
KEYS = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "page up": 0x21, "pageup": 0x21, "pgup": 0x21,
    "page down": 0x22, "pagedown": 0x22, "pgdn": 0x22,
    "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "print screen": 0x2C, "printscreen": 0x2C,
    "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "pause": 0x13, "caps lock": 0x14, "num lock": 0x90, "scroll lock": 0x91,
    "menu": 0x5D, "help": 0x2F,
    ",": 0xBC, ".": 0xBE, ";": 0xBA, "/": 0xBF, "\\": 0xDC,
    "-": 0xBD, "=": 0xBB, "`": 0xC0, "'": 0xDE, "[": 0xDB, "]": 0xDD,
}


def parse(spec: str) -> tuple[int, int] | None:
    """Строка вида ``ctrl+alt+z`` → (модификаторы, VK). None — не разобрали.

    Строгий разбор: две клавиши в одном сочетании (``a+b``) — не горячая
    клавиша, а опечатка; честный отказ лучше молча неработающего биндинга.
    """
    parts = [part.strip().lower() for part in str(spec or "").split("+") if part.strip()]
    if not parts:
        return None

    modifiers = 0
    key: int | None = None
    for part in parts:
        if part in MODIFIERS:
            modifiers |= MODIFIERS[part]
            continue
        if key is not None:
            return None
        key = _key_code(part)
        if key is None:
            return None
    if key is None:
        return None
    return modifiers, key


def _key_code(name: str) -> int | None:
    if name in KEYS:
        return KEYS[name]
    if name.startswith("f") and name[1:].isdigit():
        number = int(name[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1
    if len(name) == 1:
        char = name.upper()
        if "A" <= char <= "Z" or "0" <= char <= "9":
            return ord(char)
    return None


def describe(parsed: tuple[int, int] | None) -> str:
    """Обратное преобразование — для сообщений и проверки настроек."""
    if parsed is None:
        return ""
    modifiers, vk = parsed
    names = [name for name, flag in (("ctrl", MOD_CONTROL), ("alt", MOD_ALT),
                                     ("shift", MOD_SHIFT), ("win", MOD_WIN))
             if modifiers & flag]
    for name, code in KEYS.items():
        if code == vk:
            names.append(name)
            return "+".join(names)
    if 0x70 <= vk <= 0x87:
        names.append(f"f{vk - 0x70 + 1}")
    elif 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        names.append(chr(vk).lower())
    return "+".join(names)


user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.RegisterHotKey.restype = wt.BOOL
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wt.BOOL


class Registrar:
    """Регистрация сочетаний на конкретное окно и раздача `WM_HOTKEY`.

    Идентификатор сочетания — наш собственный счётчик: `WM_HOTKEY` приходит с
    ним в ``wParam``, поэтому окно не разбирает клавиши повторно.
    """

    def __init__(self) -> None:
        self.hwnd: int | None = None
        self.errors: list[str] = []
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._next_id = 1

    def set_window(self, hwnd: int) -> None:
        self.hwnd = int(hwnd) or None

    def clear(self) -> None:
        if self.hwnd:
            for hotkey_id in list(self._callbacks):
                try:
                    user32.UnregisterHotKey(self.hwnd, hotkey_id)
                except Exception:  # noqa: BLE001 — окно могло уже закрыться
                    pass
        self._callbacks.clear()
        self._next_id = 1

    def register(self, spec: str, callback: Callable[[], None]) -> int | None:
        """Зарегистрировать сочетание. None — отказ, причина в :attr:`errors`."""
        parsed = parse(spec)
        if parsed is None:
            self.errors.append(i18n.t("не понимаю сочетание «{v0}»", v0=spec))
            return None
        if not self.hwnd:
            self.errors.append(i18n.t("окно ещё не создано — сочетание не зарегистрировано"))
            return None
        modifiers, vk = parsed
        hotkey_id = self._next_id
        self._next_id += 1
        ctypes.set_last_error(0)
        ok = user32.RegisterHotKey(self.hwnd, hotkey_id, modifiers | MOD_NOREPEAT, vk)
        if not ok:
            code = ctypes.get_last_error()
            if code == ERROR_HOTKEY_ALREADY_REGISTERED:
                self.errors.append(i18n.t("сочетание «{v0}» занято другой программой", v0=spec))
            else:
                self.errors.append(
                    i18n.t("не удалось зарегистрировать «{v0}» (код {v1})", v0=spec, v1=code))
            return None
        self._callbacks[hotkey_id] = callback
        return hotkey_id

    def dispatch(self, hotkey_id: int) -> bool:
        """Вызвать обработчик по идентификатору из `WM_HOTKEY`."""
        callback = self._callbacks.get(int(hotkey_id))
        if callback is None:
            return False
        try:
            callback()
        except Exception:  # noqa: BLE001 — исключение в обработчике не должно убивать окно
            return False
        return True
