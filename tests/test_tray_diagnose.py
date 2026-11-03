# -*- coding: utf-8 -*-
"""Трей: пункт «Диагностика» ведёт на вкладку, а не в пустоту."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gd_tray  # noqa: E402
import main as gd_main  # noqa: E402


def test_tray_has_diagnose_signal():
    assert hasattr(gd_tray.TrayAssistant, "diagnoseRequested")


def test_tray_signal_is_connected_to_window():
    source = Path(gd_main.__file__).read_text(encoding="utf-8")
    assert "diagnoseRequested.connect" in source, "сигнал трея должен быть подключён"
    assert "def open_diagnose_tab" in source, "в окне должен быть обработчик"


def test_open_diagnose_tab_switches_page():
    class Tabs:
        index = None

        def setCurrentIndex(self, value):  # noqa: N802 — имя из Qt
            self.index = value

    class Window:
        _key_to_index = {"tab.diagnose": 9}
        tabs = Tabs()

        @staticmethod
        def show_window():
            return True

    gd_main.MainWindow.open_diagnose_tab(Window())

    assert Window.tabs.index == 9
