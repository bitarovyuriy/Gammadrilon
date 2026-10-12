# -*- coding: utf-8 -*-
"""Живая проверка записи лимита GPU: elevated-хелпер + наблюдение за nvidia-smi.

Запуск: python _tmp_gpu_live.py [watts]

Скрипт намеренно не трогает сессию запущенного приложения: у него свой порт
и свой файл сессии. Задача — увидеть, меняется ли power.limit на железе после
команды apply, и что возвращает хелпер.
"""
from __future__ import annotations

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gd_gpu  # noqa: E402
import gd_proc  # noqa: E402

WATTS = int(sys.argv[1]) if len(sys.argv) > 1 else 212
PORT = 47319
SESSION = os.path.join(HERE, "build", "gpu_live_session.json")

samples: list[tuple[float, str]] = []
stop = threading.Event()


def watcher() -> None:
    t0 = time.time()
    while not stop.is_set():
        try:
            proc = gd_proc.run(
                [gd_gpu.SMI, "--query-gpu=power.limit,clocks.gr",
                 "--format=csv,noheader,nounits"],
                text=True, timeout=8,
            )
            line = (proc.stdout or "").strip().replace("\n", " ")
        except Exception as exc:  # noqa: BLE001
            line = f"ошибка чтения: {exc}"
        samples.append((time.time() - t0, line))
        time.sleep(0.5)


th = threading.Thread(target=watcher, daemon=True)
th.start()

print(f"интерпретатор хелпера: {gd_proc.python_exe()}", flush=True)
client = gd_gpu.GpuClient(port=PORT, session_file=SESSION)
st = gd_gpu.read_state()
print(f"до: {st['pl']:.0f} Вт (диапазон {st['pl_min']:.0f}–{st['pl_max']:.0f})", flush=True)

t = time.time()
resp = client.call({"v": 1, "cmd": "apply", "pl": WATTS}, timeout=60.0)
print(f"apply({WATTS}) за {time.time() - t:.1f} с → {resp}", flush=True)

time.sleep(6)
print(f"через 6 с после apply: {gd_gpu.read_state()['pl']:.0f} Вт", flush=True)

resp = client.call({"v": 1, "cmd": "restore"}, timeout=30.0)
print(f"restore → {resp}", flush=True)
time.sleep(3)
print(f"через 3 с после restore: {gd_gpu.read_state()['pl']:.0f} Вт", flush=True)

client.shutdown(restore=True)
time.sleep(1)
stop.set()
th.join(timeout=3)

print("\n-- наблюдение (секунды от старта | power.limit, clocks.gr) --")
for sec, line in samples:
    print(f"{sec:5.1f}  {line}")
