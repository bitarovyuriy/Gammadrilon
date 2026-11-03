# -*- coding: utf-8 -*-
"""Визуальная регрессия: сравнение снимков считает именно расхождение."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import visual_check  # noqa: E402

_APP = None


def app():
    global _APP
    from PyQt5.QtWidgets import QApplication

    _APP = QApplication.instance() or QApplication([])
    return _APP


def paint(path: Path, colour: str) -> Path:
    from PyQt5.QtGui import QColor, QImage

    image = QImage(200, 120, QImage.Format_RGB32)
    image.fill(QColor(colour))
    image.save(str(path))
    return path


def test_identical_images_have_no_difference(tmp_path):
    app()
    first = paint(tmp_path / "a.png", "#101010")
    second = paint(tmp_path / "b.png", "#101010")

    assert visual_check.diff_ratio(first, second) == 0.0


def test_other_colour_is_a_full_difference(tmp_path):
    app()
    first = paint(tmp_path / "a.png", "#101010")
    second = paint(tmp_path / "b.png", "#f0f0f0")

    assert visual_check.diff_ratio(first, second) > 0.9


def test_different_size_is_a_difference(tmp_path):
    app()
    from PyQt5.QtGui import QColor, QImage

    first = paint(tmp_path / "a.png", "#101010")
    big = QImage(400, 240, QImage.Format_RGB32)
    big.fill(QColor("#101010"))
    big.save(str(tmp_path / "big.png"))

    assert visual_check.diff_ratio(first, tmp_path / "big.png") == 1.0


def test_missing_file_is_not_a_crash(tmp_path):
    app()
    first = paint(tmp_path / "a.png", "#101010")

    assert visual_check.diff_ratio(first, tmp_path / "нет.png") == 1.0


def test_tabs_are_sorted_by_index():
    app()
    import main as gd_main

    window = gd_main.MainWindow.__new__(gd_main.MainWindow)
    window._key_to_index = {"tab.gamma": 1, "tab.home": 0, "мусор": "не индекс"}

    assert visual_check.tabs(window) == [("tab.home", 0), ("tab.gamma", 1)]
