"""Регрессии критичных находок 3.6.2; внешние действия подменены."""
import json
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gd_bg  # noqa: E402
import gd_boost  # noqa: E402
import gd_cs2  # noqa: E402
import gd_gpu_service as service  # noqa: E402
import gd_helper  # noqa: E402
import gd_zapret_update as upd  # noqa: E402
import main  # noqa: E402


class BytesConnection:
    def __init__(self, payload):
        self.payload = payload
        self.sent = []

    def send_bytes(self, payload):
        self.sent.append(json.loads(payload))

    def recv_bytes(self, maxlength):
        assert maxlength <= 1024 * 1024
        result, self.payload = self.payload, None
        if result is None:
            raise EOFError
        return result

    def poll(self, timeout):
        return True

    def close(self):
        pass


def test_helper_uses_bounded_json():
    conn = BytesConnection(b'{"ok":true,"data":42}')
    client = gd_helper.HelperClient()
    client._conn = conn
    assert client._call_connected({"cmd": "ping"}, 1) == {"ok": True, "data": 42}
    assert conn.sent == [{"cmd": "ping"}]


def test_service_never_unpickles(monkeypatch):
    handle = Mock()
    monkeypatch.setattr(service, "handle", handle)
    conn = BytesConnection(b"\x80\x04cos\nsystem\n.")
    service.serve_client(conn)
    handle.assert_not_called()
    assert conn.sent and not conn.sent[0]["ok"]


def test_service_checks_actual_connection_owner(monkeypatch):
    import psutil

    conn = NS(laddr=("127.0.0.1", 51000), raddr=("127.0.0.1", 47318), pid=999)
    monkeypatch.setattr(psutil, "net_connections", lambda **kw: [conn])
    monkeypatch.setattr(psutil, "Process", lambda pid: NS(create_time=lambda: 123.0))
    assert not service._client_is_owner(("127.0.0.1", 51000), 47318, 123, 123.0)
    conn.pid = 123
    assert service._client_is_owner(("127.0.0.1", 51000), 47318, 123, 123.0)
    assert not service._client_is_owner(("127.0.0.1", 51000), 47318, 123, 122.0)


@pytest.mark.parametrize("payload", [
    {"cmd": "zapret_start", "path": "relative", "argv": []},
    {"cmd": "zapret_recon", "path": "C:/zapret", "strategy": "../run.bat"},
    {"cmd": "apply", "pl": "bad"},
    {"cmd": "zapret_service", "op": "unknown"},
])
def test_service_rejects_invalid_commands_before_dispatch(monkeypatch, payload):
    dispatch = Mock(return_value={"ok": True})
    monkeypatch.setattr(service, "_handle", dispatch)
    assert service.handle(payload)["ok"] is False
    dispatch.assert_not_called()


