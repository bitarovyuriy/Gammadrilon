"""Regression coverage for the power companion lifecycle without powercfg."""
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
from threading import Event
import time
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import gd_power_service as service
import gd_power_ui


def test_duplicate_service_leaves_owner_profile_alone(monkeypatch):
    monkeypatch.setattr(service, "Listener", Mock(side_effect=OSError))
    read_state = Mock(return_value=({"profile": "normal"}, ""))
    rollback = Mock()
    monkeypatch.setattr(service.gp, "read_state_report", read_state)
    monkeypatch.setattr(service, "_rollback", rollback)

    assert service.PowerService(0, "test").run() == 2
    read_state.assert_not_called()
    rollback.assert_not_called()


def test_shutdown_replies_with_rollback_failure(monkeypatch):
    calls = []
    report = {"ok": False, "note": "rollback failed"}
    conn = Mock()
    monkeypatch.setattr(service.gd_helper, "recv_message", lambda conn: {"cmd": "shutdown"})
    monkeypatch.setattr(service.gd_helper, "send_message", lambda conn, reply: calls.append(reply))
    monkeypatch.setattr(service, "_rollback", lambda why: calls.append("restore") or report)
    monkeypatch.setattr(service.os, "_exit", lambda code: calls.append("exit"))

    service.PowerService(0, "test")._serve_conn(conn)

    assert calls == ["restore", {"ok": False, "data": report, "error": "rollback failed"}, "exit"]


def test_watchdog_waits_for_inflight_apply_and_rejects_late_apply(monkeypatch):
    companion = service.PowerService(123, "test")
    applying, release, dead, restored = (Event() for _ in range(4))

    def apply(*args):
        applying.set()
        assert release.wait(5)
        return {"profile": "normal"}

    def process_alive(pid):
        dead.set()
        return False

    def restore(why):
        restored.set()
        return {"ok": True}

    monkeypatch.setattr(service.gp, "apply_profile", apply)
    monkeypatch.setattr(service, "is_process_alive", process_alive)
    monkeypatch.setattr(service, "_rollback", restore)
    monkeypatch.setattr(service, "WATCHDOG_INTERVAL", 0)
    monkeypatch.setattr(service.os, "_exit", Mock())
    with ThreadPoolExecutor(2) as workers:
        work = workers.submit(service.handle, {"cmd": "apply"}, stopping=companion.stop)
        try:
            assert applying.wait(5)
            watchdog = workers.submit(companion._watchdog)
            assert dead.wait(5)
            assert not restored.wait(0.1)
        finally:
            release.set()
        assert work.result(timeout=5)["ok"]
        watchdog.result(timeout=5)
    assert restored.is_set()
    assert not service.handle({"cmd": "apply"}, stopping=companion.stop)["ok"]


def test_client_returns_shutdown_report_and_waits_for_restore(monkeypatch):
    client = gd_power_ui.PowerClient()
    response = {"ok": False, "error": "rollback failed"}
    conn = Mock()
    client._conn = conn
    monkeypatch.setattr(service.gd_helper, "send_message", Mock())
    monkeypatch.setattr(service.gd_helper, "recv_message", Mock(return_value=response))

    assert client.shutdown() == response
    conn.poll.assert_called_once_with(None)
    conn.close.assert_called_once_with()


def test_async_shutdown_waits_for_current_exchange(monkeypatch):
    client = gd_power_ui.PowerClient()
    current_poll, release, closed = Event(), Event(), Event()
    conn = Mock()
    client._conn = conn

    def poll(timeout):
        if timeout is not None:
            current_poll.set()
            assert release.wait(5)
        return True

    conn.poll.side_effect = poll
    conn.close.side_effect = closed.set
    send = Mock()
    monkeypatch.setattr(service.gd_helper, "send_message", send)
    monkeypatch.setattr(service.gd_helper, "recv_message", Mock(return_value={"ok": True}))

    with ThreadPoolExecutor(1) as workers:
        pending = workers.submit(client.call, {"cmd": "status"})
        try:
            assert current_poll.wait(5)
            client.shutdown_async()
            send.assert_called_once_with(conn, {"cmd": "status"})
        finally:
            release.set()
        assert pending.result(timeout=5)["ok"]
        assert closed.wait(5)

    assert [call.args[1]["cmd"] for call in send.call_args_list] == ["status", "shutdown"]
    assert not client.call({"cmd": "apply"})["ok"]


