"""Заметные находки ревью: потоки, WinAPI и частичные отказы."""
import ctypes
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gd_bg  # noqa: E402
import gd_boost  # noqa: E402
import gd_cs2_ui  # noqa: E402
import gd_ddc  # noqa: E402
import gd_dns  # noqa: E402
import gd_extra  # noqa: E402
import gd_memory  # noqa: E402
import main  # noqa: E402


def test_timer_returns_current_not_limit(monkeypatch):
    def query(low, high, current):
        low._obj.value, high._obj.value, current._obj.value = 156250, 5000, 10000
        return 0

    monkeypatch.setattr(gd_extra, "_ntdll", lambda: NS(NtQueryTimerResolution=Mock(side_effect=query)))
    assert gd_extra.timer_current_100ns() == 10000


def test_boost_does_not_hold_failed_timer(monkeypatch, tmp_path):
    monkeypatch.setattr(gd_boost.gd_paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(gd_boost, "anticheat_present", lambda: None)
    monkeypatch.setattr(gd_boost, "collect_targets", lambda **kw: [])
    monkeypatch.setattr(gd_extra, "set_timer_resolution", lambda *a: (False, 0))
    boost = gd_boost.GameBoost()
    boost.start()
    assert boost.status()["timer"] is False
    assert "0.5" not in boost.status()["note"]


@pytest.mark.parametrize("outcome", ["success", "lookup", "adjust", "unassigned", "exception"])
def test_privilege_always_closes_token(monkeypatch, outcome):
    def open_token(proc, access, target):
        target._obj.value = 123
        return True

    def adjust(*args):
        if outcome == "exception":
            raise OSError("driver failed")
        ctypes.set_last_error(1300 if outcome == "unassigned" else 0)
        return outcome != "adjust"

    close = Mock()
    monkeypatch.setattr(gd_memory, "k32", NS(GetCurrentProcess=lambda: 42, CloseHandle=close))
    monkeypatch.setattr(gd_memory, "advapi32", NS(OpenProcessToken=open_token,
                        LookupPrivilegeValueW=lambda *a: outcome != "lookup", AdjustTokenPrivileges=adjust))
    try:
        ok, _ = gd_memory.enable_privilege("test")
        assert ok is (outcome == "success")
    except OSError:
        assert outcome == "exception"
    close.assert_called_once()
    assert close.call_args.args[0].value == 123


@pytest.mark.parametrize("mode,servers,fail_at", [("static", ["1.1.1.1", "8.8.8.8"], 2),
                                                  ("dhcp", [], 2)])
def test_dns_partial_failure_is_reported(monkeypatch, mode, servers, fail_at):
    calls = []

    def run(args):
        calls.append(args)
        return (1, "denied") if len(calls) == fail_at else (0, "")

    monkeypatch.setattr(gd_dns, "_run", run)
    monkeypatch.setattr(gd_dns, "flush_native", lambda: (True, "ok"))
    ok, message = gd_dns.apply_native(mode, servers, "Ethernet")
    assert not ok
    assert "denied" in message


def test_dns_validates_all_addresses_before_writing(monkeypatch):
    run = Mock(return_value=(0, ""))
    monkeypatch.setattr(gd_dns, "_run", run)
    monkeypatch.setattr(gd_dns, "flush_native", lambda: (True, "ok"))
    assert not gd_dns.apply_native("static", ["1.1.1.1", "bad'address"], "Ethernet")[0]
    run.assert_not_called()


def test_flush_uses_exit_code(monkeypatch):
    monkeypatch.setattr(gd_dns, "_run", lambda args: (1, "localized failure"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: NS(DnsFlushResolverCache=lambda: 0))
    assert not gd_dns.flush_native()[0]


def test_startup_normal_rights_survive_restart(monkeypatch, tmp_path):
    target = tmp_path / "presets.json"
    startup = tmp_path / "startup.vbs"
    monkeypatch.setattr(main, "config_path", lambda: target)
    monkeypatch.setattr(main.gd_paths, "migrate_legacy_config", lambda: None)
    monkeypatch.setattr(main, "get_startup_shortcut_path", lambda: startup)
    main.save_config({"settings": {"start_with_windows": True, "start_admin": False}})
    window = NS(config=main.load_config())
    main.MainWindow._sync_startup(window)
    text = startup.read_text(encoding="utf-16")
    assert "RunAs" not in text


def test_cs2_saves_no_argument_callback_and_reports_false():
    saved = []
    window = NS(_save_fn=lambda: saved.append(True) or False, _note=Mock(), _config={})
    gd_cs2_ui.Cs2Widget._save(window)
    assert saved == [True]
    assert window._note.called


def test_cs2_apply_is_submitted_before_execution(monkeypatch):
    jobs = []
    monkeypatch.setattr(gd_bg, "submit", lambda fn, done, **kw: jobs.append((fn, done)))
    apply = Mock(return_value={"ok": True, "applied": ["game_mode"]})
    monkeypatch.setattr(gd_cs2_ui.gd_cs2, "apply", apply)
    window = NS(_busy=False, _on=False, _chosen=lambda: ["game_mode"], _exe="cs2.exe",
                _note=Mock(), _settings=lambda: {}, _save=Mock(), reload=Mock(),
                btn_toggle=NS(setEnabled=Mock()), _toggle_done=Mock(),
                _client=NS(call=Mock(return_value={"ok": True, "data": {"applied": ["game_mode"]}})))
    gd_cs2_ui.Cs2Widget._toggle(window)
    assert len(jobs) == 1
    apply.assert_not_called()


def test_gamma_applies_in_background_and_coalesces(monkeypatch):
    jobs = []
    monkeypatch.setattr(gd_bg, "submit", lambda fn, done, **kw: jobs.append((fn, done)))
    controller = NS(apply=Mock(return_value=True))
    window = NS(gamma_controller=controller, shadow_strength=lambda: 0, _gamma_busy=False,
                _gamma_pending=None, set_status=Mock(), _gamma_failure_status=lambda: "failed")
    window._submit_gamma = lambda: main.MainWindow._submit_gamma(window)
    window._gamma_done = lambda result: main.MainWindow._gamma_done(window, result)
    for gamma in (1.2, 1.5, 1.7):
        main.MainWindow.apply_values(window, 1.0, 1.0, gamma, 6500, save=False)
    controller.apply.assert_not_called()
    assert len(jobs) == 1
    jobs[0][1](jobs[0][0]())
    assert len(jobs) == 2
    jobs[1][1](jobs[1][0]())
    assert [call.args[2] for call in controller.apply.call_args_list] == [1.2, 1.7]


def test_gamma_completion_is_reported_after_driver_result(monkeypatch):
    jobs, results = [], []
    monkeypatch.setattr(gd_bg, "submit", lambda fn, done, **kw: jobs.append((fn, done)))
    window = NS(gamma_controller=NS(apply=lambda *a: False), shadow_strength=lambda: 0,
                _gamma_pending=None, _gamma_busy=False, set_status=Mock(), _gamma_failure_status=lambda: "failed")
    window._submit_gamma = lambda: main.MainWindow._submit_gamma(window)
    window._gamma_done = lambda result: main.MainWindow._gamma_done(window, result)
    main.MainWindow.apply_values(window, 0.5, 1.0, 1.0, 6500, save=False, on_done=results.append)
    assert not results
    jobs[0][1](jobs[0][0]())
    assert results == [False]


def test_quiet_hour_waits_for_cpu_and_gamma(monkeypatch):
    import gd_profiles
    import gd_toast
    import gd_journal

    jobs, gamma = [], []
    monkeypatch.setattr(gd_bg, "submit", lambda fn, done, **kw: jobs.append((fn, done)))
    monkeypatch.setattr(gd_profiles, "save_quiet", lambda value: True)
    monkeypatch.setattr(gd_journal, "record", Mock())
    toast = Mock()
    monkeypatch.setattr(gd_toast, "show_toast", toast)
    window = NS(_power_page=NS(client=NS(call=lambda *a, **kw: {"ok": True})),
                apply_values=lambda *a, **kw: gamma.append(kw["on_done"]),
                set_status=Mock(), _report_failure=Mock())
    main.MainWindow._quiet_change(window, {}, {"brightness": 0.5, "contrast": 1, "gamma": 1,
                                               "temperature": 6500}, True)
    assert not toast.called
    jobs[0][1](jobs[0][0]())
    assert not toast.called
    gamma[0](True)
    assert toast.called


def test_boost_click_only_submits(monkeypatch):
    import gd_utils_ui as ui

    jobs = []
    monkeypatch.setattr(gd_bg, "submit", lambda fn, done, **kw: jobs.append((fn, done)))
    boost = NS(active=lambda: False, start=Mock(return_value=(True, "ok")), stop=Mock())
    window = NS(_boost=boost, _boost_worker=None, _show_boost=Mock(),
                btn_boost=NS(setEnabled=Mock()), _boost_done=Mock())
    ui.UtilsWidget._toggle_boost(window)
    boost.start.assert_not_called()
    assert len(jobs) == 1


def test_service_job_never_reads_widgets(monkeypatch):
    import gd_zapret_ui as ui

    jobs = []
    monkeypatch.setattr(gd_bg, "submit", lambda fn, done, **kw: jobs.append((fn, done)))
    window = NS(_worker=None, _selected_strategy=Mock(return_value={"id": "general.bat"}),
                _path=lambda: "C:/test", _set_status=Mock(), _service_done=Mock(), _render=Mock())
    window._job_service = Mock(return_value={"ok": True})
    ui.ZapretWidget._service_action(window, "install")
    window._selected_strategy.assert_called_once()
    window._selected_strategy.side_effect = AssertionError("worker read GUI")
    jobs[0][0]()
    assert window._job_service.call_args.args == ("install", "C:/test", {"id": "general.bat"})


_APP = None


def test_thread_crash_dialog_runs_in_gui_thread(monkeypatch, tmp_path):
    from PyQt5.QtWidgets import QApplication
    import faulthandler
    import gd_dialog

    global _APP
    _APP = QApplication.instance() or QApplication([])
    monkeypatch.setattr(main, "_crash_log_path", lambda: tmp_path / "crash.log")
    monkeypatch.setattr(main, "_CRASH_KEEP", [])
    monkeypatch.setattr(faulthandler, "enable", lambda **kw: None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    threads = []
    monkeypatch.setattr(gd_dialog, "notify", lambda *a: threads.append(threading.get_ident()))
    main.install_crash_handlers()

    def crash():
        threading.excepthook(NS(exc_type=ValueError, exc_value=ValueError("test"), exc_traceback=None))

    worker = threading.Thread(target=crash)
    worker.start()
    worker.join()
    _APP.processEvents()
    assert threads == [threading.get_ident()]


def test_ddc_enumerates_all_logical_monitors(monkeypatch):
    class Lib:
        def GetNumberOfPhysicalMonitorsFromHMONITOR(self, monitor, count):  # noqa: N802
            count._obj.value = 1
            return True

        def GetPhysicalMonitorsFromHMONITOR(self, monitor, count, raw):  # noqa: N802
            raw[0].handle = int(monitor) + 10
            raw[0].description = f"Panel {monitor}"
            return True

    def enum(dc, rect, callback, data):
        callback(1, None, None, 0)
        callback(2, None, None, 0)
        return True

    monkeypatch.setattr(gd_ddc, "_user32", NS(EnumDisplayMonitors=enum))
    monkeypatch.setattr(gd_ddc, "Monitor", lambda lib, handle, description, index, **kw:
                        NS(supported=True, description=description))
    monkeypatch.setattr(gd_ddc, "_load_dxva2", lambda: Lib())
    ddc = gd_ddc.Ddc()
    assert ddc.load()
    assert len(ddc.monitors) == 2


def test_display_change_reprobes_ddc(monkeypatch):
    from ctypes import wintypes

    msg = wintypes.MSG()
    msg.message = 0x007E  # WM_DISPLAYCHANGE
    window = NS(_refresh_ddc=Mock())
    main.MainWindow.nativeEvent(window, b"windows_generic_MSG", ctypes.addressof(msg))
    window._refresh_ddc.assert_called_once()
