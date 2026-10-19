# -*- coding: utf-8 -*-
"""Где лежит приложение и куда оно пишет состояние — одно место на весь проект.

Портативный режим включается **явным маркером**: рядом с ``Gammadrilon.exe``
(в сборке) или с ``main.py`` (из исходников) лежит файл ``portable.txt``
(принимаются и ``portable.marker`` / ``.portable``). Тогда журнал, правила,
state-файлы, токен локального API и логи пишутся в ту же папку — её можно
унести на флешке вместе с настройками. Без маркера поведение прежнее:
``presets.json`` рядом с приложением (так было всегда), данные — в
``%ProgramData%\\Gammadrilon``, логи — в ``%LOCALAPPDATA%\\Gammadrilon``.

Переменная ``GD_PORTABLE=1``/``0`` сильнее маркера: приборы и тесты не должны
зависеть от того, что случайно лежит рядом с проектом.

Путь приложения определяется **при каждом вызове**: компаньоны питания и
хелпер — отдельные процессы, и каждый обязан получить тот же ответ, что окно.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Gammadrilon"

#: имена файла-маркера: достаточно положить любой из них рядом с exe
MARKER_NAMES = ("portable.txt", "portable.marker", ".portable")

#: переменная окружения для приборов: перекрывает маркер в обе стороны
ENV_FLAG = "GD_PORTABLE"


def app_dir() -> Path:
    """Папка приложения: рядом с exe в сборке, папка проекта — из исходников."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_marker() -> Path | None:
    """Файл-маркер портативного режима рядом с приложением (или None)."""
    for name in MARKER_NAMES:
        path = app_dir() / name
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def portable_enabled() -> bool:
    """Портативный режим: явная переменная окружения сильнее маркера."""
    flag = (os.environ.get(ENV_FLAG) or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return find_marker() is not None


def data_dir() -> Path:
    """Куда идут журнал, правила и state-файлы: портативно — рядом с приложением."""
    if portable_enabled():
        return app_dir()
    base = os.environ.get("ProgramData", r"C:\ProgramData")
    return Path(base) / APP_NAME


def log_dir() -> Path:
    """Куда идут логи и служебные файлы процессов (токен API, сессия хелпера)."""
    if portable_enabled():
        return app_dir()
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return Path(base) / APP_NAME


def ensure(path) -> Path:
    """Создать папку, не бросая исключений: отказ прав — дело вызывающего."""
    folder = Path(path)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder


def mode_note() -> str:
    """Строка для интерфейса: где лежат данные и как включить портативный режим."""
    import gd_i18n as i18n

    if portable_enabled():
        return i18n.t("Портативный режим: данные и логи рядом с приложением — {v0}.",
                      v0=str(app_dir()))
    return i18n.t(
        "Данные: {v0} · логи: {v1}. Портативный режим — файл {v2} рядом с приложением.",
        v0=str(data_dir()), v1=str(log_dir()), v2=MARKER_NAMES[0])
