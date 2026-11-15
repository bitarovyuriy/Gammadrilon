# -*- coding: utf-8 -*-
"""Регрессии рычага «Оптимизация CS2».

Главное, что проверяем: включение записывает ровно заявленные значения, бэкап
помнит то, что было до первой правки (а не «до последней»), а выключение
возвращает прежние значения и удаляет то, чего в системе не было.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_cs2  # noqa: E402

BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
EXE = r"E:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\cs2.exe"

GAME_MODE = ("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled")
LAYERS = ("HKCU", r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers", EXE)
GPU_PREF = ("HKCU", r"Software\Microsoft\DirectX\UserGpuPreferences", EXE)
DVR = ("HKCU", r"System\GameConfigStore", "GameDVR_Enabled")
CAPTURE = ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled")


class FakeRegistry:
    """Реестр в памяти: значения по (ветвь, ключ, имя), без Windows."""

    def __init__(self, values: dict | None = None):
        self.values = dict(values or {})

    def read(self, hive, key, name):
        return self.values.get((hive, key, name))

    def write(self, hive, key, name, value, kind=gd_cs2.REG_DWORD):
        self.values[(hive, key, name)] = value

    def drop(self, hive, key, name):
        self.values.pop((hive, key, name), None)


class FakePower:
    """Схема питания в памяти: set() просто запоминает активный GUID."""

    def __init__(self, guid: str = BALANCED):
        self.guid = guid

    def active(self) -> str:
        return self.guid

    def set(self, guid: str) -> bool:
        self.guid = guid
        return True

    def available(self, guid: str) -> bool:
        return True


def _apply(ids, registry, power, path, exe=EXE):
    return gd_cs2.apply(ids, exe, registry=registry, power=power, path=path)


def test_soft_default_and_legacy_selections():
    assert gd_cs2.configured_ids(None) == ["game_mode"]
    assert gd_cs2.configured_ids(["fs_opt", "power_plan", "game_dvr"]) == ["power_plan", "fs_opt", "game_dvr"]
    assert gd_cs2.configured_ids([]) == []


def test_soft_profile_changes_only_game_mode(tmp_path, monkeypatch):
    registry, power = FakeRegistry({GAME_MODE: 0}), FakePower()
    backup = tmp_path / "cs2_backup.json"
    monkeypatch.setattr(gd_cs2, "backup_path", lambda: backup)

    assert _apply(gd_cs2.configured_ids(None), registry, power, backup)["applied"] == ["game_mode"]
    assert {name for _, _, name in registry.values} == {"AllowAutoGameMode", "AutoGameModeEnabled"}
    assert power.active() == BALANCED
    snapshot = gd_cs2.snapshot(EXE, registry=registry, power=power)
    assert snapshot["can_revert"] and gd_cs2.managed_counts(snapshot) == (1, 1)
    assert gd_cs2.revert(registry=registry, power=power)["ok"]
    assert registry.values == {GAME_MODE: 0}


def test_soft_profile_does_not_claim_existing_game_mode(tmp_path):
    values = {(hive, key, name): value for hive, key, name, value, _ in gd_cs2._entries("game_mode", "")}
    registry, power = FakeRegistry(values), FakePower()
    backup = tmp_path / "cs2_backup.json"

    report = _apply(gd_cs2.SOFT_DEFAULT, registry, power, backup)

    assert report["ok"] and report["already_active"] == ["game_mode"]
    assert registry.values == values
    assert not backup.exists()


def test_apply_sets_all_points_and_marks_them_on(tmp_path):
    registry, power = FakeRegistry(), FakePower()
    backup = tmp_path / "cs2_backup.json"

    report = _apply([spec["id"] for spec in gd_cs2.TWEAKS], registry, power, backup)

    assert report["ok"], report
    assert set(report["applied"]) == {spec["id"] for spec in gd_cs2.TWEAKS}
    assert registry.read(*GAME_MODE) == 1
    assert registry.read(*GPU_PREF) == "GpuPreference=2;"
    assert registry.read(*LAYERS) == "~ DISABLEDXMAXIMIZEDWINDOWEDMODE"
    assert registry.read(*DVR) == 0
    assert registry.read(*CAPTURE) == 0
    assert power.active() == gd_cs2.HIGH_PERF_GUID

    snap = gd_cs2.snapshot(EXE, registry=registry, power=power)
    assert snap["on"] is True
    assert all(snap["items"].values())


def test_revert_restores_previous_values_and_drops_absent_ones(tmp_path):
    # до включения: режим игры был выключен, а записи про CS2 в реестре не было вовсе
    registry = FakeRegistry({GAME_MODE: 0})
    power = FakePower()
    backup = tmp_path / "cs2_backup.json"

    _apply(["game_mode", "fs_opt", "gpu_pref"], registry, power, backup)
    assert registry.read(*GAME_MODE) == 1
    assert registry.read(*LAYERS) is not None

    report = gd_cs2.revert(registry=registry, power=power, path=backup)

    assert report["ok"], report
    assert registry.read(*GAME_MODE) == 0          # прежнее значение вернулось
    assert registry.read(*LAYERS) is None          # записи не было — её и нет
    assert registry.read(*GPU_PREF) is None
    assert power.active() == BALANCED
    assert not backup.exists()                     # бэкап израсходован


def test_second_apply_does_not_overwrite_the_first_backup(tmp_path):
    """Повторное включение не должно «запомнить» уже включённое состояние."""
    registry = FakeRegistry({GAME_MODE: 0})
    power = FakePower()
    backup = tmp_path / "cs2_backup.json"

    _apply(["game_mode"], registry, power, backup)
    _apply(["game_mode"], registry, power, backup)
    gd_cs2.revert(registry=registry, power=power, path=backup)

    assert registry.read(*GAME_MODE) == 0


def test_deleted_scheme_does_not_block_restoring_other_entries(tmp_path, monkeypatch):
    deleted = "FAAE2CA2-E202-44F8-A972-61214EF55FCA"
    cpu_clone = "B87F4B17-C211-4B72-97B4-2DD8E1CC8F9D"
    registry, power = FakeRegistry({GAME_MODE: 0}), FakePower(deleted)
    backup = tmp_path / "cs2_backup.json"
    monkeypatch.setattr(gd_cs2, "backup_path", lambda: backup)
    _apply(["power_plan", "game_mode"], registry, power, backup)
    power.guid = cpu_clone
    written = []
    power.available = lambda guid: guid != deleted
    power.set = lambda guid: written.append(guid) or False

    report = gd_cs2.revert(registry=registry, power=power, path=backup)

    assert report["ok"] is False
    assert deleted in report["errors"][0]
    assert registry.read(*GAME_MODE) == 0
    assert set(gd_cs2._load_backup(backup)) == {"power|active"}
    assert gd_cs2.snapshot(EXE, registry=registry, power=power)["missing_power"] == deleted
    assert not written
    assert power.active() == cpu_clone

    assert gd_cs2.forget_missing_scheme(deleted, power=power, path=backup)["ok"]
    assert not backup.exists()
    assert not written


def test_revert_retries_failed_power_write_and_restores_original(tmp_path, monkeypatch):
    registry, power = FakeRegistry({GAME_MODE: 0}), FakePower()
    backup = tmp_path / "cs2_backup.json"
    _apply(["power_plan", "game_mode"], registry, power, backup)
    with monkeypatch.context() as patch:
        patch.setattr(power, "set", lambda guid: False)
        assert not gd_cs2.revert(registry=registry, power=power, path=backup)["ok"]
    assert registry.read(*GAME_MODE) == 0
    assert power.active() == gd_cs2.HIGH_PERF_GUID
    assert set(gd_cs2._load_backup(backup)) == {"power|active"}
    assert not gd_cs2.forget_missing_scheme(BALANCED, power=power, path=backup)["ok"]

    assert gd_cs2.revert(registry=registry, power=power, path=backup)["ok"]
    assert power.active() == BALANCED
    assert not backup.exists()


def test_revert_keeps_scheme_selected_after_apply(tmp_path):
    registry, power = FakeRegistry(), FakePower()
    backup = tmp_path / "cs2_backup.json"
    _apply(["power_plan"], registry, power, backup)
    power.guid = gd_cs2.ULTIMATE_GUID

    assert gd_cs2.revert(registry=registry, power=power, path=backup)["ok"]
    assert power.active() == gd_cs2.ULTIMATE_GUID
    assert not backup.exists()


def test_apply_skips_existing_performance_plan_and_requires_readable_original(tmp_path):
    registry, power = FakeRegistry(), FakePower(gd_cs2.ULTIMATE_GUID)
    backup = tmp_path / "cs2_backup.json"

    assert _apply(["power_plan"], registry, power, backup)["ok"]
    assert power.active() == gd_cs2.ULTIMATE_GUID
    assert not backup.exists()

    power.guid = ""
    assert not _apply(["power_plan"], registry, power, backup)["ok"]
    assert not power.active()
    assert not backup.exists()


def test_missing_exe_is_reported_not_written(tmp_path):
    registry, power = FakeRegistry(), FakePower()
    backup = tmp_path / "cs2_backup.json"

    report = _apply(["fs_opt", "game_mode"], registry, power, backup, exe="")

    assert "fs_opt" not in report["applied"]
    assert "game_mode" in report["applied"]
    assert any("cs2.exe" in error for error in report["errors"])


def test_empty_selection_is_refused(tmp_path):
    registry, power = FakeRegistry(), FakePower()
    report = _apply([], registry, power, tmp_path / "cs2_backup.json")

    assert report["ok"] is False
    assert report["errors"]


def test_unknown_ids_are_ignored(tmp_path):
    registry, power = FakeRegistry(), FakePower()
    report = _apply(["нет такого"], registry, power, tmp_path / "cs2_backup.json")

    assert report["ok"] is False
    assert not registry.values


def test_revert_without_backup_says_so(tmp_path):
    report = gd_cs2.revert(registry=FakeRegistry(), power=FakePower(),
                           path=tmp_path / "пусто.json")

    assert report["ok"] is False
    assert "нечего возвращать" in report["errors"][0].lower()


def test_snapshot_sees_already_applied_values(tmp_path):
    """Рычаг не включали в приложении, но настройки уже стоят — экран не должен врать."""
    registry = FakeRegistry({
        GAME_MODE: 1,
        ("HKCU", r"Software\Microsoft\GameBar", "AllowAutoGameMode"): 1,
    })
    snap = gd_cs2.snapshot(EXE, registry=registry, power=FakePower(gd_cs2.HIGH_PERF_GUID))

    assert snap["items"]["game_mode"] is True
    assert snap["items"]["power_plan"] is True
    assert snap["items"]["fs_opt"] is False
    assert snap["on"] is False          # включены не все пункты


def test_video_notes_flag_expensive_settings(tmp_path):
    video = tmp_path / "cs2_video.txt"
    video.write_text(
        '"video.cfg"\n{\n'
        '\t"setting.defaultres"\t\t"1920"\n'
        '\t"setting.defaultresheight"\t\t"1080"\n'
        '\t"setting.fullscreen"\t\t"1"\n'
        '\t"setting.msaa_samples"\t\t"8"\n'
        '\t"setting.mat_vsync"\t\t"1"\n'
        '\t"setting.r_low_latency"\t\t"0"\n'
        '}\n',
        encoding="utf-8",
    )

    notes = {row["title"]: row for row in gd_cs2.video_notes(video)}

    assert notes["Сглаживание"]["level"] == "warn"          # 4x MSAA — дорого
    assert notes["Сглаживание"]["value"] == "4x MSAA"
    assert notes["Вертикальная синхронизация"]["level"] == "warn"
    assert notes["NVIDIA Reflex"]["level"] == "warn"
    assert notes["Разрешение"]["level"] == "ok"


def test_video_notes_empty_without_file(tmp_path):
    assert gd_cs2.video_notes(tmp_path / "нет.txt") == []


def test_launch_advice_tells_the_truth_about_options():
    advice = gd_cs2.launch_advice()

    assert "пустой" in advice
    assert "-threads" in advice
    assert "-novid" in advice
