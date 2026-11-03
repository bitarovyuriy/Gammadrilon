# -*- coding: utf-8 -*-
"""Конфиг: копия .good при записи и восстановление из неё при поломке."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_paths  # noqa: E402
import main as gd_main  # noqa: E402


def use_config(monkeypatch, tmp_path: Path) -> Path:
    target = tmp_path / "presets.json"
    monkeypatch.setattr(gd_main, "config_path", lambda: target)
    monkeypatch.setattr(gd_paths, "migrate_legacy_config", lambda: None)
    return target


def test_save_keeps_previous_version(monkeypatch, tmp_path):
    target = use_config(monkeypatch, tmp_path)
    target.write_text(json.dumps({"settings": {"theme": "light"}}), encoding="utf-8")

    assert gd_main.save_config({"settings": {"theme": "dark"}}) is True

    good = target.with_name(target.name + ".good")
    assert good.is_file(), "предыдущая версия должна остаться копией"
    assert "light" in good.read_text(encoding="utf-8")
    assert "dark" in target.read_text(encoding="utf-8")


def test_broken_file_is_restored_from_good(monkeypatch, tmp_path):
    target = use_config(monkeypatch, tmp_path)
    target.write_text("{ битый json", encoding="utf-8")
    good = target.with_name(target.name + ".good")
    good.write_text(json.dumps({"settings": {"theme": "light"},
                                "presets": [{"name": "Ночь"}]}), encoding="utf-8")

    data = gd_main.load_config()

    assert data["settings"]["theme"] == "light"
    assert data["presets"][0]["name"] == "Ночь"
    assert gd_main.CONFIG_RECOVERY == "restored"
    assert target.with_name(target.name + ".bak").is_file(), "битый файл сохранён"


def test_broken_file_without_good_falls_back_to_defaults(monkeypatch, tmp_path):
    target = use_config(monkeypatch, tmp_path)
    target.write_text("не json", encoding="utf-8")

    data = gd_main.load_config()

    assert data["presets"] == gd_main.DEFAULT_CONFIG["presets"]
    assert gd_main.CONFIG_RECOVERY in ("backup", "no_backup")


def test_broken_good_copy_does_not_break_start(monkeypatch, tmp_path):
    target = use_config(monkeypatch, tmp_path)
    target.write_text("[]", encoding="utf-8")          # не объект
    good = target.with_name(target.name + ".good")
    good.write_text("тоже битый", encoding="utf-8")

    data = gd_main.load_config()

    assert isinstance(data, dict)
    assert data["presets"] == gd_main.DEFAULT_CONFIG["presets"]
