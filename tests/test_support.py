# -*- coding: utf-8 -*-
"""Архив для поддержки: что внутри и что не утекает."""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_paths  # noqa: E402
import gd_support  # noqa: E402


def prepare(monkeypatch, tmp_path: Path) -> Path:
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    data.mkdir()
    logs.mkdir()
    monkeypatch.setattr(gd_paths, "data_dir", lambda: data)
    monkeypatch.setattr(gd_paths, "log_dir", lambda: logs)
    monkeypatch.setattr(gd_paths, "portable_enabled", lambda: False)
    (logs / "gammadrilon.log").write_text("строка лога\n" * 20, encoding="utf-8")
    (data / "journal.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    (data / "rules_log.jsonl").write_text('{"b": 2}\n', encoding="utf-8")
    return logs


def test_archive_contains_diagnostics_logs_and_info(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)

    ok, path = gd_support.build(destination=tmp_path / "support.zip",
                                items=[{"key": "windows", "title": "Windows",
                                        "status": "ok", "detail": "Windows 11",
                                        "action": ""}])

    assert ok is True, path
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert {"diagnose.txt", "diagnose.json", "info.txt"} <= names
        assert "gammadrilon.log" in names
        assert "journal.jsonl" in names and "rules_log.jsonl" in names
        text = archive.read("diagnose.txt").decode("utf-8")
        assert "Windows" in text
        info = archive.read("info.txt").decode("utf-8")
        assert "портативно" in info


def test_archive_skips_missing_logs(monkeypatch, tmp_path):
    logs = prepare(monkeypatch, tmp_path)
    (logs / "gammadrilon.log").unlink()

    ok, path = gd_support.build(destination=tmp_path / "support.zip",
                                items=[{"key": "x", "title": "X", "status": "ok",
                                        "detail": "d", "action": ""}])

    assert ok is True
    with zipfile.ZipFile(path) as archive:
        assert "gammadrilon.log" not in archive.namelist()


def test_archive_tail_is_limited(monkeypatch, tmp_path):
    logs = prepare(monkeypatch, tmp_path)
    big = logs / "gammadrilon.log"
    big.write_text("A" * (gd_support.TAIL_BYTES + 5000), encoding="utf-8")

    ok, path = gd_support.build(destination=tmp_path / "support.zip",
                                items=[{"key": "x", "title": "X", "status": "ok",
                                        "detail": "d", "action": ""}])

    assert ok is True
    with zipfile.ZipFile(path) as archive:
        data = archive.read("gammadrilon.log")
    assert len(data) <= gd_support.TAIL_BYTES


def test_archive_reports_failure_instead_of_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(gd_paths, "log_dir", lambda: tmp_path / "нет-такой" / "глубже")

    def boom():
        raise RuntimeError("нарочно")

    import gd_diagnose

    monkeypatch.setattr(gd_diagnose, "checks", boom)

    ok, message = gd_support.build(destination=Path("Z:/нет/доступа/support.zip"))

    assert ok is False
    assert "нарочно" in message or "Error" in message
