# -*- coding: utf-8 -*-
"""Страховка от запуска чужим интерпретатором: понятный отказ вместо диалога Qt."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as gd_main  # noqa: E402


def test_current_environment_is_ready():
    ok, reason = gd_main._qt_ready()

    assert ok is True, reason


def test_missing_pyqt_is_explained(monkeypatch, tmp_path):
    import sysconfig

    monkeypatch.setattr(sysconfig, "get_paths",
                        lambda: {"purelib": str(tmp_path / "пусто")})

    ok, reason = gd_main._qt_ready()

    assert ok is False
    assert "PyQt5" in reason
    assert "requirements.txt" in reason


def test_missing_plugin_is_explained(monkeypatch, tmp_path):
    import sysconfig

    fake_site = tmp_path / "site"
    (fake_site / "PyQt5").mkdir(parents=True)
    monkeypatch.setattr(sysconfig, "get_paths", lambda: {"purelib": str(fake_site)})
    monkeypatch.delenv("QT_QPA_PLATFORM_PLUGIN_PATH", raising=False)

    ok, reason = gd_main._qt_ready()

    assert ok is False
    assert "qwindows.dll" in reason
