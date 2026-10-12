# -*- coding: utf-8 -*-
"""ВРЕМЕННАЯ проверка: дерево процессов СОБРАННОГО exe.

Зачем: в портативной сборке при прошлой ревизии наблюдался живой компаньон
`Gammadrilon.exe --power-service --ui-pid <UI>` сразу после старта — при том, что
вкладка CPU ленивая, а в dev-запуске компаньона нет. Здесь смотрим на новую сборку:
кто есть в дереве, кто кого породил (через `exe()` и `cmdline()`, а не по имени —
в dev «второй процесс» оказался редиректором venv), и остаётся ли что-то жить
после закрытия окна.

Запуск:  .venv\\Scripts\\python.exe _tmp_exe.py [путь_к_exe] [секунды=12]
"""
from __future__ import annotations

import datetime
import os
import subprocess
import sys
import time

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = r"C:\gd_build\dist\Gammadrilon\Gammadrilon.exe"

exe = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0


def stamp(t: float) -> str:
    return datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-3]


def view(pid: int) -> str:
    try:
        p = psutil.Process(pid)
        return f"{p.name()} [{pid}] {stamp(p.create_time())}"
    except psutil.Error:
        return f"[{pid}] (нет)"


def tree_print(root: psutil.Process) -> None:
    print(f"корень: {root.name()} [{root.pid}] создан {stamp(root.create_time())}")
    print(f"        образ: {root.exe() if _safe(root.exe) else '?'}")
    print(f"        cmd:    {' '.join(root.cmdline())[:110]}")
    for ch in root.children(recursive=True):
        earlier = "СОЗДАН РАНЬШЕ КОРНЯ" if ch.create_time() < root.create_time() else "позже корня"
        try:
            image = ch.exe()
        except psutil.Error:
            image = "?"
        try:
            cmd = " ".join(ch.cmdline())[:110]
        except psutil.Error:
            cmd = "?"
        print(f"  ребёнок: {ch.name()} [{ch.pid}] создан {stamp(ch.create_time())} ({earlier})")
        print(f"           образ:    {image}")
        print(f"           cmd:      {cmd}")
        print(f"           родитель: {view(ch.ppid())}")


def _safe(fn):
    try:
        return fn()
    except psutil.Error:
        return None


def relatives(needle: str) -> list[psutil.Process]:
    """Все процессы, чей образ/команда похожи на наше приложение (включая отвязанных)."""
    out = []
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            if needle.lower() in ((p.info["exe"] or "")).lower() \
                    or needle.lower() in ((p.info["name"] or "")).lower():
                out.append(p)
        except psutil.Error:
            continue
    return out


def main() -> int:
    needle = os.path.basename(exe)
    print(f"запускаю: {exe}")
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    try:
        time.sleep(secs)
        root = psutil.Process(proc.pid)
        tree_print(root)
        print("\nвсе процессы с образом/именем приложения:")
        for p in relatives(needle):
            try:
                cmd = " ".join(p.cmdline())[:110]
            except psutil.Error:
                cmd = "?"
            print(f"  {p.pid:>6} ppid={p.ppid():>6} {p.name():<18} {cmd}")
    finally:
        victims = [proc.pid] + [p.pid for p in relatives(needle)]
        for pid in set(victims):
            try:
                psutil.Process(pid).terminate()
            except psutil.Error:
                pass
        time.sleep(3)
        alive = [p.pid for p in relatives(needle) if p.is_running()]
        print("\nпосле закрытия окна живы остались:", alive or "никого")
    return 0


if __name__ == "__main__":
    sys.exit(main())
