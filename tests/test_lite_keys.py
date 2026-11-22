"""Global hotkey tests use only isolated F23/F24 combinations."""
import ctypes
import threading
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import gd_lite_keys as keys


def test_shortcuts_are_exposed():
    assert keys.SHORTCUTS == {'brightness': 'Ctrl+Alt+B', 'power': 'Ctrl+Alt+P'}


def test_unrelated_messages_are_forwarded_and_hotkeys_dispatch_on_tk_thread(monkeypatch):
    window = tk.Tk()
    window.withdraw()
    main_thread = threading.get_ident()
    threads = []
    callbacks = {'brightness': Mock(side_effect=lambda: threads.append(threading.get_ident())),
                 'power': Mock(side_effect=lambda: threads.append(threading.get_ident()))}
    # F23/F24 with Shift avoid the application's real combinations.
    monkeypatch.setattr(keys, '_VIRTUAL_KEYS', {'brightness': 0x86, 'power': 0x87})
    monkeypatch.setattr(keys, '_MODIFIERS', keys._MODIFIERS | 0x0004)
    hotkeys = keys.Hotkeys(window, callbacks)
    try:
        errors = hotkeys.start()
        if errors:
            pytest.skip(f'Isolated test hotkey unavailable: {errors}')
        assert hotkeys.start() == {}
        user32 = keys._user32
        for name, ident in hotkeys._ids.items():
            assert user32.PostMessageW(hotkeys._hwnd, keys.WM_HOTKEY, ident, 0)
        window.update()
        callbacks['brightness'].assert_called_once_with()
        callbacks['power'].assert_called_once_with()
        assert threads == [main_thread, main_thread]
        window.title('messages still forwarded')
        assert window.title() == 'messages still forwarded'
        hotkeys.close()
        hotkeys.close()
        assert not user32.UnregisterHotKey(hotkeys._hwnd, hotkeys._ids['brightness'])
    finally:
        hotkeys.close()
        window.destroy()


def test_conflict_does_not_disable_other_shortcut_and_close_cancels_pending(monkeypatch):
    window = tk.Tk()
    window.withdraw()
    callbacks = {'brightness': Mock(), 'power': Mock()}
    unregister = Mock(return_value=True)

    def register(hwnd, ident, modifiers, vk):
        assert modifiers == 0x4003  # Ctrl+Alt+MOD_NOREPEAT
        if vk == ord('B'):
            ctypes.set_last_error(1409)
            return False
        return True

    user32 = SimpleNamespace(RegisterHotKey=Mock(side_effect=register), UnregisterHotKey=unregister)
    monkeypatch.setattr(keys, '_user32', user32)
    hotkeys = keys.Hotkeys(window, callbacks)
    try:
        errors = hotkeys.start()
        assert list(errors) == ['brightness']
        assert 'Ctrl+Alt+B' in errors['brightness'] and 'занята' in errors['brightness']
        hotkeys._dispatch(str(hotkeys._ids['power']))
        window.update()
        callbacks['power'].assert_called_once_with()
        callbacks['brightness'].assert_not_called()
        hotkeys._dispatch(str(hotkeys._ids['power']))
        hotkeys.close()
        window.update()
        callbacks['power'].assert_called_once_with()
        unregister.assert_called_once_with(hotkeys._hwnd, hotkeys._ids['power'])
    finally:
        hotkeys.close()
        window.destroy()


def test_destroy_window_unregisters_owned_keys_and_releases_callback(monkeypatch):
    window = tk.Tk()
    window.withdraw()
    unregister = Mock(return_value=True)
    monkeypatch.setattr(keys, '_user32', SimpleNamespace(
        RegisterHotKey=Mock(return_value=True), UnregisterHotKey=unregister))
    hotkeys = keys.Hotkeys(window, {'brightness': Mock(), 'power': Mock()})
    assert hotkeys.start() == {}
    assert id(hotkeys) in keys._subclasses
    window.destroy()
    assert unregister.call_count == 2
    assert id(hotkeys) not in keys._subclasses
    assert not hotkeys._registered
