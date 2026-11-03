# -*- coding: utf-8 -*-
"""Игровой буст и умная очистка: логика, которую можно проверить без прав.

Реальные приоритеты чужих процессов в тестах не меняем — проверяем расчёт,
классификацию и восстановление по снимку. Живые вызовы WinAPI — отдельным
тестом на своём дочернем процессе.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_boost  # noqa: E402 — путь проекта добавлен выше
import gd_paths  # noqa: E402


def test_system_processes_never_touched():
    for name in ("System", "csrss.exe", "MemCompression", "explorer.exe", "winws.exe"):
        assert gd_boost.protected(name), name
        assert not gd_boost.protected("chrome.exe"), name


def test_background_list_covers_the_usual_memory_hogs():
    assert gd_boost.background("chrome.exe")
    assert gd_boost.background("Telegram.exe"), "сравнение должно быть без учёта регистра"
    assert not gd_boost.background("game.exe")


def test_purge_only_when_free_is_low_and_standby_is_big():
    """Ровно та ошибка, из-за которой кнопка «оптимизировать» вредит играм."""
    assert gd_boost.should_purge(500, 6000), "мало свободной и большой кэш"
    assert not gd_boost.should_purge(500, 2000), "кэш небольшой — чистить нечего"
    assert not gd_boost.should_purge(3000, 6000), "свободной памяти достаточно"
    assert not gd_boost.should_purge(1024, 5000), "граница: не меньше порога"
    assert gd_boost.should_purge(1023, 4097)


def test_memory_priority_roundtrip_on_child_process():
    """Живой WinAPI: читаем, меняем, возвращаем — на своём процессе, не на чужом."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        deadline = time.time() + 5
        initial = None
        while time.time() < deadline and initial is None:
            initial = gd_boost.get_memory_priority(child.pid)
            time.sleep(0.1)
        assert initial is not None, "приоритет памяти должен читаться у своего процесса"

        assert gd_boost.set_memory_priority(child.pid, gd_boost.MEMORY_PRIORITY_VERY_LOW)
        assert gd_boost.get_memory_priority(child.pid) == gd_boost.MEMORY_PRIORITY_VERY_LOW

        assert gd_boost.set_memory_priority(child.pid, initial)
        assert gd_boost.get_memory_priority(child.pid) == initial
    finally:
        child.kill()
        child.wait(timeout=10)


def test_game_boost_snapshot_and_restore(tmp_path, monkeypatch):
    """Включение снимает снимок и пишет его на диск, выключение — возвращает всё."""
    calls = []

    monkeypatch.setattr(gd_paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(gd_boost, "anticheat_present", lambda: None)
    monkeypatch.setattr(gd_boost, "collect_targets",
                        lambda **kw: [{"pid": 111, "name": "chrome.exe", "rss_mb": 900.0}])
    monkeypatch.setattr(gd_boost, "get_memory_priority", lambda pid: 5)
    monkeypatch.setattr(gd_boost, "get_eco_qos", lambda pid: False)
    monkeypatch.setattr(gd_boost, "get_cpu_priority", lambda pid: gd_boost.NORMAL_PRIORITY_CLASS)
    monkeypatch.setattr(gd_boost, "set_memory_priority",
                        lambda pid, value=1: calls.append(("mem", pid, value)) or True)
    monkeypatch.setattr(gd_boost, "set_eco_qos",
                        lambda pid, enabled=True: calls.append(("eco", pid, enabled)) or True)
    monkeypatch.setattr(gd_boost, "set_cpu_priority",
                        lambda pid, value=1: calls.append(("cpu", pid, value)) or True)
    monkeypatch.setattr(gd_boost.gd_memory, "empty_working_sets_native",
                        lambda pids: (True, "ok", len(pids)))
    monkeypatch.setitem(sys.modules, "gd_extra", type(sys)("gd_extra"))
    sys.modules["gd_extra"].set_timer_resolution = lambda enabled=True: True

    boost = gd_boost.GameBoost()
    ok, message = boost.start()
    assert ok, message
    assert boost.active()
    assert (tmp_path / gd_boost.STATE_FILE).is_file(), "снимок должен пережить падение приложения"
    saved = json.loads((tmp_path / gd_boost.STATE_FILE).read_text(encoding="utf-8"))
    assert saved["affected"]["111"]["memory_priority"] == 5

    ok, message = boost.stop()
    assert ok, message
    assert not boost.active()
    assert ("mem", 111, 5) in calls, "исходный приоритет памяти должен вернуться"
    assert ("eco", 111, False) in calls, "EcoQoS должен быть снят"
    assert not (tmp_path / gd_boost.STATE_FILE).exists()


def test_boost_restores_after_crash(tmp_path, monkeypatch):
    """Снимок на диске — единственный способ откатить приоритеты после падения."""
    monkeypatch.setattr(gd_paths, "data_dir", lambda: tmp_path)
    restored = []
    monkeypatch.setattr(gd_boost, "set_memory_priority",
                        lambda pid, value=1: restored.append(("mem", pid, value)) or True)
    monkeypatch.setattr(gd_boost, "set_eco_qos", lambda pid, enabled=True: True)
    monkeypatch.setattr(gd_boost, "set_cpu_priority", lambda pid, value=1: True)

    (tmp_path / gd_boost.STATE_FILE).write_text(json.dumps({
        "active": True,
        "game_pid": None,
        "timer": False,
        "affected": {"222": {"name": "telegram.exe", "memory_priority": 3,
                             "eco_qos": False, "cpu_priority": None}},
    }), encoding="utf-8")

    boost = gd_boost.GameBoost()
    assert boost.restore_from_disk()
    assert ("mem", 222, 3) in restored
    assert not (tmp_path / gd_boost.STATE_FILE).exists()


def test_anticheat_detection_uses_process_names(monkeypatch):
    class Proc:
        def __init__(self, name):
            self.info = {"name": name}

    monkeypatch.setattr(gd_boost.psutil, "process_iter",
                        lambda fields=None: [Proc("chrome.exe"), Proc("vgc.exe")])
    assert gd_boost.anticheat_present() == "vgc.exe"

    monkeypatch.setattr(gd_boost.psutil, "process_iter",
                        lambda fields=None: [Proc("chrome.exe")])
    assert gd_boost.anticheat_present() is None