def test_launch_preserves_actual_argv(monkeypatch, tmp_path):
    import subprocess

    base = tmp_path / "Папка с пробелом O'Brien"
    base.mkdir()
    script = base / "agent.py"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(gd_helper, "HERE", str(base))
    monkeypatch.setattr(gd_helper, "LOG_FILE", str(base / "журнал агента.log"))
    commands = []
    monkeypatch.setattr(gd_helper.gd_proc, "popen", lambda argv: commands.append(argv))
    gd_helper.HelperClient(service="agent.py")._launch(1234, "aa" * 16)
    # Выполняем только разбор PowerShell, заменив Start-Process безопасной функцией.
    prelude = 'function Start-Process { param($FilePath,$ArgumentList,$Verb,$WindowStyle) $ArgumentList }\n'
    result = subprocess.run(["powershell", "-NoProfile", "-Command", prelude + commands[0][-1]],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    from ctypes import POINTER, byref, c_int, c_wchar_p, windll

    parse = windll.shell32.CommandLineToArgvW
    parse.restype = POINTER(c_wchar_p)
    count = c_int()
    args = parse('agent.exe ' + result.stdout.strip(), byref(count))
    try:
        argv = [args[i] for i in range(count.value)]
    finally:
        windll.kernel32.LocalFree(args)
    assert argv[1:3] == ["-u", str(script)]
    assert argv[argv.index("--log") + 1] == gd_helper.LOG_FILE


def test_restored_config_is_normalized(monkeypatch, tmp_path):
    target = tmp_path / "presets.json"
    target.write_text("broken", encoding="utf-8")
    target.with_suffix(".json.good").write_text('{"settings":{"theme":"light"}}', encoding="utf-8")
    monkeypatch.setattr(main, "config_path", lambda: target)
    monkeypatch.setattr(main.gd_paths, "migrate_legacy_config", lambda: None)
    data = main.load_config()
    assert data["settings"]["current"] == main.DEFAULT_CONFIG["settings"]["current"]
    assert data["presets"]
    assert json.loads(target.read_text(encoding="utf-8")) == data


@pytest.mark.parametrize("failure", ["power", "clocks"])
def test_gpu_restore_can_retry(monkeypatch, failure):
    monkeypatch.setattr(service, "_original", {"pl": 200})
    monkeypatch.setattr(service, "_applied", {"pl": 100, "lock_clocks": [900, 1200]})
    monkeypatch.setattr(service, "_clear_kept_original", Mock())
    monkeypatch.setattr(service, "_keep_original", Mock())
    attempts = {"power": 0, "clocks": 0}

    def run(part):
        attempts[part] += 1
        return (False, "permission denied") if part == failure and attempts[part] == 1 else (True, "ok")

    monkeypatch.setattr(service, "_set_power", lambda pl: run("power"))
    monkeypatch.setattr(service, "_run_smi", lambda args: run("clocks"))
    assert not service._restore()[0]
    assert service._original is not None
    assert service._restore()[0]
    assert service._original is None
    assert attempts[failure] == 2


def test_cs2_does_not_change_system_when_backup_fails(monkeypatch, tmp_path):
    registry = NS(read=lambda *a: None, write=Mock())
    power = NS(active=lambda: "balanced", set=Mock(return_value=True))
    monkeypatch.setattr(gd_cs2, "_save_backup", lambda *a: False)
    res = gd_cs2.apply(["game_mode", "power_plan"], registry=registry, power=power,
                       path=tmp_path / "backup.json")
    assert not res["ok"]
    registry.write.assert_not_called()
    power.set.assert_not_called()


def test_cs2_backup_exists_before_first_write(monkeypatch, tmp_path):
    path = tmp_path / "backup.json"

    def write(*args):
        assert gd_cs2._load_backup(path), "изменять систему до снимка нельзя"

    registry = NS(read=lambda *a: 0, write=write)
    assert gd_cs2.apply(["game_mode"], registry=registry, path=path)["ok"]


def test_ddc_coalesces_writes_without_confirming_pending(monkeypatch):
    jobs = []
    monkeypatch.setattr(main.gd_bg, "submit", lambda fn, done, **kw: jobs.append((fn, done)))
    monitor = NS(set_brightness=Mock(return_value=True))
    window = NS(_ddc_monitor=monitor, _ddc_pending=20, _ddc_last_ok=50, _ddc_busy=False)
    window._ddc_apply = lambda: main.MainWindow._ddc_apply(window)
    window._ddc_applied = lambda result: main.MainWindow._ddc_applied(window, result)
    window._ddc_apply()
    window._ddc_pending = 70
    window._ddc_apply()
    assert len(jobs) == 1
    jobs[0][1](jobs[0][0]())
    assert window._ddc_last_ok == 20
    assert len(jobs) == 2
    jobs[1][1](jobs[1][0]())
    assert window._ddc_last_ok == 70
    assert monitor.set_brightness.call_args_list[0].args == (20,)
    assert monitor.set_brightness.call_args_list[1].args == (70,)


def test_switching_ddc_discards_previous_monitor_pending(monkeypatch):
    jobs = []
    monkeypatch.setattr(gd_bg, "submit", lambda fn, done, **kw: jobs.append((fn, done)))
    old = NS(supported=True)
    new = NS(supported=True, brightness=lambda: 30, set_brightness=Mock(), description="new")
    window = NS(_ddc=NS(monitors=[old, new]), _ddc_monitor=old, _ddc_pending=70,
                _ddc_last_ok=20, _ddc_busy=True, config={}, _save_config=Mock(),
                ddc_slider=NS(setEnabled=Mock()), ddc_hint=object(), bind_text=Mock(), _ddc_level_read=Mock())
    window._ddc_applied = Mock()
    window._ddc_apply = lambda: main.MainWindow._ddc_apply(window)
    main.MainWindow.on_ddc_monitor_picked(window, 1)
    main.MainWindow._ddc_applied(window, (old, 20, True))
    assert len(jobs) == 1, "выбор монитора не переносит на него старую цель"
    new.set_brightness.assert_not_called()


def test_wait_all_keeps_running_workers(monkeypatch):
    worker = Mock()
    worker.isRunning.return_value = True
    worker.wait.return_value = False
    monkeypatch.setattr(gd_bg, "_WORKERS", {worker})
    assert gd_bg.wait_all(0) is False
    assert worker in gd_bg._WORKERS
    worker.isRunning.return_value = False
    assert gd_bg.wait_all(0) is True


@pytest.mark.parametrize("kind", ["corrupt", "traversal", "no_binary"])
def test_bad_rollback_preserves_working_install(tmp_path, kind):
    root = tmp_path / "zapret"
    (root / "bin").mkdir(parents=True)
    (root / "bin/winws.exe").write_bytes(b"working")
    backup = tmp_path / "backup.zip"
    if kind == "corrupt":
        backup.write_bytes(b"broken")
    else:
        with zipfile.ZipFile(backup, "w") as zf:
            zf.writestr("../escape.txt" if kind == "traversal" else "readme.txt", "bad")
    result = upd.rollback_update({"root": str(root), "backup": str(backup)})
    assert not result["ok"]
    assert (root / "bin/winws.exe").read_bytes() == b"working"
    assert not (tmp_path.parent / "escape.txt").exists()


def test_update_check_failure_does_not_stop_service(monkeypatch):
    import gd_zapret_ui as ui

    client = NS(call=Mock(return_value={"ok": True}))
    window = NS(_path=lambda: "C:/test", _remembered_strategy=lambda: "general.bat",
                _ensure=lambda: client)
    monkeypatch.setattr(ui.gd_zapret, "snapshot", lambda p: {"service_zapret": "RUNNING"})
    monkeypatch.setattr(ui.gd_zapret, "service_registry_strategy", lambda: "general.bat")
    monkeypatch.setattr(ui.upd, "check_updates", lambda *a, **kw: {"ok": False, "error": "offline"})
    assert not ui.ZapretWidget._job_update(window)["ok"]
    client.call.assert_not_called()


def test_release_keeps_sha256():
    digest = "sha256:" + "a" * 64
    release = upd._release_from_api({"assets": [{"digest": digest}]})
    assert release["assets"][0]["digest"] == digest


def test_helper_applies_the_verified_copy(monkeypatch, tmp_path):
    import hashlib

    archive = tmp_path / "release.zip"
    archive.write_bytes(b"official")
    digest = hashlib.sha256(b"official").hexdigest()
    protected = []
    monkeypatch.setattr(upd, "_protect_directory", lambda path: protected.append(path))
    monkeypatch.setattr(upd, "_privileged_temp_root", lambda: tmp_path, raising=False)

    def apply(root, copied, version):
        assert copied != archive
        assert copied.parent in protected
        archive.write_bytes(b"replaced after verification")
        assert copied.read_bytes() == b"official"
        return {"ok": True}

    monkeypatch.setattr(upd, "apply_update", apply)
    assert upd.apply_verified_update(tmp_path, archive, "2.0", digest)["ok"]


@pytest.mark.parametrize("url", ["http://github.com/Flowseal/zapret-discord-youtube/releases/download/v/a.zip",
                                 "https://evil.example/a.zip", "https://github.com/other/repo/releases/download/a.zip"])
def test_download_rejects_untrusted_sources(monkeypatch, tmp_path, url):
    opener = Mock()
    monkeypatch.setattr(upd.urllib.request, "build_opener", opener)
    assert not upd.download(url, tmp_path / "new.zip")[0]
    opener.assert_not_called()


def test_download_rejects_untrusted_redirect():
    with pytest.raises(ValueError):
        upd._ReleaseRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example/a.zip")


def test_helper_rejects_archive_substitution(monkeypatch, tmp_path):
    archive = tmp_path / "new.zip"
    archive.write_bytes(b"replacement")
    apply = Mock(return_value={"ok": True})
    monkeypatch.setattr(upd, "_protect_directory", lambda path: None)
    monkeypatch.setattr(upd, "_privileged_temp_root", lambda: tmp_path, raising=False)
    monkeypatch.setattr(upd, "apply_update", apply)
    result = service.handle({"cmd": "zapret_update", "op": "apply", "path": str(tmp_path),
                             "zip": str(archive), "sha256": "a" * 64, "version": "2.0"})
    assert not result["ok"]
    apply.assert_not_called()


def test_gpu_failed_clock_restore_survives_helper_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_state_path", lambda: tmp_path / "gpu.json")
    monkeypatch.setattr(service, "_read_keep_setting", lambda: False)
    monkeypatch.setattr(service, "_original", {"pl": 200})
    monkeypatch.setattr(service, "_applied", {"pl": 100, "lock_clocks": [900, 1200]})
    monkeypatch.setattr(service, "_set_power", lambda value: (True, ""))
    monkeypatch.setattr(service, "_run_smi", lambda args: (False, "permission denied"))
    assert not service._restore()[0]
    monkeypatch.setattr(service, "_original", None)
    monkeypatch.setattr(service, "_applied", None)
    service._load_kept_original()
    calls = []
    monkeypatch.setattr(service, "_run_smi", lambda args: calls.append(args) or (True, ""))
    assert service._restore()[0]
    assert calls == [["-rgc"]]


def test_gpu_partial_apply_retains_rollback_state(monkeypatch):
    monkeypatch.setattr(service, "_original", None)
    monkeypatch.setattr(service, "_applied", None)
    monkeypatch.setattr(service.gd_gpu, "read_state", lambda: {"pl": 200})
    monkeypatch.setattr(service, "_set_power", lambda value: (True, ""))
    monkeypatch.setattr(service, "_run_smi", lambda args: (False, "driver error"))
    monkeypatch.setattr(service, "_keep_original", Mock())
    assert not service._apply(100, [900, 1200], False)[0]
    assert service._original["pl"] == 200
    assert service._applied["pl"] == 100


def test_boost_keeps_failed_restores_for_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(gd_boost.gd_paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(gd_boost, "set_memory_priority", lambda *a: False)
    monkeypatch.setattr(gd_boost, "set_eco_qos", lambda *a: True)
    monkeypatch.setattr(gd_boost, "set_cpu_priority", lambda *a: True)
    boost = gd_boost.GameBoost()
    boost._active = True
    boost._snapshot = {"111": {"memory_priority": 5, "eco_qos": False}}
    assert not boost.stop()[0]
    assert "111" in boost._snapshot
    monkeypatch.setattr(gd_boost, "set_memory_priority", lambda *a: True)
    assert boost.stop()[0]


def test_reset_gamma_survives_watchdog(monkeypatch):
    import gd_extra
    import gd_journal
    import gd_profiles
    import gd_rules
    import gd_toast

    monkeypatch.setattr(gd_rules, "load_rules", lambda: [])
    monkeypatch.setattr(gd_profiles, "_read_autoclean", lambda: {})
    monkeypatch.setattr(gd_extra, "timer_ms", lambda: 15.6)
    monkeypatch.setattr(gd_journal, "record", lambda *a, **kw: None)
    monkeypatch.setattr(gd_toast, "show_toast", lambda *a: None)
    monkeypatch.setattr(main.gd_zapret if hasattr(main, "gd_zapret") else __import__("gd_zapret"),
                        "our_state", lambda: None)
    controller = NS(reset=Mock(), apply=Mock())
    window = NS(_ensure_page=lambda key: None, gamma_controller=controller,
                gamma_animation_timer=NS(stop=lambda: None, isActive=lambda: False),
                _cancel_cat_vision=Mock(), _gamma_applied_ok=True,
                _gamma_applied_values=(1.5, 1.0, 1.7, 6500, 20))
    monkeypatch.setattr(gd_bg, "submit", lambda *a, **kw: None)
    window.set_status = Mock()
    window._reset_system_done = Mock()

    def set_values(b, c, g, t, **kw):
        window._gamma_applied_values = (b, c, g, t, kw["shadow"])

    window.set_ui_values = Mock(side_effect=set_values)
    main.MainWindow.reset_everything(window)
    window.set_ui_values.assert_called_once_with(1.0, 1.0, 1.0, 6500, shadow=0, apply_now=True, save=True)
    main.MainWindow.gamma_watch_tick(window)
    controller.apply.assert_not_called()


def test_exit_waits_for_ddc_before_cleanup(monkeypatch):
    import gd_metrics

    jobs = []
    monkeypatch.setattr(main.QTimer, "singleShot", lambda delay, fn: jobs.append(fn))
    monkeypatch.setattr(gd_bg, "begin_shutdown", lambda: None, raising=False)
    worker = Mock()
    worker.isRunning.return_value = True
    monkeypatch.setattr(gd_bg, "_WORKERS", {worker})
    monkeypatch.setattr(gd_metrics, "stop", lambda *a, **kw: None)
    ddc = NS(close=Mock())
    cleanup = Mock()
    window = NS(is_quitting=False, _remember_window_size=Mock(), _save_config=Mock(),
                hotkey_manager=NS(clear=Mock()), gamma_watch_timer=NS(stop=Mock()),
                _stop_foreground_watch=Mock(), _ddc_timer=NS(stop=Mock()), _ddc=ddc,
                gamma_controller=NS(reset=Mock()), findChildren=lambda cls: [], setEnabled=Mock(),
                _poll_exit=lambda: main.MainWindow._poll_exit(window), _cleanup_for_exit=cleanup,
                _exit_finished=Mock(), set_status=Mock())
    monkeypatch.setattr(main.QApplication, "quit", Mock())
    monkeypatch.setattr(gd_bg, "submit", lambda fn, done, **kw: fn())
    main.MainWindow.quit_app(window)
    ddc.close.assert_not_called()
    cleanup.assert_not_called()
    assert jobs, "ожидание должно продолжаться через цикл Qt"
    worker.isRunning.return_value = False
    jobs.pop(0)()
    cleanup.assert_called_once()


def test_exit_restores_game_boost_and_reports_failure(monkeypatch):
    import gd_journal
    import gd_metrics

    monkeypatch.setattr(gd_metrics, "stop", lambda *a, **kw: None)
    record = Mock()
    monkeypatch.setattr(gd_journal, "record", record)
    boost = NS(stop=Mock(return_value=(False, "restore failed")))
    window = NS(_utils_page=NS(_boost=boost), _ddc=None, gamma_controller=NS(reset=Mock()))
    result = main.MainWindow._cleanup_for_exit(window)
    boost.stop.assert_called_once()
    assert not result["ok"]
    assert record.called


_APP = None


@pytest.mark.parametrize("action", ["_export", "_import", "_reset_all_settings", "_make_portable"])
def test_profile_actions_have_visible_status(monkeypatch, tmp_path, action):
    import gd_extra_ui as ui
    from PyQt5.QtWidgets import QApplication

    global _APP
    _APP = QApplication.instance() or QApplication([])
    monkeypatch.setattr(ui.gd_extra, "timer_ms", lambda: 15.6)
    monkeypatch.setattr(ui.gd_profiles, "load_quiet", lambda: {})
    monkeypatch.setattr(ui.gd_profiles, "export_all", lambda *a, **kw: (True, "export ok"))
    monkeypatch.setattr(ui.gd_profiles, "import_all", lambda *a, **kw: (True, "import ok"))
    monkeypatch.setattr(ui.gd_paths, "make_portable", lambda: (True, "portable ok"))
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "x.json"), ""))
    monkeypatch.setattr(ui.QFileDialog, "getOpenFileName", lambda *a: (str(tmp_path / "x.json"), ""))
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *a: ui.QMessageBox.Yes)
    monkeypatch.setattr(ui.gd_bg, "submit", lambda fn, done, **kw: done(fn()))
    panel = ui.ExtraPanel(host=NS(config={}, reset_app_settings=lambda: (True, "reset ok")))
    getattr(panel, action)()
    assert panel.lbl_prof.text()
    assert not panel.lbl_prof.isHidden()
    panel.deleteLater()
