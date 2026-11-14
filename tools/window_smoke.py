"""Run the packaged GUI with temporary user data and inert machine controls.

Build using the regular PyInstaller spec, with its Analysis entry point
temporarily set to tools/window_smoke.py. Pass the JSON report path as argv[1].
"""
import json
import os
import sys
import tempfile
import threading
import traceback
from pathlib import Path


def run(report):
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import gd_helper
    import gd_power
    import main as gd
    from gd_power_ui import PowerClient

    errors = []
    result = {"pages": []}

    def record_error(exc_type, error, tb):
        errors.append("".join(traceback.format_exception(exc_type, error, tb)))

    def capture_errors():
        sys.excepthook = record_error
        threading.excepthook = lambda args: record_error(
            args.exc_type, args.exc_value, args.exc_traceback
        )

    def offline(*args, **kwargs):
        return {"ok": False, "error": "smoke test: disconnected"}

    def no_op(*args, **kwargs):
        pass

    def unexpected_power_write(*args, **kwargs):
        errors.append("The window attempted a direct power write during the smoke test")
        raise AssertionError(errors[-1])

    gd.install_crash_handlers = capture_errors
    gd.MainWindow.bind_hotkeys = no_op
    gd.MainWindow._start_foreground_watch = no_op
    gd.MainWindow._sync_startup = no_op
    gd.MainWindow._gpu_autostart_check = no_op
    gd.GammaController.apply = lambda *args, **kwargs: True
    gd.GammaController.reset = lambda *args: True
    gd.gd_hz.set_refresh = lambda *args, **kwargs: (False, "smoke test")
    gd_helper.HelperClient.call = offline
    gd_helper.HelperClient.call_existing = offline
    gd_helper.HelperClient.shutdown = lambda *a, **k: {"ok": True}
    PowerClient.call = offline
    PowerClient.call_existing = offline
    PowerClient.shutdown = lambda *a, **k: {"ok": True}
    gd_power.apply_profile = unexpected_power_write
    gd_power.restore_report = unexpected_power_write
    gd_power.cleanup_clones = unexpected_power_write

    original_show = gd.MainWindow.show

    def visit(window):
        app = QApplication.instance()
        result["platform"] = app.platformName()
        assert result["platform"] == "windows", result["platform"]
        assert window.isVisible() and window.windowHandle().isExposed()
        assert window.windowTitle() == gd.APP_NAME
        assert window._key_to_index["tab.home"] == window.tabs.currentIndex()
        pages = iter(sorted(window._key_to_index.items(), key=lambda pair: pair[1]))

        def advance():
            try:
                for key, index in pages:
                    window.tabs.setCurrentIndex(index)
                    assert window._page_widgets[key].isVisible(), key
                    result["pages"].append(key)
                    QTimer.singleShot(180, advance)
                    return
                window.toggle_theme()
                assert gd.th.active_name() == "light"
                window.toggle_theme()
                assert gd.th.active_name() == "dark"
                result["themes"] = ["light", "dark"]
                window.close()
                assert window.is_quitting
                result["closed"] = True
            except Exception:
                errors.append(traceback.format_exc())
                window.quit_app()

        advance()

    def show(window):
        original_show(window)
        QTimer.singleShot(750, lambda: visit(window))
        QTimer.singleShot(30000, lambda: QApplication.exit(1))

    gd.MainWindow.show = show
    code = 1
    try:
        gd.main()
    except SystemExit as exit_result:
        code = exit_result.code
    except Exception:
        errors.append(traceback.format_exc())
    result.update(errors=errors, exit_code=code)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(code != 0 or bool(errors) or not result.get("closed"))


if __name__ == "__main__":
    output = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="gd_window_smoke_") as temp:
        folder = Path(temp)
        for key in ("APPDATA", "LOCALAPPDATA", "ProgramData"):
            os.environ[key] = str(folder / key)
        os.environ["GD_PORTABLE"] = "0"
        os.environ["GD_ALLOW_SECOND_INSTANCE"] = "1"
        os.environ["QT_QPA_PLATFORM"] = "windows"
        settings = Path(os.environ["LOCALAPPDATA"]) / "Gammadrilon" / "presets.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"settings": {
            "first_run_done": True, "sensors": False, "check_updates": False,
            "close_on_x": True, "theme": "dark",
        }}), encoding="utf-8")
        sys.exit(run(output))
