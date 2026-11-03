# -*- coding: utf-8 -*-
"""Смена активного окна: путь процесса, активное окно, постановка хука.

Сам колбэк проверяется живьём (блокнот выходит вперёд), здесь — то, что можно
проверить без переключения окон: корректные типы, путь своего процесса,
постановка и снятие хука, устойчивость к падению колбэка.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_winwatch as ww  # noqa: E402


def test_process_path_points_to_exe():
    path = ww.process_path(os.getpid())

    assert path.lower().endswith(".exe"), path
    assert os.path.isfile(path), path


def test_process_path_of_dead_pid_is_empty():
    assert ww.process_path(0) == ""
    assert ww.process_path(999_999_999) == ""


def test_foreground_returns_ints():
    hwnd, pid = ww.foreground()

    assert isinstance(hwnd, int) and isinstance(pid, int)
    assert hwnd >= 0 and pid >= 0


def test_window_pid_of_zero_is_zero():
    assert ww.window_pid(0) == 0


def test_watcher_starts_and_stops():
    watcher = ww.ForegroundWatcher(lambda _hwnd, _pid: None)

    assert watcher.start() is True
    assert watcher.active is True
    assert watcher.start() is True, "повторный start не должен ставить второй хук"

    watcher.stop()
    assert watcher.active is False
    watcher.stop()  # повторный stop безопасен


def test_watcher_ignores_repeat_and_swallows_errors():
    """Колбэк не должен падать наружу: исключение в WinAPI-колбэке — это крах."""
    calls = []

    def boom(hwnd, pid):
        calls.append(hwnd)
        raise RuntimeError("нарочно")

    watcher = ww.ForegroundWatcher(boom)
    assert watcher.start() is True
    watcher._last_hwnd = 0
    # зовём внутренний колбэк напрямую, как это сделал бы WinAPI
    callback = watcher._proc
    assert callback is not None
    callback(None, ww.EVENT_SYSTEM_FOREGROUND, 12345, 0, 0, 0, 0)
    assert calls == [12345]

    callback(None, ww.EVENT_SYSTEM_FOREGROUND, 12345, 0, 0, 0, 0)
    assert calls == [12345], "то же окно дважды не обрабатывается"

    watcher.stop()


def test_main_watches_foreground_for_gamma():
    """Сторож гаммы обязан реагировать на смену окна, а не только на таймер."""
    import main as gd_main

    assert hasattr(gd_main.MainWindow, "_start_foreground_watch")
    assert hasattr(gd_main.MainWindow, "_on_foreground_changed")
    assert gd_main.GAMMA_EVENT_DELAY_MS < gd_main.GAMMA_WATCH_INTERVAL_MS, (
        "событие должно срабатывать раньше очередного тика таймера")
