"""Exercise GPU transaction failures and helper shutdown without driver writes."""
import json
from concurrent.futures import ThreadPoolExecutor
import sys
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import gd_gpu_service as service
import gd_helper
import gd_nvml

GPU = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_GPU = "GPU-aaaaaaaa-bbbb-cccc-dddd-ffffffffffff"


@pytest.fixture
def snapshot(monkeypatch, tmp_path):
    path = tmp_path / "gpu.json"
    monkeypatch.setattr(service, "_state_path", lambda: path)
    monkeypatch.setattr(service, "_read_keep_setting", lambda default=True: default)
    monkeypatch.setattr(service, "_keep_on_exit", False)
    monkeypatch.setattr(service, "_original", None)
    monkeypatch.setattr(service, "_applied", None)
    monkeypatch.setattr(service, "_snapshot_problem", False)
    monkeypatch.setattr(service, "_stopping", False)
    monkeypatch.setattr(service.gd_gpu, "read_state", lambda: {"pl": 200.123, "uuid": GPU})
    return path


def test_snapshot_must_exist_before_driver_write_and_survives_timeout(monkeypatch, snapshot):
    def write_device(uuid, value):
        assert (uuid, value) == (GPU, 100)
        original = json.loads(snapshot.read_text(encoding="utf-8"))["original"]
        assert original == {"pl": 200.123, "uuid": GPU}
        raise service.gd_proc.TimeoutExpired("nvidia-smi", 20)

    monkeypatch.setattr(service, "_set_power", write_device)
    assert not service.handle({"cmd": "apply", "pl": 100})["ok"]
    assert snapshot.exists()
    service._load_kept_original()
    restored = Mock(return_value=(True, ""))
    monkeypatch.setattr(service, "_set_power", restored)
    assert service.handle({"cmd": "restore"})["ok"]
    restored.assert_called_once_with(GPU, 200.123)
    assert not snapshot.exists()


def test_no_write_if_rollback_snapshot_cannot_be_saved(monkeypatch, snapshot):
    monkeypatch.setattr(service.gd_state, "write_json_atomic", lambda *a: False)
    write = Mock(return_value=(True, ""))
    monkeypatch.setattr(service, "_set_power", write)
    assert not service.handle({"cmd": "apply", "pl": 100})["ok"]
    write.assert_not_called()
    assert not snapshot.exists()


def test_clock_only_snapshot_never_restores_power(monkeypatch, snapshot):
    monkeypatch.setattr(service.gd_gpu, "read_state", lambda: {"pl": None, "uuid": GPU})
    power = Mock()
    smi = Mock(return_value=(True, ""))
    monkeypatch.setattr(service, "_set_power", power)
    monkeypatch.setattr(service, "_run_smi", smi)
    assert not service.handle({"cmd": "apply", "pl": 100})["ok"]
    assert service.handle({"cmd": "apply", "lock_clocks": [900, 1200]})["ok"]
    service._load_kept_original()
    assert service.handle({"cmd": "restore"})["ok"]
    power.assert_not_called()
    assert smi.call_args_list[0].args == (GPU, ["-lgc", "900,1200"])
    assert smi.call_args_list[1].args == (GPU, ["-rgc"])


def test_snapshot_is_not_applied_to_different_gpu_after_restart(monkeypatch, snapshot):
    power = Mock(return_value=(True, ""))
    monkeypatch.setattr(service, "_set_power", power)
    assert service.handle({"cmd": "apply", "pl": 100})["ok"]
    service._load_kept_original()
    monkeypatch.setattr(service.gd_gpu, "read_state", lambda: {"pl": 300, "uuid": OTHER_GPU})
    current = snapshot.read_bytes()
    assert not service.handle({"cmd": "apply", "pl": 150})["ok"]
    assert snapshot.read_bytes() == current
    power.side_effect = [(False, "GPU not found"), (True, "")]
    assert not service.handle({"cmd": "restore"})["ok"]
    assert snapshot.exists()
    assert service.handle({"cmd": "restore"})["ok"]
    assert [call.args for call in power.call_args_list] == [(GPU, 100), (GPU, 200.123), (GPU, 200.123)]


