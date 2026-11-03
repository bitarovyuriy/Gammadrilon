# -*- coding: utf-8 -*-
"""Сброс настроек: заводские значения, копия прежнего файла, честный отказ."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as gd_main  # noqa: E402


class FakeWindow:
    def __init__(self, config: dict | None = None) -> None:
        self.config = config if config is not None else {}


def test_reset_writes_defaults_and_keeps_backup(monkeypatch, tmp_path):
    target = tmp_path / "presets.json"
    target.write_text(json.dumps({"settings": {"theme": "light"}, "presets": [{"name": "X"}]}),
                      encoding="utf-8")
    saved = {}

    monkeypatch.setattr(gd_main, "config_path", lambda: target)
    monkeypatch.setattr(gd_main, "save_config", lambda data: saved.update(data) or True)

    window = FakeWindow({"settings": {"theme": "light"}, "presets": [{"name": "X"}]})
    ok, note = gd_main.MainWindow.reset_app_settings(window)

    assert ok is True, note
    assert (tmp_path / "presets.before-reset.json").is_file(), "копия обязана остаться"
    assert saved["settings"]["theme"] == "dark", "сброс возвращает тёмную тему"
    assert window.config["settings"]["theme"] == "dark"
    assert window.config["presets"] == gd_main.DEFAULT_CONFIG["presets"]
    assert "сброшены" in note


def test_reset_reports_write_failure(monkeypatch, tmp_path):
    target = tmp_path / "presets.json"
    target.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(gd_main, "config_path", lambda: target)
    monkeypatch.setattr(gd_main, "save_config", lambda data: False)

    window = FakeWindow({"settings": {"theme": "light"}})
    ok, note = gd_main.MainWindow.reset_app_settings(window)

    assert ok is False
    assert "не удалось" in note
    assert window.config["settings"]["theme"] == "light", "при отказе ничего не меняем"


def test_reset_without_file_still_works(monkeypatch, tmp_path):
    target = tmp_path / "нет-файла.json"
    saved = {}

    monkeypatch.setattr(gd_main, "config_path", lambda: target)
    monkeypatch.setattr(gd_main, "save_config", lambda data: saved.update(data) or True)

    ok, note = gd_main.MainWindow.reset_app_settings(FakeWindow({"settings": {}}))

    assert ok is True, note
    assert saved, "файл настроек обязан быть создан"
    assert not (tmp_path / "presets.before-reset.json").exists(), "копии нет — копировать нечего"
