# -*- coding: utf-8 -*-
"""ВРЕМЕННАЯ проверка: дерево процессов живого приложения через 6 с после старта.

Зачем: в живой пробе появлялся ребёнок `pythonw.exe main.py` (выглядит как перезапуск).
Печатаем pid/ppid/cmdline и родителя каждого ребёнка, чтобы понять, кто его породил.

Запуск:  .venv\\Scripts\\python.exe _tmp_tree.py [секунды=6]
"""
from __future__ import annotations

import datetime
import os
import subprocess
import sys
import time

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0

pyw = os.path.join(HERE, ".venv", "Scripts", "pythonw.exe")
app = subprocess.Popen([pyw, os.path.join(HERE, "main.py")], cwd=HERE)
try:
    time.sleep(secs)
    root = psutil.Process(app.pid)
    t_root = datetime.datetime.fromtimestamp(root.create_time()).strftime("%H:%M:%S.%f")[:-3]
    print("root:", root.pid, f"создан {t_root}", " ".join(root.cmdline())[:90])
    for ch in root.children(recursive=True):
        ppid = ch.ppid()
        t_ch = datetime.datetime.fromtimestamp(ch.create_time()).strftime("%H:%M:%S.%f")[:-3]
        try:
            parent = " ".join(psutil.Process(ppid).cmdline())[:80]
        except Exception as exc:  # noqa: BLE001
            parent = f"(недоступен: {exc})"
        try:
            cmd = " ".join(ch.cmdline())[:90]
        except psutil.Error:
            cmd = "?"
        earlier = "СОЗДАН РАНЬШЕ КОРНЯ" if ch.create_time() < root.create_time() else "позже корня"
        try:
            image = ch.exe()
        except psutil.Error:
            image = "?"
        print(f"  child pid={ch.pid} ppid={ppid} name={ch.name()} создан {t_ch} ({earlier})")
        print(f"        образ: {image}")
        print(f"        cmd={cmd!r}")
        print(f"        родитель: {parent}")
finally:
    try:
        app.terminate()
        app.wait(10)
    except Exception:  # noqa: BLE001
        pass
