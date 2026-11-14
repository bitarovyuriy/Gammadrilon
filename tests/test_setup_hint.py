# -*- coding: utf-8 -*-
"""Главный экран: подсказка «что ещё не настроено»."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gd_dashboard_ui  # noqa: E402


class Host:
    def __init__(self, settings: dict) -> None:
        self.config = {"settings": settings}


def test_fresh_install_lists_both():
    missing = gd_dashboard_ui.missing_setup(Host({}))

    assert len(missing) == 2
    assert any("Обход" in item for item in missing)
    assert any("Гамма" in item for item in missing)


def test_configured_bypass_is_not_listed():
    missing = gd_dashboard_ui.missing_setup(Host({"zapret_path": "D:/za"}))

    assert not any("Обход" in item for item in missing)


def test_auto_profiles_are_not_listed():
    missing = gd_dashboard_ui.missing_setup(
        Host({"zapret_path": "D:/za", "auto_profiles": [{"exe": "cs2.exe", "preset": 1}]}))

    assert missing == []


def test_blank_bypass_path_counts_as_missing():
    missing = gd_dashboard_ui.missing_setup(Host({"zapret_path": "   "}))

    assert any("Обход" in item for item in missing)


def test_no_config_is_not_a_crash():
    class Bare:
        config = None

    assert len(gd_dashboard_ui.missing_setup(Bare())) == 2


def test_issues_carry_tab_keys():
    issues = gd_dashboard_ui.setup_issues(Host({}))

    assert [key for _text, key in issues] == ["tab.zapret", "tab.gamma"]


def test_click_opens_first_issue_tab():
    class Tabs:
        index = None

        def setCurrentIndex(self, value):  # noqa: N802 — имя из Qt
            self.index = value

    class Window:
        config = {"settings": {}}
        _key_to_index = {"tab.zapret": 8, "tab.gamma": 1}
        tabs = Tabs()

    window = Window()
    window.host = window
    gd_dashboard_ui.DashboardWidget._goto_setup_issue(window)

    assert Window.tabs.index == 8


def test_click_without_issues_stays_put():
    class Tabs:
        index = None

        def setCurrentIndex(self, value):  # noqa: N802
            self.index = value

    class Window:
        config = {"settings": {"zapret_path": "D:/za",
                               "auto_profiles": [{"exe": "cs2.exe", "preset": 1}]}}
        _key_to_index = {"tab.zapret": 8}
        tabs = Tabs()

    window = Window()
    window.host = window
    gd_dashboard_ui.DashboardWidget._goto_setup_issue(window)

    assert Window.tabs.index is None

def test_rebuilding_rows_during_snapshot_does_not_destroy_running_thread(monkeypatch):
    from threading import Event

    from PyQt5 import sip
    from PyQt5.QtCore import QCoreApplication, QEvent
    from PyQt5.QtWidgets import QApplication

    import gd_bg
    import gd_zapret

    app = QApplication.instance() or QApplication([])
    started = Event()
    finish = Event()

    def snapshot(_path):
        started.set()
        finish.wait(5)
        return {}

    monkeypatch.setattr(gd_zapret, "snapshot", snapshot)
    monkeypatch.setattr(gd_dashboard_ui.gd_metrics, "start", lambda: None)
    board = gd_dashboard_ui.DashboardWidget(Host({}))
    board.layout_model = {"rows": [{"tiles": [{"id": "w.zapret"}]}]}
    board.reload()
    tile = board._tiles["w.zapret"]
    tile.refresh_slow()
    worker = tile._worker
    try:
        assert started.wait(3)
        board.reload()
        # До обработки удаления проверяем, что регрессия даст assert, а не abort Qt.
        assert worker.parent() is None
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert sip.isdeleted(tile)
        assert gd_bg.is_running(worker)
    finally:
        finish.set()
        assert worker.wait(3000)
        app.processEvents()
        board.close()
