# -*- coding: utf-8 -*-
"""Конфиг: копия .good при записи и восстановление из неё при поломке."""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("legacy_dir", ["app", "programdata"])
def test_installed_upgrade_can_save_migrated_legacy_settings(monkeypatch, tmp_path, legacy_dir):
    app = tmp_path / "Программа с пробелами"
    programdata = tmp_path / "ProgramData"
    localappdata = tmp_path / "LocalAppData"
    monkeypatch.setattr(gd_paths, "app_dir", lambda: app)
    monkeypatch.setenv("GD_PORTABLE", "0")
    monkeypatch.setenv("ProgramData", str(programdata))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    old_dir = app if legacy_dir == "app" else programdata / gd_paths.APP_NAME
    old_dir.mkdir(parents=True)
    old_file = old_dir / gd_paths.CONFIG_NAME
    previous = {
        "settings": {"theme": "light", "close_on_x": True, "hotkeys": {"toggle": "Ctrl+Alt+G"}},
        "presets": [{"name": "Ночь", "gamma": 1.25, "hotkey": "Ctrl+Alt+N"}],
    }
    original = json.dumps(previous, ensure_ascii=False)
    old_file.write_text(original, encoding="utf-8")
    old_file.chmod(stat.S_IREAD)
    try:
        migrated = gd_main.load_config()
        assert migrated["settings"]["theme"] == "light"
        assert migrated["settings"]["close_on_x"] is True
        assert migrated["settings"]["hotkeys"]["toggle"] == "Ctrl+Alt+G"
        assert migrated["presets"][0]["name"] == "Ночь"
        assert migrated["presets"][0]["gamma"] == 1.25
        assert migrated["presets"][0]["hotkey"] == "Ctrl+Alt+N"
        assert gd_paths.config_path().stat().st_mode & stat.S_IWRITE
        migrated["settings"]["theme"] = "dark"
        assert gd_main.save_config(migrated)
        assert gd_main.load_config() == migrated
        assert old_file.read_text(encoding="utf-8") == original
    finally:
        old_file.chmod(stat.S_IREAD | stat.S_IWRITE)
        active_file = gd_paths.config_path()
        if active_file.exists():
            active_file.chmod(stat.S_IREAD | stat.S_IWRITE)
