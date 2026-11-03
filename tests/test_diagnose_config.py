# -*- coding: utf-8 -*-
"""Диагностика: размер presets.json и предупреждение о распухшем конфиге."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_diagnose  # noqa: E402


def use_config(monkeypatch, tmp_path: Path) -> Path:
    target = tmp_path / "presets.json"
    monkeypatch.setattr(gd_diagnose.gd_paths, "config_path", lambda: target)
    return target


def test_missing_config_is_skipped(monkeypatch, tmp_path):
    use_config(monkeypatch, tmp_path)

    item = gd_diagnose.check_config_size()

    assert item["status"] == gd_diagnose.SKIP


def test_small_config_is_ok(monkeypatch, tmp_path):
    target = use_config(monkeypatch, tmp_path)
    target.write_text("{}" * 100, encoding="utf-8")

    item = gd_diagnose.check_config_size()

    assert item["status"] == gd_diagnose.OK
    assert item["detail"].endswith(("Б", "КБ"))


def test_bloated_config_warns(monkeypatch, tmp_path):
    target = use_config(monkeypatch, tmp_path)
    target.write_text("x" * (600 * 1024), encoding="utf-8")

    item = gd_diagnose.check_config_size()

    assert item["status"] == gd_diagnose.WARN
    assert "КБ" in item["detail"]
    assert item["action"], "предупреждение должно говорить, что делать"
