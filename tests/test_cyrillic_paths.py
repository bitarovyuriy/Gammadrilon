# -*- coding: utf-8 -*-
"""Кириллица и пробелы в пути zapret: предупреждение, но не отказ.

У пользователей папки бывают «D:\\Загрузки\\zapret-discord-youtube». Структура
там правильная, и работать это может — значит рубить отказом нельзя, но и
молчать нечестно: winws.exe падает уже после запуска, без внятной причины.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_zapret  # noqa: E402


def make_zapret(root: Path) -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "winws.exe").write_bytes(b"")
    (root / "service.bat").write_text("@echo off", encoding="utf-8")
    (root / "general.bat").write_text("@echo off", encoding="utf-8")
    (root / "general (ALT).bat").write_text("@echo off", encoding="utf-8")
    return root


def test_ascii_path_has_no_warnings(tmp_path):
    root = make_zapret(tmp_path / "zapret-discord-youtube")

    result = gd_zapret.validate(str(root))

    assert result["errors"] == []
    assert result["warnings"] == []


def test_cyrillic_path_warns_but_works(tmp_path):
    root = make_zapret(tmp_path / "Загрузки" / "zapret-discord-youtube")

    result = gd_zapret.validate(str(root))

    assert result["errors"] == [], "структура верная — отказывать нельзя"
    assert result["warnings"], "про кириллицу надо предупредить"


def test_path_with_spaces_warns(tmp_path):
    root = make_zapret(tmp_path / "Мои документы" / "zapret")

    result = gd_zapret.validate(str(root))

    assert result["errors"] == []
    assert result["warnings"]


def test_missing_structure_is_an_error(tmp_path):
    empty = tmp_path / "просто папка"
    empty.mkdir()

    result = gd_zapret.validate(str(empty))

    assert result["errors"], "без bin/winws.exe это не zapret"
    assert any("winws" in error for error in result["errors"])
