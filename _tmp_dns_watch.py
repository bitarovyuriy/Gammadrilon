# -*- coding: utf-8 -*-
"""ВРЕМЕННАЯ проверка: какие дети порождает живое приложение за окно наблюдения.

Зачем: убедиться, что DNS-конфигурация (`netsh`) больше не читается фоном — ни на
старте, ни по таймеру. Раньше чтение шло сразу при старте и раз в 60 с в открытом окне.
Опрос дерева — каждые 20 мс, а `netsh`/`nvidia-smi` живут ~70 мс, поэтому пропуск
отдельного запуска возможен (ложное срабатывание — нет). Контроль качества прибора —
`nvidia-smi`: он запускается штатно раз в 5 с, то есть обязан быть виден.

Запуск:  .venv\\Scripts\\python.exe _tmp_dns_watch.py [секунды=70]
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

try:
    import psutil
except ImportError:  # pragma: no cover
    sys.exit("нужен psutil")

POLL = 0.02
HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 70.0
    pyw = os.path.join(HERE, ".venv", "Scripts", "pythonw.exe")
    exe = pyw if os.path.isfile(pyw) else os.path.join(HERE, ".venv", "Scripts", "python.exe")
    app = subprocess.Popen([exe, os.path.join(HERE, "main.py")], cwd=HERE)

    counts: dict[str, int] = {}
    seen: set[int] = set()
    detail: list[str] = []
    t0 = time.monotonic()
    try:
        root = psutil.Process(app.pid)
        time.sleep(2.0)                      # дать окну собраться (первый тик метрик)
        while time.monotonic() - t0 < secs:
            try:
                kids = root.children(recursive=True)
            except psutil.Error:
                kids = []
            for ch in kids:
                if ch.pid in seen:
                    continue
                seen.add(ch.pid)
                try:
                    name = (ch.name() or "?").lower()
                except psutil.Error:
                    name = "?"
                try:
                    cmd = " ".join(ch.cmdline() or [])[:110]
                except psutil.Error:
                    cmd = ""
                counts[name] = counts.get(name, 0) + 1
                detail.append(f"    +{time.monotonic() - t0:5.1f} с  {name:<14} {cmd}")
            time.sleep(POLL)
        wall = time.monotonic() - t0
    finally:
        try:
            app.terminate()
            app.wait(10)
        except Exception:  # noqa: BLE001
            pass

    print(f"окно наблюдения: {wall:.1f} с, детей увидено: {len(seen)}")
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {name:<16} {n} запусков")
    print("--- кто и когда появился (кроме nvidia-smi/conhost) ---")
    for line in detail:
        if "nvidia-smi" not in line and "conhost" not in line:
            print(line)
    netsh = counts.get("netsh.exe", 0)
    nv = counts.get("nvidia-smi.exe", 0)
    print()
    print(f"контроль прибора: nvidia-smi {nv} запусков (ожидается ~{wall / 5:.0f} — по TTL 5 с)")
    if netsh:
        print(f"ВЕРДИКТ: ПРОВАЛ — netsh запускался {netsh} раз(а), DNS всё ещё читается фоном")
    else:
        print("ВЕРДИКТ: netsh за окно не запускался — DNS читается только по запросу")
    return 1 if netsh else 0


if __name__ == "__main__":
    sys.exit(main())
