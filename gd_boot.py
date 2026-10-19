# -*- coding: utf-8 -*-
"""Отметки времени старта — инструмент замеров (GD_BOOT_LOG).

T0 берётся в момент импорта этого модуля: main.py импортирует его до PyQt5,
поэтому отметки считаются от самого начала запуска, включая импорт Qt. Без
переменной окружения GD_BOOT_LOG вызовы mark() ничего не пишут — в обычном
запуске цена нулевая. Формат файла — JSONL: ``{"mark", "ms", "pid"}``.
"""
from __future__ import annotations

import json
import os
import time

T0 = time.perf_counter()


def mark(name: str) -> None:
    """Записать отметку старта в файл из GD_BOOT_LOG (мс от импорта gd_boot)."""
    path = os.environ.get("GD_BOOT_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "mark": name,
                "ms": round((time.perf_counter() - T0) * 1000, 1),
                "pid": os.getpid(),
            }) + "\n")
    except OSError:
        pass