def test_local_recovery_serializes_requests_on_the_same_client(monkeypatch):
    client = gd_power_ui.PowerClient(pipe=rf"\\.\pipe\gd_power_recovery_{uuid4().hex}")
    recovering, release, ensuring = Event(), Event(), Event()

    def recover():
        recovering.set()
        assert release.wait(5)
        return {"ok": True}

    monkeypatch.setattr(service.gp, "restore_report", recover)
    monkeypatch.setattr(gd_power_ui, "Client", Mock(side_effect=OSError))
    ensure = Mock(side_effect=lambda: ensuring.set() or False)
    monkeypatch.setattr(client, "_ensure_locked", ensure)

    with ThreadPoolExecutor(2) as workers:
        recovery = workers.submit(client.recover_leftover)
        try:
            assert recovering.wait(5)
            applying = workers.submit(client.call, {"cmd": "apply"})
            assert not ensuring.wait(0.1)
        finally:
            release.set()
        assert recovery.result(timeout=5)["ok"]
        assert not applying.result(timeout=5)["ok"]
    ensure.assert_called_once_with()


def test_local_recovery_leaves_a_concurrently_started_service_alone(monkeypatch):
    pipe = rf"\\.\pipe\gd_power_recovery_{uuid4().hex}"
    client = gd_power_ui.PowerClient(pipe=pipe)
    restore = Mock()
    monkeypatch.setattr(service.gp, "restore_report", restore)
    monkeypatch.setattr(gd_power_ui, "Client", Mock(side_effect=OSError))

    with service.Listener(address=pipe, family="AF_PIPE"):
        assert client.recover_leftover() is None

    restore.assert_not_called()


def test_local_mutation_reserves_pipe_against_companion_start(monkeypatch):
    pipe = rf"\\.\pipe\gd_power_recovery_{uuid4().hex}"
    client = gd_power_ui.PowerClient(pipe=pipe)
    result = {"ok": True}

    def restore():
        assert service.PowerService(0, pipe).run() == 2
        return result

    monkeypatch.setattr(service.gp, "restore_report", restore)

    assert client.recover_leftover() == result


def test_power_ui_keeps_writes_blocked_during_automatic_rollback(monkeypatch):
    page = SimpleNamespace(
        _shutting_down=False,
        _apply_busy=False,
        _leftover_busy=True,
        _clones_busy=False,
    )
    page._mutation_busy = lambda: gd_power_ui.PowerWindow._mutation_busy(page)
    submit = Mock()
    monkeypatch.setattr(gd_power_ui.gd_bg, "submit", submit)

    gd_power_ui.PowerWindow._call_apply(page, {"cmd": "apply"})
    gd_power_ui.PowerWindow._restore(page)
    gd_power_ui.PowerWindow._clean_clones(page)

    submit.assert_not_called()


def test_watchdog_does_not_treat_access_denied_as_process_death(monkeypatch):
    monkeypatch.setattr(service, "_kernel32", SimpleNamespace(OpenProcess=lambda *_: None))
    monkeypatch.setattr(service.ctypes, "get_last_error", lambda: 5)
    assert service.is_process_alive(123)

    monkeypatch.setattr(service.ctypes, "get_last_error", lambda: service.ERROR_INVALID_PARAMETER)
    assert not service.is_process_alive(123)


def test_shutdown_report_survives_real_pipe_exit():
    pipe = rf"\\.\pipe\gd_power_shutdown_{uuid4().hex}"
    script = """
import sys
import gd_power_service as service

service.gp.read_state_report = lambda: (None, "")
service._rollback = lambda why: {"ok": False, "note": "rollback failed"}
sys.exit(service.PowerService(0, sys.argv[1]).run())
"""
    client = gd_power_ui.PowerClient(pipe=pipe)
    with subprocess.Popen([sys.executable, "-c", script, pipe]) as child:
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if client.call_existing({"cmd": "ping"}, timeout=0.25).get("ok"):
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("power service did not start")
            with ThreadPoolExecutor(1) as workers:
                shutdown = workers.submit(client.shutdown)
                try:
                    assert shutdown.result(timeout=5)["error"] == "rollback failed"
                finally:
                    if not shutdown.done():
                        child.kill()
            assert child.wait(timeout=5) == 0
        finally:
            if child.poll() is None:
                child.kill()
