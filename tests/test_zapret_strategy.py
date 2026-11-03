# -*- coding: utf-8 -*-
"""Проба стратегий zapret: разбор .bat, запуск winws.exe, TLS, уборка.

Настоящий winws.exe не запускаем: перехват трафика в тестах недопустим.
Проверяем разбор аргументов на реальном файле стратегии и логику пробы
с подменённым процессом и подменённой TLS-проверкой.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_zapret as zapret  # noqa: E402


def test_bat_lines_are_glued_back():
    text = "start winws.exe --a=1 ^\n--b=2 ^\n--c=3\nnext line\n"
    lines = zapret._bat_logical_lines(text)  # noqa: SLF001 — проверяем склейку

    assert lines[0] == "start winws.exe --a=1 --b=2 --c=3"
    assert lines[1] == "next line"


def test_parse_argv_of_real_strategy():
    root = zapret.guess_path()
    argv = zapret.parse_bat_argv(root, "general.bat")

    assert argv, "стратегия general.bat должна разбираться"
    assert argv[0].endswith("winws.exe")
    assert argv[1].startswith("--wf-tcp=")
    assert not any("%" in part for part in argv), "переменные .bat обязаны подставляться"
    assert not any('"' in part for part in argv), "кавычки не должны попадать в аргументы"


def test_parse_argv_of_missing_file_is_none():
    assert zapret.parse_bat_argv(zapret.guess_path(), "нет-такого.bat") is None
    assert zapret.parse_bat_argv(None, "general.bat") is None


def test_bat_without_winws_is_not_a_strategy(tmp_path):
    file = tmp_path / "general.bat"
    file.write_text("@echo off\necho привет\n", encoding="utf-8")

    assert zapret.parse_bat_argv(str(tmp_path), "general.bat") is None


class FakeProc:
    def __init__(self, exit_code=None) -> None:
        self.exit_code = exit_code
        self.terminated = False

    def poll(self):
        return 6 if self.terminated else self.exit_code

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


def test_probe_starts_and_stops_with_cleanup(monkeypatch, tmp_path):
    (tmp_path / "general.bat").write_text(
        '@echo off\r\nstart "z" /min "%BIN%winws.exe" --wf-tcp=80,443\r\n', encoding="utf-8")
    (tmp_path / "bin").mkdir()

    proc = FakeProc()
    started = []
    resets = []

    monkeypatch.setattr(zapret, "processes", lambda: [])
    monkeypatch.setattr(zapret.gd_proc, "popen",
                        lambda args, cwd=None: started.append(args) or proc)
    monkeypatch.setattr(zapret, "reset_stuck",
                        lambda timeout=8: resets.append(timeout) or (True, ""))
    monkeypatch.setattr(zapret, "tls_ok",
                        lambda host=zapret.PROBE_HOST, timeout=5.0: (True, "TLSv1.3"))

    result = zapret.probe_strategy(str(tmp_path), "general.bat", seconds=0.1)

    assert result["ok"] is True
    assert "TLS прошёл" in result["detail"]
    assert started and started[0][1] == "--wf-tcp=80,443"
    assert proc.terminated is True, "winws обязан быть остановлен после пробы"
    assert resets, "перед и после пробы нужен сброс залипшего драйвера"


def test_probe_reports_process_that_died(monkeypatch, tmp_path):
    (tmp_path / "general.bat").write_text("start winws.exe --x=1\n", encoding="utf-8")
    (tmp_path / "bin").mkdir()

    monkeypatch.setattr(zapret, "processes", lambda: [])
    monkeypatch.setattr(zapret.gd_proc, "popen", lambda args, cwd=None: FakeProc(exit_code=5))
    monkeypatch.setattr(zapret, "reset_stuck", lambda timeout=8: (True, ""))
    monkeypatch.setattr(zapret, "tls_ok", lambda *args, **kwargs: (True, "TLSv1.3"))

    result = zapret.probe_strategy(str(tmp_path), "general.bat", seconds=0.05)

    assert result["ok"] is False
    assert "кодом 5" in result["detail"]


def test_probe_honours_failed_tls(monkeypatch, tmp_path):
    (tmp_path / "general.bat").write_text("start winws.exe --x=1\n", encoding="utf-8")
    (tmp_path / "bin").mkdir()

    monkeypatch.setattr(zapret, "processes", lambda: [])
    monkeypatch.setattr(zapret.gd_proc, "popen", lambda args, cwd=None: FakeProc())
    monkeypatch.setattr(zapret, "reset_stuck", lambda timeout=8: (True, ""))
    monkeypatch.setattr(zapret, "tls_ok", lambda *args, **kwargs: (False, "TimeoutError"))

    result = zapret.probe_strategy(str(tmp_path), "general.bat", seconds=0.05)

    assert result["ok"] is False
    assert "TLS не прошёл" in result["detail"]


def test_probe_round_respects_limit_and_reports_progress(monkeypatch, tmp_path):
    for name in ("general.bat", "general (ALT).bat", "general (ALT2).bat"):
        (tmp_path / name).write_text("start winws.exe --x=1\n", encoding="utf-8")
    (tmp_path / "bin").mkdir()

    monkeypatch.setattr(zapret, "processes", lambda: [])
    monkeypatch.setattr(zapret.gd_proc, "popen", lambda args, cwd=None: FakeProc())
    monkeypatch.setattr(zapret, "reset_stuck", lambda timeout=8: (True, ""))
    monkeypatch.setattr(zapret, "tls_ok", lambda *args, **kwargs: (True, "TLSv1.3"))

    seen = []
    results = zapret.probe_round(
        str(tmp_path), seconds=0.01, limit=2,
        on_progress=lambda i, total, res: seen.append((i, total, res["ok"])))

    assert len(results) == 2
    assert seen == [(1, 2, True), (2, 2, True)]
