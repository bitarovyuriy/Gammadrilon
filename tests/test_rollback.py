# -*- coding: utf-8 -*-
"""Снимок временных изменений: запись, чтение, сирота, откат после сбоя."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_rollback  # noqa: E402


def use_tmp(monkeypatch, tmp_path: Path) -> Path:
    target = tmp_path / "rollback_state.json"
    monkeypatch.setattr(gd_rollback, "state_path", lambda: target)
    return target


def test_save_and_load(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)

    assert gd_rollback.save({"hz": {"hz": 60}}, pid=1234) is True
    data = gd_rollback.load()

    assert data["hz"] == {"hz": 60}
    assert data["pid"] == 1234
    assert data["at"] > 0


def test_merge_keeps_other_sections(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)

    gd_rollback.save({"hz": {"hz": 60}}, pid=1)
    gd_rollback.merge("gamma", {"brightness": 1.0})

    data = gd_rollback.load()
    assert data["hz"] == {"hz": 60}
    assert data["gamma"] == {"brightness": 1.0}
    assert data["pid"] == os.getpid(), "хозяин снимка — текущий процесс"


def test_clear_removes_file(monkeypatch, tmp_path):
    target = use_tmp(monkeypatch, tmp_path)
    gd_rollback.save({"hz": {"hz": 60}})

    assert gd_rollback.clear() is True
    assert not target.exists()
    assert gd_rollback.load() is None


def test_save_empty_is_clear(monkeypatch, tmp_path):
    target = use_tmp(monkeypatch, tmp_path)
    gd_rollback.save({"hz": {"hz": 60}})

    assert gd_rollback.save({}) is True
    assert not target.exists()


def test_owner_alive_for_self_and_for_dead_pid(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)

    gd_rollback.save({"hz": {"hz": 60}})
    assert gd_rollback.owner_alive() is True
    assert gd_rollback.orphaned() is None

    data = gd_rollback.load()
    data["pid"] = 999_999_999
    assert gd_rollback.owner_alive(data) is False
    assert gd_rollback.orphaned(data) is not None


def test_broken_file_is_not_a_snapshot(monkeypatch, tmp_path):
    target = use_tmp(monkeypatch, tmp_path)
    target.write_text("{ не json", encoding="utf-8")

    assert gd_rollback.load() is None
    assert gd_rollback.orphaned() is None
    assert gd_rollback.clear() is True


def test_describe_lists_changes(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)

    text = gd_rollback.describe({"hz": 60, "preset": 1, "gamma": {}, "gpu": True})

    assert "частота 60" in text
    assert "пресет #2" in text
    assert "гамма" in text and "GPU" in text
    assert gd_rollback.describe(None) == ""


@pytest.mark.parametrize("saved_hertz", [60, {"hz": 60}])
def test_refresh_restore_preserves_snapshot_until_success(monkeypatch, tmp_path, saved_hertz):
    from main import MainWindow, gd_hz

    use_tmp(monkeypatch, tmp_path)
    assert gd_rollback.save({"hz": saved_hertz, "gamma": {}}, pid=1234)
    data = gd_rollback.load()
    calls = []

    def rejected(device, rate):
        calls.append((device, rate))
        return False, "rejected"

    monkeypatch.setattr(gd_hz, "set_refresh", rejected)
    assert MainWindow._restore_saved_hz(data) == (False, "rejected")
    assert calls == [(None, 60)]
    assert gd_rollback.load() == data

    def accepted(device, rate):
        calls.append((device, rate))
        return True, "restored"

    monkeypatch.setattr(gd_hz, "set_refresh", accepted)
    assert MainWindow._restore_saved_hz(data) == (True, "restored")
    assert calls == [(None, 60), (None, 60)]
    assert gd_rollback.load() == {k: v for k, v in data.items() if k != "hz"}


def test_restore_does_not_discard_new_snapshot(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    gd_rollback.save({"hz": 60})
    previous = gd_rollback.load()
    gd_rollback.save({"hz": 75})

    assert gd_rollback.saved_hz(previous) == 60
    assert gd_rollback.discard_hz(previous) is False
    assert gd_rollback.saved_hz(gd_rollback.load()) == 75
