# -*- coding: utf-8 -*-
"""Выбор монитора для подсветки: в список попадают только умеющие DDC/CI."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as gd_main  # noqa: E402


class FakeMonitor:
    def __init__(self, name: str, supported: bool) -> None:
        self.description = name
        self.supported = supported


class FakeDdc:
    def __init__(self, monitors) -> None:
        self.monitors = monitors


def test_only_supported_monitors_are_listed():
    ddc = FakeDdc([FakeMonitor("DELL U2720Q", True),
                   FakeMonitor("Ноутбук", False),
                   FakeMonitor("LG 27GL850", True)])

    names = [item.description for item in gd_main.usable_monitors(ddc)]

    assert names == ["DELL U2720Q", "LG 27GL850"]


def test_no_monitors_gives_empty_list():
    assert gd_main.usable_monitors(FakeDdc([])) == []
    assert gd_main.usable_monitors(None) == []


def test_monitor_without_supported_flag_is_skipped():
    class Bare:
        monitors = [object()]

    assert gd_main.usable_monitors(Bare()) == []