@pytest.mark.parametrize("contents", [
    '{"original_pl": 200}',
    '{"original":{"pl":200}}',
    '{"original_pl":null,"original":{"pl":null,"clocks_locked":false},"applied":{"pl":150}}',
    '{broken',
])
def test_ambiguous_or_corrupt_snapshot_is_left_for_manual_recovery(monkeypatch, snapshot, contents):
    snapshot.write_text(contents, encoding="utf-8")
    assert service._load_kept_original() is None
    power = Mock()
    monkeypatch.setattr(service, "_set_power", power)
    for command in ({"cmd": "apply", "pl": 150}, {"cmd": "restore"}):
        result = service.handle(command)
        assert not result["ok"]
        assert str(snapshot) in result["error"]
    power.assert_not_called()
    assert snapshot.read_text(encoding="utf-8") == contents


def test_smi_fallback_uses_same_uuid_and_preserves_fractional_limit(monkeypatch):
    nvml = Mock(return_value=SimpleNamespace(available=lambda: False, shutdown=lambda: None))
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(gd_nvml, "Nvml", nvml)
    monkeypatch.setattr(service.gd_gpu, "_smi_path", lambda: "nvidia-smi.exe")
    monkeypatch.setattr(service.gd_proc, "run", run)
    assert service._set_power(GPU, 200.123)[0]
    nvml.assert_called_once_with(GPU)
    assert run.call_args.args[0] == ["nvidia-smi.exe", "-i", GPU, "-pl", "200.123"]


def test_nvml_cleanup_error_does_not_retry_a_confirmed_write(monkeypatch, snapshot):
    nvml = Mock()
    nvml.available.return_value = True
    nvml.set_power_limit.return_value = (True, "")
    nvml.shutdown.side_effect = OSError("NVML cleanup failure")
    smi = Mock()
    monkeypatch.setattr(gd_nvml, "Nvml", lambda uuid: nvml)
    monkeypatch.setattr(service, "_run_smi", smi)

    assert service.handle({"cmd": "apply", "pl": 150})["ok"] is True
    nvml.set_power_limit.assert_called_once_with(150)
    smi.assert_not_called()
    assert snapshot.exists()


def test_driver_error_after_write_leaves_original_limit_for_restore(monkeypatch, snapshot):
    limit = 200.123

    def set_power(uuid, value):
        nonlocal limit
        assert uuid == GPU
        limit = value
        return (False, "driver error") if value == 150 else (True, "")

    monkeypatch.setattr(service, "_set_power", set_power)
    assert service.handle({"cmd": "apply", "pl": 150})["ok"] is None
    assert limit == 150
    service._load_kept_original()
    assert service.handle({"cmd": "restore"})["ok"] is True
    assert limit == 200.123
    assert not snapshot.exists()


def test_missing_clock_tool_drops_snapshot_if_no_write_started(monkeypatch, snapshot):
    monkeypatch.setattr(service.gd_gpu, "_smi_path", lambda: None)
    assert service.handle({"cmd": "apply", "lock_clocks": [900, 1200]})["ok"] is False
    assert not snapshot.exists()


@pytest.mark.parametrize("restore", [True, False])
def test_shutdown_obeys_explicit_restore_over_stale_flag(monkeypatch, snapshot, restore):
    monkeypatch.setattr(service, "_keep_on_exit", restore)
    rollback = Mock(return_value=(True, ""))
    monkeypatch.setattr(service, "_restore", rollback)
    assert service.handle({"cmd": "shutdown", "restore": restore})["ok"] is True
    assert rollback.call_count == int(restore)


