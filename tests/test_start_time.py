# -*- coding: utf-8 -*-
"""Диагностика: время старта из прошлого запуска."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_diagnose  # noqa: E402


def use_config(monkeypatch, tmp_path: Path, settings: dict) -> Path:
    target = tmp_path / "presets.json"
    target.write_text(json.dumps({"settings": settings}), encoding="utf-8")
    monkeypatch.setattr(gd_diagnose.gd_paths, "config_path", lambda: target)
    return target


def test_without_measurement_is_skipped(monkeypatch, tmp_path):
    use_config(monkeypatch, tmp_path, {})

    assert gd_diagnose.check_start_time()["status"] == gd_diagnose.SKIP


def test_fast_start_is_ok(monkeypatch, tmp_path):
    use_config(monkeypatch, tmp_path, {"last_boot_ms": 640})

    item = gd_diagnose.check_start_time()

    assert item["status"] == gd_diagnose.OK
    assert item["detail"] == "0.6 с"


def test_slow_start_warns(monkeypatch, tmp_path):
    use_config(monkeypatch, tmp_path, {"last_boot_ms": 5200})

    item = gd_diagnose.check_start_time()

    assert item["status"] == gd_diagnose.WARN
    assert item["action"]


def test_broken_config_is_not_a_crash(monkeypatch, tmp_path):
    target = tmp_path / "presets.json"
    target.write_text("не json", encoding="utf-8")
    monkeypatch.setattr(gd_diagnose.gd_paths, "config_path", lambda: target)

    assert gd_diagnose.check_start_time()["status"] == gd_diagnose.SKIP
