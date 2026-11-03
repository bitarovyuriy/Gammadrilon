# -*- coding: utf-8 -*-
"""Права на папку данных: проверка пробным файлом, а не «на бумаге»."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_paths  # noqa: E402


def test_writable_folder_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(gd_paths, "config_path", lambda: tmp_path / "Gammadrilon" / "presets.json")

    ok, detail = gd_paths.can_write_data()

    assert ok is True
    assert "Gammadrilon" in detail
    assert not (tmp_path / "Gammadrilon" / ".write_probe").exists(), "пробный файл убирается"


def test_readonly_folder_fails(monkeypatch, tmp_path):
    folder = tmp_path / "locked"
    folder.mkdir()
    monkeypatch.setattr(gd_paths, "config_path", lambda: folder / "presets.json")

    def deny(*_args, **_kwargs):
        raise PermissionError("нет прав")

    monkeypatch.setattr(Path, "write_text", deny)

    ok, detail = gd_paths.can_write_data()

    assert ok is False
    assert "нет прав" in detail
