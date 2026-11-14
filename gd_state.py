# -*- coding: utf-8 -*-
"""Честная запись небольших JSON-состояний: атомарно и без исключений.

Один код на все state-файлы приложения (правила, DNS-откат, «тихий час»,
автоочистка, состояние обхода DPI, адрес локального API) — чтобы обрыв записи
нигде не оставлял обрезанный файл, а отказ диска не прятался за `except: pass`.

Порядок тот же, что у presets.json (main.save_config): пишем во временный файл
рядом → fsync → os.replace. На NTFS переименование атомарно: читатель видит
либо старый файл целиком, либо новый — обрезанного JSON не бывает даже при
внезапном выключении.

Функции не бросают OSError наружу: из слота Qt необработанное исключение
завершает приложение (PyQt5), а занятый чужим редактором файл — не повод
падать. Честный ответ — False / default; вызывающий сам решает, что сказать
пользователю.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

_REPLACE_LOCK = Lock()


def write_text_atomic(path: str | Path, text: str) -> bool:
    """Записать текст атомарно. False — не получилось (файл не тронут)."""
    path = Path(path)
    tmp = None

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as file:
            tmp = Path(file.name)
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        with _REPLACE_LOCK:
            os.replace(tmp, path)
        return True
    except OSError:
        try:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def write_json_atomic(path: str | Path, data, indent: int = 2) -> bool:
    """Записать JSON атомарно (см. write_text_atomic)."""
    return write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=indent))


def read_json(path: str | Path, default=None):
    """Прочитать JSON терпимо: нет файла / битый / отказ чтения — default."""
    try:
        with _REPLACE_LOCK:
            contents = Path(path).read_text(encoding="utf-8")
        return json.loads(contents)
    except (OSError, ValueError):
        return default


def read_json_with_recovery(path: str | Path, *, expected_type: type | None = None) -> tuple:
    """Прочитать JSON и убрать повреждённый файл в .bak под замком записи.

    Возвращает (данные, статус восстановления). Если передан expected_type,
    JSON с другим типом тоже считается повреждённым.
    """
    path = Path(path)
    with _REPLACE_LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, ""
        except OSError:
            return None, "no_backup"
        except ValueError:
            pass
        else:
            if expected_type is None or isinstance(data, expected_type):
                return data, ""
        try:
            os.replace(path, path.with_name(path.name + ".bak"))
        except OSError:
            return None, "no_backup"
        return None, "backup"
