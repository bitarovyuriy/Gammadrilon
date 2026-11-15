# -*- coding: utf-8 -*-
"""Регрессии отката рычага CS2 на подменном реестре."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import gd_cs2


ALLOW_GAME_MODE = ("HKCU", r"Software\Microsoft\GameBar", "AllowAutoGameMode")
ENABLE_GAME_MODE = ("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled")
GAME_PATH = r"C:\SteamLibrary\game\bin\win64\cs2.exe"


class Registry:
    def __init__(self, values):
        self.values = dict(values)

    def read(self, hive, key, name):
        return self.values.get((hive, key, name))

    def write(self, hive, key, name, value, kind):
        self.values[hive, key, name] = value

    def drop(self, hive, key, name):
        self.values.pop((hive, key, name), None)


def test_apply_backs_up_only_changed_registry_values(tmp_path):
    registry = Registry({ALLOW_GAME_MODE: 1, ENABLE_GAME_MODE: 0})
    backup = tmp_path / "cs2_backup.json"
    power = Mock()

    assert gd_cs2.apply(["game_mode"], GAME_PATH, registry=registry, power=power, path=backup)["ok"]
    saved = json.loads(backup.read_text(encoding="utf-8"))
    assert set(saved["items"]) == {"|".join(ENABLE_GAME_MODE)}

    registry.values[ALLOW_GAME_MODE] = 0
    assert gd_cs2.revert(registry=registry, power=power, path=backup)["ok"]
    assert registry.values == {ALLOW_GAME_MODE: 0, ENABLE_GAME_MODE: 0}


def test_legacy_revert_keeps_new_registry_value(tmp_path):
    registry = Registry({ALLOW_GAME_MODE: 2, ENABLE_GAME_MODE: 1})
    backup = tmp_path / "cs2_backup.json"
    backup.write_text(json.dumps({
        "version": 1,
        "items": {
            "|".join(ALLOW_GAME_MODE): {"exist": True, "value": 0, "kind": "dword"},
            "|".join(ENABLE_GAME_MODE): {"exist": True, "value": 0, "kind": "dword"},
        },
    }), encoding="utf-8")

    report = gd_cs2.revert(registry=registry, power=Mock(), path=backup)

    assert report["ok"], report
    assert report["restored"] == 1
    assert registry.values == {ALLOW_GAME_MODE: 2, ENABLE_GAME_MODE: 0}
    assert not backup.exists()


def test_apply_cannot_take_registry_snapshot_when_read_is_denied(tmp_path, monkeypatch):
    winreg = SimpleNamespace(
        OpenKey=Mock(side_effect=PermissionError("read blocked")),
        SetValueEx=Mock(),
        CreateKeyEx=MagicMock(),
        KEY_SET_VALUE=0,
    )
    monkeypatch.setattr(gd_cs2, "winreg", winreg)
    backup = tmp_path / "cs2_backup.json"

    report = gd_cs2.apply(["game_mode"], GAME_PATH, power=Mock(), path=backup)

    assert not report["ok"]
    assert any("read blocked" in error for error in report["errors"])
    assert not backup.exists()
    winreg.CreateKeyEx.assert_not_called()
