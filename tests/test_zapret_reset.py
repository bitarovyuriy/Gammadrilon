# -*- coding: utf-8 -*-
"""Сброс залипшего zapret: процессы, служба, драйвер WinDivert.

Настоящие sc и winws.exe не трогаем — подменяем границы: список процессов,
состояния служб и запуск sc.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_zapret  # noqa: E402


class FakePsutil:
    """psutil.Process: завершается сразу, если попросили."""

    class Error(Exception):
        pass

    class TimeoutExpired(Exception):
        pass

    def __init__(self, state: dict) -> None:
        self.state = state

    def Process(self, pid):  # noqa: N802
        state = self.state

        class Proc:
            def terminate(self_inner):
                state["alive"] = False

            def wait(self_inner, timeout=None):
                return 0

            def kill(self_inner):
                state["alive"] = False

        return Proc()

    Error = Error
    TimeoutExpired = TimeoutExpired


def test_nothing_stuck_reports_cleanly(monkeypatch):
    monkeypatch.setattr(gd_zapret, "processes", lambda: [])
    states = {"zapret": "STOPPED", "WinDivert": "STOPPED", "WinDivert14": None}
    monkeypatch.setattr(gd_zapret, "service_state", lambda name: states.get(name))
    monkeypatch.setattr(gd_zapret, "_run_sc", lambda args, timeout=20.0: (0, ""))
    monkeypatch.setattr(gd_zapret, "_service_wait", lambda target, timeout=15.0: True)

    ok, report = gd_zapret.reset_stuck(timeout=1)

    assert ok is True
    assert "залипшего состояния не было" in report


def test_stuck_service_is_stopped(monkeypatch):
    monkeypatch.setattr(gd_zapret, "processes", lambda: [])
    calls: list[list[str]] = []
    states = {"zapret": "STOP_PENDING", "WinDivert": None, "WinDivert14": None}

    def fake_state(name):
        return states.get(name)

    def fake_sc(args, timeout=20.0):
        calls.append(list(args))
        if args[:2] == ["stop", "zapret"]:
            states["zapret"] = "STOPPED"
        return 0, ""

    monkeypatch.setattr(gd_zapret, "service_state", fake_state)
    monkeypatch.setattr(gd_zapret, "_run_sc", fake_sc)
    monkeypatch.setattr(gd_zapret, "_service_wait", lambda target, timeout=15.0: True)

    ok, report = gd_zapret.reset_stuck(timeout=1)

    assert ok is True
    assert ["stop", "zapret"] in calls
    assert "служба zapret остановлена" in report


def test_live_winws_is_terminated(monkeypatch):
    state = {"alive": True}
    monkeypatch.setattr(gd_zapret, "processes",
                        lambda: [{"pid": 4242}] if state["alive"] else [])
    monkeypatch.setitem(sys.modules, "psutil", FakePsutil(state))
    monkeypatch.setattr(gd_zapret, "service_state", lambda name: None)
    monkeypatch.setattr(gd_zapret, "_run_sc", lambda args, timeout=20.0: (0, ""))

    ok, report = gd_zapret.reset_stuck(timeout=1, kill_alive=True)

    assert state["alive"] is False
    assert ok is True
    assert "4242 остановлен" in report


def test_live_winws_is_left_alone_by_default(monkeypatch):
    """Чужой рабочий обход убивать нельзя: у человека пропадёт интернет."""
    monkeypatch.setattr(gd_zapret, "processes", lambda: [{"pid": 777}])
    monkeypatch.setattr(gd_zapret, "service_state", lambda name: None)
    monkeypatch.setattr(gd_zapret, "_run_sc", lambda args, timeout=20.0: (0, ""))

    ok, report = gd_zapret.reset_stuck(timeout=1)

    assert "777 работает — не трогаем" in report
    assert ok is False, "живой процесс — это не «чисто»"


def test_driver_in_delete_pending_asks_for_reboot(monkeypatch):
    monkeypatch.setattr(gd_zapret, "processes", lambda: [])
    monkeypatch.setattr(gd_zapret, "service_state",
                        lambda name: "DELETE_PENDING" if name == "WinDivert" else None)
    monkeypatch.setattr(gd_zapret, "_run_sc", lambda args, timeout=20.0: (0, ""))

    ok, report = gd_zapret.reset_stuck(timeout=0.2)

    assert ok is False, "драйвер не выгрузился — состояние не чистое"
    assert "перезагрузка" in report


def test_stuck_names_lists_process_and_service(monkeypatch):
    monkeypatch.setattr(gd_zapret, "processes", lambda: [{"pid": 1}, {"pid": 2}])
    monkeypatch.setattr(gd_zapret, "service_state",
                        lambda name: "STOP_PENDING" if name == "zapret" else None)

    names = gd_zapret.stuck_names()

    assert any("winws.exe ×2" in line for line in names)
    assert any("zapret" in line for line in names)
