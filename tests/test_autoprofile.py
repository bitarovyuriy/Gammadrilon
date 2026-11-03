# -*- coding: utf-8 -*-
"""Автопрофили по игре: вход в окно игры и возврат пресета на выходе.

Окно Qt не создаём: проверяем чистую логику — какой пресет выбран и что
происходит, когда игра теряет фокус. Применение пресета подменено списком.
"""
from __future__ import annotations

import sys
from pathlib import Path

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


class FakeWindow:
    """Ровно те поля, которыми пользуется _auto_profile_tick."""

    def __init__(self, rules: list[dict], presets: list[dict]) -> None:
        self.config = {"settings": {"auto_profiles": rules, "auto_profiles_on": True},
                       "presets": presets}
        self.current_preset_index = 0
        self._auto_prev_index = None
        self._auto_prev_hz = None
        self._fg_pid = 0
        self.applied: list[tuple[int, bool]] = []
        self.preset_combo = _Widget()
        self.shadow_slider = _Widget()
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

    def tick(self):
        return gd_main.MainWindow._auto_profile_tick(self)


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
