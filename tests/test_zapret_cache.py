# -*- coding: utf-8 -*-
"""Кэш перебора стратегий: помнить удачные и не путать папки."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_zapret as zapret  # noqa: E402


def use_tmp(monkeypatch, tmp_path: Path) -> Path:
    target = tmp_path / "zapret_probe.json"
    monkeypatch.setattr(zapret, "_probe_cache_file", lambda: target)
    return target


def test_saves_only_passed(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)

    ok = zapret.save_probe_result("/zapret", [
        {"file": "general.bat", "ok": True},
        {"file": "general (ALT).bat", "ok": False},
        {"file": "general (ALT2).bat", "ok": True},
    ])

    assert ok is True
    data = zapret.load_probe_result("/zapret")
    assert data["passed"] == ["general.bat", "general (ALT2).bat"]
    assert data["at"] > 0


def test_nothing_passed_is_not_saved(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)

    assert zapret.save_probe_result("/zapret", [{"file": "general.bat", "ok": False}]) is False
    assert zapret.load_probe_result("/zapret") is None


def test_cache_is_bound_to_folder(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    zapret.save_probe_result("/one", [{"file": "a.bat", "ok": True}])

    assert zapret.load_probe_result("/two") is None, "чужой папке чужой кэш"
    assert zapret.load_probe_result("/one")["passed"] == ["a.bat"]
    assert zapret.load_probe_result(None) is None


def test_broken_cache_is_ignored(monkeypatch, tmp_path):
    target = use_tmp(monkeypatch, tmp_path)
    target.write_text("{ не json", encoding="utf-8")

    assert zapret.load_probe_result("/zapret") is None


def test_cache_with_empty_list_is_ignored(monkeypatch, tmp_path):
    target = use_tmp(monkeypatch, tmp_path)
    target.write_text('{"root": "/zapret", "passed": []}', encoding="utf-8")

    assert zapret.load_probe_result("/zapret") is None
