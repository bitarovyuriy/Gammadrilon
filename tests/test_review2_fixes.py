# -*- coding: utf-8 -*-
"""Повторное ревью 3.6.3: отложенный клик CS2, жизненный цикл разделов, DDC, откат zapret."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as gd_main  # noqa: E402
import gd_cs2_ui  # noqa: E402
import gd_utils_ui  # noqa: E402
import gd_zapret_ui  # noqa: E402


class _Notes:
    """Заглушка вместо Cs2Widget: PyQt-объект через object.__new__ не создать."""

    def __init__(self):
        self.text = []
        self._ready = False
        self._want_toggle = False
        self.toggled = 0

    def _note(self, text):
        self.text.append(text)

    def _toggle(self):
        self.toggled += 1


# --- CS2: клик до готовности ------------------------------------------------

def test_cs2_toggle_is_deferred_until_first_snapshot():
    page = _Notes()

    assert gd_cs2_ui.Cs2Widget.toggle(page) is True
    assert page._want_toggle is True
    assert page.toggled == 0


def test_cs2_toggle_runs_immediately_when_ready():
    page = _Notes()
    page._ready = True

    gd_cs2_ui.Cs2Widget.toggle(page)

    assert page._want_toggle is False
    assert page.toggled == 1


# --- Утилиты: жизненный цикл раздела ---------------------------------------

class _Section:
    def __init__(self):
        self.calls = []

    def activate(self):
        self.calls.append("activate")

    def deactivate(self):
        self.calls.append("deactivate")


class _Utils:
    SECTION_INDEX = gd_utils_ui.UtilsWidget.SECTION_INDEX

    def __init__(self, section):
        self._section_widgets = {"games": section}


def test_section_lifecycle_calls_activate_and_deactivate():
    section = _Section()
    utils = _Utils(section)

    gd_utils_ui.UtilsWidget._section_lifecycle(utils, gd_utils_ui.UtilsWidget.SECTION_INDEX["games"], True)
    gd_utils_ui.UtilsWidget._section_lifecycle(utils, gd_utils_ui.UtilsWidget.SECTION_INDEX["games"], False)

    assert section.calls == ["activate", "deactivate"]


def test_section_lifecycle_ignores_unbuilt_section():
    utils = _Utils(None)   # раздел ещё не собран — молча пропускаем
    gd_utils_ui.UtilsWidget._section_lifecycle(utils, 2, True)


# --- DDC: сохранённый монитор главнее первого -------------------------------

class _Pick:
    def __init__(self, index):
        self._index = index

    def currentIndex(self):  # noqa: N802 — имя из Qt
        return self._index


class _Host:
    def __init__(self, pick):
        self.ddc_pick = pick


class _Monitor:
    def __init__(self, name):
        self.description = name


def test_saved_ddc_monitor_wins_over_first(monkeypatch):
    monitors = [_Monitor("Первый"), _Monitor("Второй")]
    monkeypatch.setattr(gd_main, "usable_monitors", lambda ddc: monitors)
    host = _Host(_Pick(1))

    assert gd_main.MainWindow._picked_ddc_monitor(host, object()) is monitors[1]


def test_single_monitor_is_returned(monkeypatch):
    monitors = [_Monitor("Единственный")]
    monkeypatch.setattr(gd_main, "usable_monitors", lambda ddc: monitors)
    host = _Host(_Pick(0))

    assert gd_main.MainWindow._picked_ddc_monitor(host, object()) is monitors[0]


# --- zapret: откат возвращает обход ----------------------------------------

class _Zapret:
    """Заглушка вместо ZapretWidget: нужен только метод восстановления."""


class _Client:
    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def call(self, payload, timeout=None):
        self.calls.append(payload)
        return self.answers.get(payload.get("op") or payload.get("cmd"), {"ok": True})


def test_rollback_restarts_service_even_after_failure():
    client = _Client({"start": {"ok": True}})
    page = _Zapret()

    result = gd_zapret_ui.ZapretWidget._restart_after_rollback(page, client, "D:/za", True, {})

    assert result == {"ok": True}
    assert client.calls == [{"cmd": "zapret_service", "op": "start"}]


def test_rollback_restarts_own_process_only():
    client = _Client({
        "zapret_recon": {"ok": True, "data": {"argv": ["winws.exe"]}},
        "zapret_start": {"ok": True, "data": {"pid": 4242}},
    })
    page = _Zapret()
    ours = {"pid": 123, "strategy": "general", "external": False}

    result = gd_zapret_ui.ZapretWidget._restart_after_rollback(page, client, "D:/za", False, ours)

    assert result.get("ok") is True
    assert client.calls[0]["cmd"] == "zapret_recon"
    assert client.calls[1]["cmd"] == "zapret_start"
