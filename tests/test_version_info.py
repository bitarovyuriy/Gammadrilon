# -*- coding: utf-8 -*-
"""Метаданные версии для PyInstaller: один источник — APP_VERSION."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import make_version_info  # noqa: E402


def test_version_is_read_from_main():
    version = make_version_info.app_version()

    assert version.startswith("v") or version[0].isdigit()
    assert "." in version


def test_numeric_parts_are_padded():
    assert make_version_info.numbers("3.5") == (3, 5, 0)
    assert make_version_info.numbers("v3.5.2") == (3, 5, 2)
    assert make_version_info.numbers("v4") == (4, 0, 0)


def test_render_has_all_four_fields():
    text = make_version_info.render("v3.5")

    assert "filevers=(3, 5, 0, 0)" in text
    assert "prodvers=(3, 5, 0, 0)" in text
    assert "StringStruct('FileVersion', '3.5')" in text
    assert "ProductVersion', '3.5'" in text


def test_missing_version_is_an_error(tmp_path):
    source = tmp_path / "main.py"
    source.write_text("# пусто", encoding="utf-8")

    try:
        make_version_info.app_version(source)
    except SystemExit as exc:
        assert "APP_VERSION" in str(exc)
    else:  # pragma: no cover - ветка на случай, если ошибка не поднялась
        raise AssertionError("без APP_VERSION должен быть отказ")
