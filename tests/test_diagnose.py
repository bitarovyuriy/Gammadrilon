# -*- coding: utf-8 -*-
"""Диагностика: обезличивание отчёта и устойчивость проверок."""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("status, exit_code", [(gd_diagnose.OK, 0), (gd_diagnose.FAIL, 1)])
def test_selftest_writes_utf8_to_redirected_stdout(monkeypatch, status, exit_code):
    import main

    items = [{"title": "Запись данных", "status": status, "detail": "Доступна"}]
    monkeypatch.setattr(gd_diagnose, "checks", lambda: items)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["Gammadrilon.exe", "--selftest"])

    with io.BytesIO() as stream, io.TextIOWrapper(stream, encoding="cp1251") as stdout:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "stdout", stdout)
            with pytest.raises(SystemExit) as raised:
                main.main()
        stdout.flush()
        report = stream.getvalue().decode("utf-8")

    assert raised.value.code == exit_code
    assert "Запись данных: Доступна" in report
    assert f"провалов: {exit_code} из 1" in report


def test_selftest_writes_report_without_stdout(monkeypatch, tmp_path):
    import main

    items = [{"title": "Запись данных", "status": gd_diagnose.FAIL, "detail": "Недоступна"}]
    report = tmp_path / "отчёт.txt"
    monkeypatch.setattr(gd_diagnose, "checks", lambda: items)
    monkeypatch.setenv("GD_SELFTEST_REPORT", str(report))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["Gammadrilon.exe", "--selftest"])

    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", None)
        with pytest.raises(SystemExit) as raised:
            main.main()

    assert raised.value.code == 1
    assert "Запись данных: Недоступна" in report.read_text(encoding="utf-8")
