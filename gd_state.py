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
from pathlib import Path


def write_text_atomic(path: str | Path, text: str) -> bool:
    """Записать текст атомарно. False — не получилось (файл не тронут)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        try:
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
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def quarantine(path: str | Path) -> Path | None:
    """Сохранить повреждённый файл как <имя>.bak вместо тихой перезаписи.

    Важна последняя копия, поэтому старая заменяется. Возвращаем путь копии;
    None — переименовать не удалось (файл остаётся как есть, копии нет).
    """
    path = Path(path)
    backup = path.with_name(path.name + ".bak")
    try:
        os.replace(path, backup)
        return backup
    except OSError:
        return None
