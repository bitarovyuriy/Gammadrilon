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
