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
import shutil
import sys
from pathlib import Path

APP_NAME = "Gammadrilon"

#: имя файла настроек (историческое — рядом с exe, теперь в data_dir)
CONFIG_NAME = "presets.json"

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


def can_write_data() -> tuple[bool, str]:
    """Можно ли писать в папку настроек. Возвращает (да/нет, путь или причина).

    Установка «для всех» кладёт программу в `Program Files`, и без прав
    администратора запись туда молча проваливается: пресеты выглядят
    сохранёнными и исчезают после перезапуска. Проверяем пробным файлом,
    а не правами на бумаге.
    """
    target = ensure(config_path().parent)
    probe = Path(target) / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, str(target)
    except OSError as exc:
        return False, f"{target}: {exc}"


def ensure(path) -> Path:
    """Создать папку, не бросая исключений: отказ прав — дело вызывающего."""
    folder = Path(path)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder


def config_path() -> Path:
    """Настройки приложения: портативно — рядом с exe, иначе в профиле пользователя.

    Раньше файл лежал рядом с приложением, потом в ``data_dir()`` (ProgramData).
    Оба места плохи для настроек: в ``Program Files`` писать нельзя, а в
    ProgramData файл, созданный elevated-процессом, обычный запуск перезаписать
    не сможет (владелец — администраторы, у Users только чтение). Настройки —
    личные, поэтому им место в ``%LOCALAPPDATA%``, куда UI пишет без прав.
    """
    if portable_enabled():
        return app_dir() / CONFIG_NAME
    return log_dir() / CONFIG_NAME


def migrate_legacy_config() -> Path | None:
    """Перенести старый ``presets.json`` в профиль пользователя.

    Смотрим два прежних места: рядом с приложением (самая старая схема) и в
    ``data_dir()`` (промежуточная). Копируем, а не перемещаем: старая версия,
    запущенная рядом, продолжит работать со своим файлом.
    """
    if portable_enabled():
        return None
    target = config_path()
    try:
        if target.is_file():
            return None
        for legacy in (data_dir() / CONFIG_NAME, app_dir() / CONFIG_NAME):
            if not legacy.is_file():
                continue
            ensure(target.parent)
            shutil.copy2(legacy, target)
            return target
    except OSError:
        return None
    return None


def make_portable() -> tuple[bool, str]:
    """Включить портативный режим: маркер рядом с приложением + копия настроек.

    Нужно, чтобы унести программу на флешке вместе с настройками: без маркера
    данные живут в профиле пользователя и на другой машине их не будет.
    Возвращает (ok, путь или причина отказа).
    """
    import gd_i18n as i18n

    app = app_dir()
    marker = app / MARKER_NAMES[0]
    source = config_path()          # до записи маркера — профиль пользователя
    target = app / CONFIG_NAME
    try:
        marker.write_text(
            "Портативный режим Gammadrilon: данные и настройки лежат в этой папке.\n"
            "Удалите этот файл, чтобы вернуться к хранению в профиле пользователя.\n",
            encoding="utf-8")
    except OSError as exc:
        return False, i18n.t("не удалось создать маркер {v0}: {v1}",
                             v0=marker.name, v1=exc)
    if source.is_file() and source != target and not target.exists():
        try:
            ensure(target.parent)
            shutil.copy2(source, target)
        except OSError:
            pass            # маркер уже работает; копия — приятный бонус
    return True, str(marker)


def mode_note() -> str:
    """Строка для интерфейса: где лежат данные и как включить портативный режим."""
    import gd_i18n as i18n

    if portable_enabled():
        return i18n.t("Портативный режим: данные и логи рядом с приложением — {v0}.",
                      v0=str(app_dir()))
    return i18n.t(
        "Данные: {v0} · логи: {v1}. Портативный режим — файл {v2} рядом с приложением.",
        v0=str(data_dir()), v1=str(log_dir()), v2=MARKER_NAMES[0])
