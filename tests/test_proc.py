# -*- coding: utf-8 -*-
"""Запуск внешних процессов: текстовый режим не должен терять вывод.

Windows-утилиты печатают в OEM-кодировке. В UTF-8 режиме Python
(`PYTHONUTF8=1`) декодирование по умолчанию падало в фоновом reader-потоке,
а `stdout` оставался `None` — молча ломались `nvidia-smi` и `sc query`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_proc  # noqa: E402 — путь проекта добавлен выше


def test_text_mode_decodes_raw_bytes():
    """Байт вне UTF-8 не должен обнулять stdout."""
    code = "import sys; sys.stdout.buffer.write(b'\\x88\\n')"
    proc = gd_proc.run([sys.executable, "-c", code], text=True, timeout=15)

    assert proc.returncode == 0
    assert isinstance(proc.stdout, str), "reader-поток уронил декодирование"
    assert proc.stdout.strip(), "вывод потерян"
