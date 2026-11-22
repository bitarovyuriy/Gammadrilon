"""Event-driven Windows global shortcuts for the Tk main thread."""
from __future__ import annotations

import ctypes
import itertools
import sys
from ctypes import wintypes

SHORTCUTS = {'brightness': 'Ctrl+Alt+B', 'power': 'Ctrl+Alt+P'}
_VIRTUAL_KEYS = {'brightness': 0x42, 'power': 0x50}
_MODIFIERS = 0x0002 | 0x0001 | 0x4000  # CONTROL | ALT | NOREPEAT
WM_HOTKEY = 0x0312
_WM_NCDESTROY = 0x0082
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409

# Keep the native callback alive until Windows no longer holds its address.
_subclasses = {}
_ids = itertools.count(0x4000)

if sys.platform == 'win32':
    _user32 = ctypes.WinDLL('user32', use_last_error=True)
    _comctl32 = ctypes.WinDLL('comctl32', use_last_error=True)
    _SUBCLASSPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
        wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
    _user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.PostMessageW.restype = wintypes.BOOL
    _comctl32.SetWindowSubclass.argtypes = (wintypes.HWND, _SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t)
    _comctl32.SetWindowSubclass.restype = wintypes.BOOL
    _comctl32.RemoveWindowSubclass.argtypes = (wintypes.HWND, _SUBCLASSPROC, ctypes.c_size_t)
    _comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
    _comctl32.DefSubclassProc.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _comctl32.DefSubclassProc.restype = ctypes.c_ssize_t
else:
    _user32 = _comctl32 = None


class Hotkeys:
    """Register on the Tk window; start and close on its owning thread."""

    def __init__(self, window, callbacks):
        self.window = window
        self.callbacks = callbacks
        self._ids = {name: next(_ids) for name in SHORTCUTS}
        if max(self._ids.values()) > 0xBFFF:
            raise RuntimeError('Исчерпаны идентификаторы горячих клавиш')
        self._hwnd = None
        self._registered = {}
        self._pending = set()
        self._proc = None
        self._started = False
        self._errors = {}
        self._command = None
        self._tcl = None

    def start(self):
        if self._started:
            return dict(self._errors)
        if self._proc is not None:
            return {name: f'Не удалось повторно подключить {shortcut}: обработчик ещё активен'
                    for name, shortcut in SHORTCUTS.items()}
        self._started = True
        if _user32 is None:
            self._errors = {name: 'Глобальные клавиши доступны только в Windows' for name in SHORTCUTS}
            return dict(self._errors)
        try:
            version = self.window.tk.call('info', 'patchlevel').split('.')
            stem = f'tcl{version[0]}{version[1]}'
            try:
                self._tcl = ctypes.CDLL(stem + 't.dll')
            except OSError:
                self._tcl = ctypes.CDLL(stem + '.dll')
            self._tcl.Tcl_EvalEx.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_int)
            self._tcl.Tcl_EvalEx.restype = ctypes.c_int
            self._interp = self.window.tk.interpaddr()
            self._command = self.window.register(self._dispatch)
        except (OSError, AttributeError) as exc:
            self._errors = {name: f'Не удалось подключить обработчик клавиш: {exc}' for name in SHORTCUTS}
            return dict(self._errors)
        self._hwnd = self.window.winfo_id()
        self._proc = _SUBCLASSPROC(self._window_proc)
        if not _comctl32.SetWindowSubclass(self._hwnd, self._proc, id(self), 0):
            self._proc = None
            self._errors = {name: f'Не удалось подключить горячую клавишу {shortcut}'
                            for name, shortcut in SHORTCUTS.items()}
            return dict(self._errors)
        _subclasses[id(self)] = self
        for name, shortcut in SHORTCUTS.items():
            ident = self._ids[name]
            if _user32.RegisterHotKey(self._hwnd, ident, _MODIFIERS, _VIRTUAL_KEYS[name]):
                self._registered[ident] = name
            else:
                code = ctypes.get_last_error()
                if code == _ERROR_HOTKEY_ALREADY_REGISTERED:
                    self._errors[name] = f'{shortcut} уже занята другой программой'
                else:
                    self._errors[name] = f'Не удалось зарегистрировать {shortcut} (ошибка Windows {code})'
        return dict(self._errors)

    def _window_proc(self, hwnd, message, wparam, lparam, subclass_id, ref_data):
        # Keep the ctypes trampoline alive if close() removes it during dispatch.
        _current_proc = self._proc
        if message == WM_HOTKEY and wparam in self._registered:
            # Enter Tcl, not tkinter directly: Tk releases its Python thread state
            # while dispatching native messages. Tcl invokes our registered command
            # using tkinter's normal callback bridge, which restores that state.
            script = f'{self._command} {wparam}'.encode('ascii')
            self._tcl.Tcl_EvalEx(self._interp, script, len(script), 0)
            return 0
        if message == _WM_NCDESTROY and self._command:
            script = f'{self._command} destroy'.encode('ascii')
            self._tcl.Tcl_EvalEx(self._interp, script, len(script), 0)
        return _comctl32.DefSubclassProc(hwnd, message, wparam, lparam)

    def _dispatch(self, value):
        if value == 'destroy':
            self.close()
            return
        ident = int(value)
        name = self._registered.get(ident)
        if name is None:
            return
        token = None

        def invoke():
            self._pending.discard(token)
            if ident in self._registered:
                self.callbacks[name]()

        token = self.window.after_idle(invoke)
        self._pending.add(token)

    def close(self):
        for token in self._pending:
            try:
                self.window.after_cancel(token)
            except Exception:
                pass  # Tk may already be destroying the window.
        self._pending.clear()
        if self._hwnd is not None and _user32 is not None:
            for ident in self._registered:
                _user32.UnregisterHotKey(self._hwnd, ident)
        self._registered.clear()
        if self._proc is not None:
            # If removal fails, retain the callback: native code may still call it.
            if _comctl32.RemoveWindowSubclass(self._hwnd, self._proc, id(self)):
                _subclasses.pop(id(self), None)
                self._proc = None
        if self._proc is None and self._command is not None:
            try:
                self.window.deletecommand(self._command)
            except Exception:
                pass  # Tcl may already have deleted the command during teardown.
            self._command = None
        self._started = False
        self._errors = {}