def test_shutdown_waits_for_active_write_and_rejects_writes_before_exit(monkeypatch, snapshot):
    applying, release, replying, sent = Event(), Event(), Event(), Event()
    reports = []

    def apply(*args):
        applying.set()
        assert release.wait(5)
        return True, ""

    def send(conn, reply):
        reports.append(reply)
        replying.set()
        assert sent.wait(5)

    conn = Mock()
    restore = Mock(return_value=(True, ""))
    exit_ = Mock()
    monkeypatch.setattr(service, "_apply", apply)
    monkeypatch.setattr(service, "_restore", restore)
    monkeypatch.setattr(service, "_stop_boost", Mock(side_effect=RuntimeError("boost error")))
    monkeypatch.setattr(service, "_zapret_shutdown", Mock(return_value=(False, "DPI error")))
    monkeypatch.setattr(service, "recv_message", lambda _: {"cmd": "shutdown"})
    monkeypatch.setattr(service, "send_message", send)
    monkeypatch.setattr(service.os, "_exit", exit_)
    with ThreadPoolExecutor(2) as pool:
        before = pool.submit(service.handle, {"cmd": "apply", "pl": 100})
        assert applying.wait(3)
        closing = pool.submit(service.serve_client, conn)
        try:
            assert not replying.wait(0.05)
            restore.assert_not_called()
            release.set()
            assert before.result(3)["ok"]
            assert replying.wait(3)
            assert not service.handle({"cmd": "apply", "pl": 100})["ok"]
            restore.assert_called_once_with()
            exit_.assert_not_called()
        finally:
            release.set()
            sent.set()
        closing.result(3)
    assert reports == [{"ok": False, "data": "bye", "error": "boost error; DPI error"}]
    exit_.assert_called_once_with(0)


def test_shutdown_client_waits_for_reply_and_distinguishes_unconfirmed_outcome(monkeypatch):
    client = gd_helper.HelperClient()
    conn = client._conn = Mock()
    report = {"ok": False, "error": "driver failure"}
    received = Mock(side_effect=[report, EOFError])
    sent = Mock()
    monkeypatch.setattr(gd_helper, "send_message", sent)
    monkeypatch.setattr(gd_helper, "recv_message", received)
    assert client.shutdown() == report
    assert client._conn is None
    client._conn = conn
    assert client.shutdown()["ok"] is None
    assert client._conn is None
    conn.poll.assert_not_called()
    assert conn.close.call_count == 2
    assert sent.call_args.args == (conn, {"cmd": "shutdown", "restore": True, "force": False})


def test_failed_listener_does_not_touch_another_companions_rollback(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["service", "--ui-pid", "1234", "--token", "aa"])
    monkeypatch.setattr(service, "Listener", Mock(side_effect=OSError))
    owner = Mock()
    load = Mock()
    monkeypatch.setattr(service, "_owner_handle", owner)
    monkeypatch.setattr(service, "_load_kept_original", load)
    assert service.main() == 2
    owner.assert_not_called()
    load.assert_not_called()


def test_watchdog_closes_owner_handle_and_calls_same_shutdown(monkeypatch, snapshot):
    owner = object()
    k32 = Mock()
    k32.WaitForSingleObject.return_value = 0
    restore = Mock(return_value=(True, ""))
    exit_ = Mock()
    monkeypatch.setattr(service, "_keep_on_exit", True)
    monkeypatch.setattr(service, "_read_keep_setting", lambda default: False)
    monkeypatch.setattr(service, "_restore", restore)
    monkeypatch.setattr(service.os, "_exit", exit_)
    service.watchdog(k32, owner)
    k32.WaitForSingleObject.assert_called_once_with(owner, 0xFFFFFFFF)
    k32.CloseHandle.assert_called_once_with(owner)
    restore.assert_called_once_with()
    exit_.assert_called_once_with(0)
    assert not service.handle({"cmd": "apply", "pl": 100})["ok"]
