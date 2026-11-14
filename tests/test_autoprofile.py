# -*- coding: utf-8 -*-
"""Автопрофили по игре: вход в окно игры и возврат пресета на выходе.

Окно Qt не создаём: проверяем чистую логику — какой пресет выбран и что
происходит, когда игра теряет фокус. Применение пресета подменено списком.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_winwatch  # noqa: E402
import main as gd_main  # noqa: E402


class _Widget:
    """Заглушка Qt-виджета: apply_preset_by_index дёргает только эти методы."""

    def blockSignals(self, *_args):  # noqa: N802
        return None

    def setCurrentIndex(self, *_args):  # noqa: N802
        return None

    def setValue(self, *_args):  # noqa: N802
        return None

    def setEnabled(self, *_args):  # noqa: N802
        return None


class FakeWindow:
    """Ровно те поля, которыми пользуется _auto_profile_tick."""

    def __init__(self, rules: list[dict], presets: list[dict]) -> None:
        self.config = {"settings": {"auto_profiles": rules, "auto_profiles_on": True},
                       "presets": presets}
        self.current_preset_index = 0
        self._auto_prev_index = None
        self._auto_prev_hz = None
        self._hz_busy = False
        self._auto_hz_pending = False
        self.is_quitting = False
        self._fg_pid = 0
        self.applied: list[tuple[int, bool]] = []
        self.preset_combo = _Widget()
        self.shadow_slider = _Widget()
        self.hz_apply_button = _Widget()
        self.status: list[str] = []

    def _cancel_cat_vision(self, *_args, **_kwargs):
        return None

    def animate_to(self, *_args, **_kwargs):
        return None

    def set_status(self, text: str, ok: bool = True):
        self.status.append(text)

    def apply_preset_by_index(self, index: int, auto: bool = False):
        # повторяем поведение настоящего метода: ручной выбор отменяет авто-возврат
        if not auto:
            self._auto_prev_index = None
        self.current_preset_index = index
        self.applied.append((index, auto))

    def _auto_profiles(self) -> list:
        return gd_main.MainWindow._auto_profiles(self)

    tick = _auto_profile_tick = gd_main.MainWindow._auto_profile_tick
    _start_hz_operation = gd_main.MainWindow._start_hz_operation
    _finish_hz_operation = gd_main.MainWindow._finish_hz_operation
    _auto_hz_applied = gd_main.MainWindow._auto_hz_applied
    _auto_hz_restored = gd_main.MainWindow._auto_hz_restored
    _rollback_done = gd_main.MainWindow._rollback_done
    _restore_before_shutdown = gd_main.MainWindow._restore_before_shutdown
    _restore_saved_hz = staticmethod(gd_main.MainWindow._restore_saved_hz)


def with_focus(monkeypatch, window: FakeWindow, pid: int, exe: str):
    monkeypatch.setattr(gd_winwatch, "process_path",
                        lambda value: f"C:\\Games\\{exe}" if value == pid else "")
    window._fg_pid = pid
    window.tick()


PRESETS = [{"name": "Работа"}, {"name": "Игра"}, {"name": "Кино"}]


def test_entering_game_applies_its_preset(monkeypatch):
    window = FakeWindow([{"exe": "cs2.exe", "preset": 1}], PRESETS)

    with_focus(monkeypatch, window, 500, "cs2.exe")

    assert window.applied == [(1, True)]
    assert window.current_preset_index == 1
    assert window._auto_prev_index == 0, "прежний пресет запоминается для возврата"


def test_leaving_game_restores_previous_preset(monkeypatch):
    window = FakeWindow([{"exe": "cs2.exe", "preset": 1}], PRESETS)
    with_focus(monkeypatch, window, 500, "cs2.exe")

    with_focus(monkeypatch, window, 900, "explorer.exe")

    assert window.applied == [(1, True), (0, True)]
    assert window.current_preset_index == 0
    assert window._auto_prev_index is None


def test_unknown_process_does_not_touch_anything(monkeypatch):
    window = FakeWindow([{"exe": "cs2.exe", "preset": 1}], PRESETS)

    with_focus(monkeypatch, window, 900, "notepad.exe")

    assert window.applied == []


def test_same_preset_is_not_reapplied(monkeypatch):
    window = FakeWindow([{"exe": "cs2.exe", "preset": 0}], PRESETS)

    with_focus(monkeypatch, window, 500, "cs2.exe")

    assert window.applied == [], "пресет уже стоит — дёргать анимацию незачем"


def test_disabled_switch_does_nothing(monkeypatch):
    window = FakeWindow([{"exe": "cs2.exe", "preset": 1}], PRESETS)
    window.config["settings"]["auto_profiles_on"] = False

    with_focus(monkeypatch, window, 500, "cs2.exe")

    assert window.applied == []


def test_manual_choice_cancels_auto_return():
    """Человек выбрал пресет сам — авто-возврат не должен его перебивать."""
    window = FakeWindow([{"exe": "cs2.exe", "preset": 1}], PRESETS)
    window._auto_prev_index = 2
    window._cancel_cat_vision = lambda *a, **k: None

    # настоящий метод, не заглушка: проверяем сам сброс
    gd_main.MainWindow.apply_preset_by_index(window, 1, auto=False)

    assert window._auto_prev_index is None


def test_broken_rule_points_are_ignored(monkeypatch):
    window = FakeWindow([{"exe": "cs2.exe", "preset": 99},
                         {"exe": "dota2.exe", "preset": "abc"}], PRESETS)

    with_focus(monkeypatch, window, 500, "cs2.exe")
    with_focus(monkeypatch, window, 700, "dota2.exe")

    assert window.applied == [], "индекс вне списка не должен ронять приложение"


def test_focus_change_during_hz_apply_waits_to_restore(monkeypatch, tmp_path):
    window = FakeWindow([{"exe": "cs2.exe", "preset": 1, "hz": 144}], PRESETS)
    rollback = gd_main.gd_rollback
    monkeypatch.setattr(rollback, "state_path", lambda: tmp_path / "rollback_state.json")
    monkeypatch.setattr(gd_main.gd_hz, "refresh_rate", lambda: 60)
    work = []
    changes = []

    def submit(fn, done, *, parent):
        work.append((fn, done))

    def set_refresh(device, value):
        assert rollback.saved_hz(rollback.load()) == 60
        changes.append(value)
        return True, ""

    def finish_work():
        fn, done = work.pop(0)
        done(fn())

    monkeypatch.setattr(gd_main.gd_bg, "submit", submit)
    monkeypatch.setattr(gd_main.gd_hz, "set_refresh", set_refresh)
    with_focus(monkeypatch, window, 500, "cs2.exe")
    with_focus(monkeypatch, window, 900, "explorer.exe")

    window.hz_combo = SimpleNamespace(currentData=lambda: 120)
    gd_main.MainWindow.apply_refresh_rate(window)
    assert len(work) == 1
    assert rollback.saved_hz(rollback.load()) == 60

    finish_work()
    assert len(work) == 1
    assert changes == [144]
    finish_work()
    assert changes == [144, 60]
    assert not work
    assert rollback.load() is None
    assert window._auto_prev_hz is None


def test_autoprofile_requires_a_saved_frequency_before_starting_worker(monkeypatch):
    window = FakeWindow([{"exe": "cs2.exe", "preset": 1, "hz": 144}], PRESETS)
    monkeypatch.setattr(gd_main.gd_hz, "refresh_rate", lambda: 60)
    monkeypatch.setattr(gd_main.gd_rollback, "load", lambda: None)
    monkeypatch.setattr(gd_main.gd_rollback, "merge", lambda *args: False)
    work = []
    monkeypatch.setattr(gd_main.gd_bg, "submit", lambda *a, **kw: work.append(a))

    with_focus(monkeypatch, window, 500, "cs2.exe")

    assert window.applied == [(1, True)]
    assert not work
    assert window._auto_prev_hz is None
    assert not window._hz_busy


def test_restore_can_use_in_memory_frequency_if_the_snapshot_disappears(monkeypatch):
    window = FakeWindow([], PRESETS)
    window._auto_prev_hz = 60
    monkeypatch.setattr(gd_main.gd_rollback, "load", lambda: None)
    changes = []
    monkeypatch.setattr(gd_main.gd_hz, "set_refresh",
                        lambda device, hertz: changes.append(hertz) or (True, ""))

    assert window._restore_before_shutdown()[0]
    assert changes == [60]


def test_failed_hz_apply_and_restore_keep_the_snapshot_for_next_process(monkeypatch, tmp_path):
    window = FakeWindow([{"exe": "cs2.exe", "preset": 1, "hz": 144}], PRESETS)
    rollback = gd_main.gd_rollback
    monkeypatch.setattr(rollback, "state_path", lambda: tmp_path / "rollback_state.json")
    monkeypatch.setattr(gd_main.gd_hz, "refresh_rate", lambda: 60)
    monkeypatch.setattr(gd_main.gd_hz, "set_refresh", lambda device, hertz: (False, "rejected"))
    monkeypatch.setattr(gd_main.gd_bg, "submit", lambda fn, done, parent: done(fn()))
    monkeypatch.setattr(rollback, "owner_alive", lambda data: True)
    assert rollback.save({"gamma": {"brightness": 1.0}})

    with_focus(monkeypatch, window, 500, "cs2.exe")
    snapshot = rollback.load()
    assert rollback.saved_hz(snapshot) == 60
    assert snapshot["gamma"] == {"brightness": 1.0}
    assert window.status[-1].endswith("rejected")

    with_focus(monkeypatch, window, 900, "explorer.exe")
    assert rollback.load() == snapshot
    assert window._auto_prev_hz == 60
    assert window.status[-1].endswith("rejected")

    next_window = FakeWindow([], PRESETS)
    monkeypatch.setattr(rollback, "owner_alive", lambda data: False)
    changes = []
    monkeypatch.setattr(gd_main.gd_hz, "set_refresh",
                        lambda device, hertz: changes.append(hertz) or (True, "restored"))
    gd_main.MainWindow._rollback_after_crash(next_window)

    assert changes == [60]
    assert rollback.load() == {key: value for key, value in snapshot.items() if key != "hz"}
    assert next_window.status[-1].endswith("restored")
