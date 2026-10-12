# -*- coding: utf-8 -*-
"""Живая проверка GPU-хелпера в собранном exe (портативная сборка v3.1).

Хелпер запускается снаружи:
    Start-Process Gammadrilon.exe -ArgumentList '--gpu-service','--port','47320',...
Здесь — только клиент: apply / restore / shutdown и наблюдение за power.limit.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gd_gpu  # noqa: E402
import gd_proc  # noqa: E402

PORT = 47320
TOKEN = "00112233445566778899aabbccddeeff"
SESSION = os.path.join(HERE, "build", "gpu_exe_session.json")

os.makedirs(os.path.dirname(SESSION), exist_ok=True)
with open(SESSION, "w", encoding="utf-8") as fh:
    json.dump({"port": PORT, "token": TOKEN}, fh)

samples: list[tuple[float, str]] = []
stop = threading.Event()


def watcher() -> None:
    t0 = time.time()
    while not stop.is_set():
        try:
            proc = gd_proc.run(
                [gd_gpu.SMI, "--query-gpu=power.limit", "--format=csv,noheader,nounits"],
                text=True, timeout=8,
            )
            line = (proc.stdout or "").strip()
        except Exception as exc:  # noqa: BLE001
            line = f"ошибка: {exc}"
        samples.append((time.time() - t0, line))
        time.sleep(0.5)


th = threading.Thread(target=watcher, daemon=True)
th.start()

client = gd_gpu.GpuClient(port=PORT, session_file=SESSION)
print("живой хелпер exe:", client.has_session(), flush=True)
print(f"до: {gd_gpu.read_state()['pl']:.0f} Вт", flush=True)

t = time.time()
resp = client.call({"v": 1, "cmd": "apply", "pl": 212}, timeout=30.0)
print(f"apply(212) за {time.time() - t:.1f} с → ok={resp.get('ok')} error={resp.get('error')}",
      flush=True)
print("state.pl после apply:", ((resp.get("data") or {}).get("state") or {}).get("pl"), flush=True)
time.sleep(5)
print(f"через 5 с: {gd_gpu.read_state()['pl']:.0f} Вт", flush=True)

resp = client.call({"v": 1, "cmd": "restore"}, timeout=30.0)
print(f"restore → ok={resp.get('ok')} error={resp.get('error')}", flush=True)
time.sleep(3)
print(f"после restore: {gd_gpu.read_state()['pl']:.0f} Вт", flush=True)

client.shutdown(restore=True)
time.sleep(1)
stop.set()
th.join(timeout=3)

print("\n-- наблюдение (секунды | power.limit) --")
for sec, line in samples:
    print(f"{sec:5.1f}  {line}")
