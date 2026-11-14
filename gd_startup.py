"""Файлы пользовательской автозагрузки Windows."""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

import gd_proc
from gd_paths import APP_NAME


def get_startup_shortcut_path() -> Path:
    """Скрипт автозапуска. `.vbs`: WScript умеет запускать процесс без окна."""
    startup_dir = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup_dir / f"{APP_NAME}.vbs"


def enable_startup(path: Path, *, admin: bool) -> None:
    """Включить автозапуск без окна.

    ``admin=True`` — сразу с правами администратора: иначе лимит GPU, DNS и
    очистка ОЗУ при каждом запуске упираются в UAC. Права запрашивает
    PowerShell (`Start-Process -Verb RunAs`), окно скрыто.

    Раньше это был .bat: он стартовал через `cmd` и при входе в систему
    мелькал чёрным окном. WScript.Shell.Run с флагом 0 (скрыто) не показывает
    ничего. В dev-режиме берём pythonw.exe — у python.exe консольная подсистема.
    """
    frozen = getattr(sys, "frozen", False)
    target = gd_proc.python_exe()
    entrypoint = Path(__file__).resolve().with_name("main.py")
    argument = "" if frozen else f'"{entrypoint}"'

    if admin:
        ps = "Start-Process -FilePath '{0}'".format(target.replace("'", "''"))
        if argument:
            ps += " -ArgumentList '{0}'".format(argument.replace("'", "''"))
        ps += " -Verb RunAs -WindowStyle Hidden"
        command = 'powershell -NoProfile -WindowStyle Hidden -Command "' + ps + '"'
    else:
        command = f'"{target}"'
        if argument:
            command += " " + argument

    # в VBS кавычка внутри строкового литерала удваивается
    vbs_command = command.replace('"', '""')
    # перевод строки собираем из кодов: исходник не должен зависеть от
    # того, как редактор сохранил escape-последовательность
    nl = chr(13) + chr(10)
    content = (
        # i18n: skip — комментарий внутри .vbs-файла автозапуска, пользователь его не видит
        "' Gammadrilon — автозапуск без окна (файл создан приложением)" + nl
        + 'Set sh = CreateObject("WScript.Shell")' + nl
        + 'sh.Run "' + vbs_command + '", 0, False' + nl
    )
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content.encode("utf-16"))
        temporary.replace(path)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)

    with suppress(OSError):
        path.with_suffix(".bat").unlink(missing_ok=True)


def disable_startup(path: Path) -> None:
    path.with_suffix(".bat").unlink(missing_ok=True)
    path.unlink(missing_ok=True)
