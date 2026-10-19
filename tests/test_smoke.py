# -*- coding: utf-8 -*-
"""Полная смоук-проверка окна (offscreen) как тест — по требованию, а не всегда.

Запускает ``tests/smoke_check.py`` отдельным процессом: Qt, потоки и sys.exit
внутри проверки не должны ронять сбор тестов. Включается переменной окружения
``GD_SMOKE=1`` — обычный прогон остаётся быстрым и без графики.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = Path(__file__).resolve().parent / "smoke_check.py"


@pytest.mark.smoke
def test_smoke_check_passes():
    if os.environ.get("GD_SMOKE") != "1":
        pytest.skip("смоук выключен: запустите с GD_SMOKE=1 (нужен venv с PyQt5)")

    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    tail = "\n".join((proc.stdout or "").splitlines()[-25:])
    assert proc.returncode == 0, (
        f"смоук-проверка вернула {proc.returncode}\n--- хвост вывода ---\n{tail}\n"
        f"--- stderr ---\n{(proc.stderr or '')[-2000:]}"
    )
