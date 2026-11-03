# -*- coding: utf-8 -*-
"""Диагностика монитора: DDC/CI и частоты попадают в отчёт и не роняют прогон."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_diagnose  # noqa: E402


def test_display_check_is_part_of_report():
    keys = [item["key"] for item in gd_diagnose.checks()]

    assert "display" in keys, "монитор обязан быть в диагностике"


def test_display_check_reports_brightness_or_reason():
    item = gd_diagnose.check_display()

    assert item["status"] in (gd_diagnose.OK, gd_diagnose.WARN, gd_diagnose.SKIP)
    assert item["detail"], "подробность обязана быть"
    assert "DDC" in item["detail"] or "частот" in item["detail"]


def test_display_check_survives_broken_dxva2(monkeypatch):
    import gd_ddc

    monkeypatch.setattr(gd_ddc, "Ddc", lambda: (_ for _ in ()).throw(RuntimeError("нет dxva2")))

    item = gd_diagnose.check_display()

    assert item["status"] == gd_diagnose.WARN
    assert "ошибка" in item["detail"]


def test_report_text_contains_monitor_line():
    text = gd_diagnose.report_text(gd_diagnose.checks(), ascii_only=True)

    assert "Монитор" in text or "Monitor" in text
