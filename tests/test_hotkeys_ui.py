# -*- coding: utf-8 -*-
"""Панель «Клавиши»: отвязка сочетания и сохранение в конфиг."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# offscreen и пути плагинов — до PyQt5: без этого процесс падает молча
# с кодом 127 на не-ASCII пути (проверено на этом же проекте)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import main as _gd_main  # noqa: E402,F401  — выставляет QT_PLUGIN_PATH

from PyQt5.QtWidgets import QApplication  # noqa: E402

# QApplication держим в модульной переменной: локальный объект соберёт GC,
# и процесс упадёт молча (в PyQt5 это уже случалось)
_APP = QApplication.instance() or QApplication([])

import gd_hotkeys_ui as ui  # noqa: E402


def _panel(config=None):
    saved, applied = [], []
    panel = ui.HotkeysPanel(
        config=config if config is not None else {"settings": {}},
        save_fn=lambda: saved.append(True),
        apply_fn=lambda: applied.append(True),
    )
    return panel, saved, applied


def test_empty_shortcut_is_saved_as_free():
    panel, saved, applied = _panel()

    panel._clear("reset")
    panel._save()

    assert saved == [True] and applied == [True]
    assert panel._settings()["hotkeys"]["reset"] == ""
    assert panel._fields["reset"].text() == ""


def test_all_actions_can_be_cleared():
    panel, saved, _ = _panel()

    for key, _label in ui.ACTIONS:
        panel._clear(key)
    panel._save()

    values = panel._settings()["hotkeys"]
    assert set(values) == {key for key, _ in ui.ACTIONS}
    assert all(value == "" for value in values.values())
    assert saved == [True]


def test_broken_shortcut_is_rejected_and_not_saved():
    panel, saved, applied = _panel()

    panel._fields["reset"].setText("ctrl+нетакой")
    panel._save()

    assert saved == [] and applied == []
    assert "не понимаю" in panel.lbl_status.text()


def test_same_shortcut_twice_is_rejected():
    panel, saved, _ = _panel()

    panel._fields["reset"].setText("ctrl+alt+k")
    panel._fields["perf_toggle"].setText("ctrl+alt+k")
    panel._save()

    assert saved == []
    assert "уже занято" in panel.lbl_status.text()


def test_defaults_restore_known_values():
    panel, _, _ = _panel()

    panel._clear("next_preset")
    panel._restore_defaults()

    assert panel._fields["next_preset"].text() == ui.DEFAULTS["next_preset"]


def test_existing_values_are_shown():
    config = {"settings": {"hotkeys": {"reset": "", "next_preset": "f9"}}}
    panel, _, _ = _panel(config)

    assert panel._fields["reset"].text() == ""
    assert panel._fields["next_preset"].text() == "f9"


@pytest.mark.parametrize("spec", ["ctrl+alt+k", "page up", "scroll lock", "f9"])
def test_known_specs_pass_validation(spec):
    panel, _, _ = _panel()

    # остальные поля свободны: иначе «page up» конфликтует с «следующим пресетом»
    for key in panel._fields:
        panel._fields[key].clear()
    panel._fields["reset"].setText(spec)

    assert panel._validate()[0] is True
