# -*- coding: utf-8 -*-
"""Диагностика: обезличивание отчёта и устойчивость проверок."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_diagnose  # noqa: E402


def test_anonymize_hides_user_ip_and_mac():
    user = os.environ.get("USERNAME") or "user"
    text = f"{user} 192.168.1.5 127.0.0.1 AA:BB:CC:DD:EE:FF"

    out = gd_diagnose.anonymize(text)

    assert "<USER>" in out, "имя пользователя не должно уходить в отчёт"
    assert "192.168.1.5" not in out
    assert "127.0.0.1" in out, "локальный адрес — суть проверки, его оставляем"
    assert "<MAC>" in out


def test_report_text_shows_status_marks():
    items = [
        {"key": "a", "title": "Первое", "status": gd_diagnose.OK, "detail": "всё хорошо", "action": ""},
        {"key": "b", "title": "Второе", "status": gd_diagnose.FAIL, "detail": "сломано",
         "action": "починить"},
    ]

    text = gd_diagnose.report_text(items)

    assert "✓ Первое" in text
    assert "× Второе" in text and "починить" in text


def test_checks_cover_key_sections_and_never_raise():
    items = gd_diagnose.checks()
    keys = {item["key"] for item in items}

    assert {"windows", "admin", "mode", "write", "gpu", "sensors",
            "dotnet", "zapret", "proxy", "uac"} <= keys
    for item in items:
        assert item["status"] in (gd_diagnose.OK, gd_diagnose.WARN,
                                  gd_diagnose.FAIL, gd_diagnose.SKIP), item
        assert item["title"] and item["detail"] is not None
