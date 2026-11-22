"""Gammadrilon Lite entry point. Setters run only after explicit user input."""
from __future__ import annotations

import sys

APP_VERSION = '1.0.0'


def main(args: list[str]) -> int:
    from gd_lite_actions import ACTIONS, backend, worker

    if len(args) == 3 and args[0] == '--apply':
        return worker(args[1], args[2])
    if len(args) == 6 and args[0] == '--zapret-settings':
        import ctypes
        from gd_lite_actions import TITLE
        from gd_lite_zapret import Settings, apply_settings

        if not ctypes.windll.shell32.IsUserAnAdmin():
            return 3
        try:
            apply_settings(Settings(*args[1:]))
        except (OSError, ValueError, RuntimeError) as exc:
            ctypes.windll.user32.MessageBoxW(None, str(exc), TITLE, 0x10010)
            return 1
        return 0
    if args == ['--diagnose']:
        from pathlib import Path

        from gd_lite_keys import Hotkeys
        from gd_lite_temps import cpu_measure, gpu_temperature
        from gd_lite_zapret import inspect

        if getattr(sys, 'frozen', False) and not (Path(__file__).parent / 'vendor/lhm/LiteSensors.exe').is_file():
            return 1
        checks = [inspect, Hotkeys, cpu_measure, gpu_temperature, *(fn for section in ACTIONS for fn in backend(section))]
        return int(not all(callable(fn) for fn in checks))
    if args not in ([], ['--smoke']):
        return 2

    from gd_lite_window import LiteWindow

    window = LiteWindow(hide=bool(args))
    window.title(f'Gammadrilon Lite {APP_VERSION}')
    if args:
        try:
            window.update_idletasks()
            return int(set(window.rows) != {'cpu', 'gpu', 'dns', 'zapret'} or bool(window.tk.call('after', 'info')))
        finally:
            window.destroy()
    window.mainloop()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:
        if sys.stderr is not None:
            raise
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, str(exc), 'Gammadrilon Lite', 0x10010)
        raise SystemExit(1) from exc
