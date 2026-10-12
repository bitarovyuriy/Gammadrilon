# -*- coding: utf-8 -*-
"""Единый слой запуска внешних процессов — без мелькающих консолей.

Все утилиты (powershell, netsh, ipconfig, nvidia-smi) и процессы-компаньоны
запускаются только через этот модуль. Оконная подсистема подавляется флагами
`CREATE_NO_WINDOW` и `STARTUPINFO{STARTF_USESHOWWINDOW, SW_HIDE}`.

Зачем отдельный модуль: раньше каждый вызов `subprocess.run(...)` писался вручную,
и там, где флаги забывали (`gd_dns._run`), при каждом обращении мелькало чёрное
окно консоли. Теперь это одна точка, где про подавление забыть нельзя.
"""
from __future__ import annotations

import os
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0
#: исключение таймаута — чтобы вызывающие не импортировали subprocess напрямую
TimeoutExpired = subprocess.TimeoutExpired


def _startupinfo() -> "subprocess.STARTUPINFO":
    si = subprocess.STARTUPINFO()
    si.dwFlags |= STARTF_USESHOWWINDOW
    si.wShowWindow = SW_HIDE
    return si


def hidden_kwargs() -> dict:
    """Аргументы Popen/run, гарантирующие отсутствие окна консоли."""
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": _startupinfo()}


def run(args: list, timeout: float | None = None, text: bool = False, cwd: str | None = None):
    """Аналог subprocess.run без окна и с пустым stdin."""
    return subprocess.run(
        list(args),
        capture_output=True,
        text=text,
        timeout=timeout,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        **hidden_kwargs(),
    )


def popen(args: list, cwd: str | None = None) -> "subprocess.Popen":
    """Фоновый процесс без окна; stdin/stdout/stderr уходят в никуда."""
    return subprocess.Popen(
        list(args),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **hidden_kwargs(),
    )


def python_exe() -> str:
    """Интерпретатор для компаньонов.

    В собранном exe — сам `Gammadrilon.exe` (оконная подсистема). В dev-режиме —
    `pythonw.exe` (консольная подсистема python.exe дала бы окно).
    """
    exe = sys.executable
    if getattr(sys, "frozen", False):
        return exe
    if os.path.basename(exe).lower() == "python.exe":
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(cand):
            return cand
    return exe
