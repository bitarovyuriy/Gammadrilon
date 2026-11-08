# -*- coding: utf-8 -*-
"""Профиль GPU применяется, даже если метрики ещё не собрались.

Раньше `_on_profile` при пустом снимке молча выходил: на старте приложения
клик по профилю ничего не делал — кнопка горела, лимит оставался прежним.
Теперь состояние берётся у драйвера, а если и он молчит — честный отказ.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gd_gpu  # noqa: E402
import gd_gpu_ui  # noqa: E402
import gd_metrics  # noqa: E402


class _Slider:
    def __init__(self):
        self.value = None

    def setValue(self, value):  # noqa: N802 — имя из Qt
        self.value = value


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):  # noqa: N802 — имя из Qt
        self.text = text


class _Page:
    """Заглушка вместо GpuWidget: проверяем только путь _on_profile."""

    def __init__(self):
        self._state = None
        self.sl_pl = _Slider()
        self.profile_desc = _Label()
        self._status = []
        self._applied = []

    def _set_status(self, text, ok=True, color=None):
        self._status.append((text, ok))

    def _apply(self, payload, profile_name=None, autostart=False):
        self._applied.append((payload, profile_name))
        return True


def _page(**attrs):
    page = _Page()
    for key, value in attrs.items():
        setattr(page, key, value)
    return page


def _on_profile(page, name):
    return gd_gpu_ui.GpuWidget._on_profile(page, name)


def test_profile_applies_when_metrics_are_empty(monkeypatch):
    state = {"pl": 300.0, "pl_min": 100.0, "pl_max": 366.0, "temp": 32}
    monkeypatch.setattr(gd_metrics, "gpu", lambda: None)
    monkeypatch.setattr(gd_metrics, "refresh_now", lambda what=None: None)
    monkeypatch.setattr(gd_gpu, "read_state", lambda: state)
    page = _page()

    result = _on_profile(page, "game")

    assert result is True
    assert page._applied == [({"pl": 340}, "game")]
    assert page.sl_pl.value == 340


def test_profile_reports_when_driver_is_silent(monkeypatch):
    monkeypatch.setattr(gd_metrics, "gpu", lambda: None)
    monkeypatch.setattr(gd_metrics, "refresh_now", lambda what=None: None)
    monkeypatch.setattr(gd_gpu, "read_state", lambda: None)
    page = _page()

    result = _on_profile(page, "max")

    assert result is False
    assert page._applied == []
    assert page._status and page._status[0][1] is False


def test_busy_apply_is_not_reported_as_success(monkeypatch):
    state = {"pl": 300.0, "pl_min": 100.0, "pl_max": 366.0}
    monkeypatch.setattr(gd_metrics, "gpu", lambda: state)
    monkeypatch.setattr(gd_gpu, "read_state", lambda: state)
    monkeypatch.setattr(gd_gpu_ui.gd_bg, "is_running", lambda worker: True)
    page = _page()
    page._apply = gd_gpu_ui.GpuWidget._apply.__get__(page, gd_gpu_ui.GpuWidget)
    page.btn_apply = type("B", (), {"setEnabled": lambda self, v: None})()
    page._apply_worker = None

    result = gd_gpu_ui.GpuWidget.apply_profile(page, "quiet")
    assert result is False
