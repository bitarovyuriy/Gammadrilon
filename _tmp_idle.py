# -*- coding: utf-8 -*-
"""Замер холостого расхода Gammadrilon: сам процесс + его дети (nvidia-smi, netsh, powershell).

Запуск:  .venv\\Scripts\\python.exe _tmp_idle.py [pid|auto] [секунды]

Зачем: диспетчер задач и psutil.Process.cpu_percent() считают только корневой
процесс, а внешние утилиты (nvidia-smi, powershell, netsh) живут своей жизнью —
их время видно только по дереву. Здесь дети читаются через GetProcessTimes по
хэндлу: короткоживущий nvidia-smi (~70 мс) обычным опросом cpu_times теряется.

Печатает: % одного ядра на корень, на детей и суммарно по дереву, а также кто
именно из детей съел время и сколько раз за окно он запускался.
"""
from __future__ import annotations

import ctypes
import sys
import time

try:
    import psutil
except ImportError:  # pragma: no cover
    sys.exit("нужен psutil")

POLL = 0.05
K32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _FT(ctypes.Structure):
    _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]


def _q(ft) -> int:
    return (ft.hi << 32) | ft.lo


def cpu_of(pid: int) -> float | None:
    """CPU-время процесса в секундах. Работает и после выхода (хэндл держим сами)."""
    h = K32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return None
    try:
        create, exit_, kern, user = _FT(), _FT(), _FT(), _FT()
        if not K32.GetProcessTimes(h, ctypes.byref(create), ctypes.byref(exit_),
                                   ctypes.byref(kern), ctypes.byref(user)):
            return None
        return (_q(kern) + _q(user)) / 1e7
    finally:
        K32.CloseHandle(h)


def find_pid() -> int:
    """Живой экземпляр Gammadrilon: dev (pythonw main.py) или собранный exe.

    Запуск через `cmd /c start` оставляет процесс-обёртку с тем же cmdline
    (`start` сам показывает 6 МБ) — берём кандидата с наибольшим RSS.
    """
    best = None
    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            name = (p.info["name"] or "").lower()
            if name not in ("python.exe", "pythonw.exe", "gammadrilon.exe"):
                continue
            cmd = " ".join(p.info["cmdline"] or [])
            if "service" in cmd or "gd_" in cmd:
                continue
            if "main.py" not in cmd and name != "gammadrilon.exe":
                continue
            rss = (p.info["memory_info"].rss if p.info["memory_info"] else 0)
            if best is None or rss > best[0]:
                best = (rss, p.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if best is None:
        sys.exit("живой экземпляр не найден — укажи pid вручную")
    return best[1]


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else "auto"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    proc = psutil.Process(int(arg) if arg != "auto" else find_pid())
    root_pid = proc.pid

    root0 = cpu_of(root_pid) or 0.0
    kids: dict[int, dict] = {}
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < secs:
        try:
            for ch in proc.children(recursive=True):
                val = cpu_of(ch.pid)
                if val is None:
                    continue
                info = kids.get(ch.pid)
                if info is None:
                    try:
                        name = ch.name()
                    except psutil.Error:
                        name = "?"
                    # first — накопленное CPU к моменту первого взгляда: у детей,
                    # живших до окна замера (например сам exe под обёрткой start),
                    # в окно идёт только разница
                    kids[ch.pid] = {"name": name, "first": val, "last": val}
                elif val > info["last"]:
                    info["last"] = val
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        time.sleep(POLL)

    wall = time.perf_counter() - t0
    root = max((cpu_of(root_pid) or 0.0) - root0, 0.0)

    by_name: dict[str, dict] = {}
    for info in kids.values():
        rec = by_name.setdefault(info["name"].lower(), {"cpu": 0.0, "runs": 0, "seen": 0.0})
        rec["cpu"] += max(info["last"] - info["first"], 0.0)
        rec["seen"] += info["last"]
        if info["first"] > 0.5:
            rec["long"] = rec.get("long", 0) + 1    # жил до окна замера (не разовая утилита)
        else:
            rec["runs"] += 1

    kids_cpu = sum(max(i["last"] - i["first"], 0.0) for i in kids.values())
    pct = lambda c: c / wall * 100.0  # noqa: E731

    print(f"окно: {wall:.1f} с, процессов-детей увидено: {len(kids)}")
    print(f"корень (pid {root_pid} {proc.name()}):        {pct(root):.2f} % ядра")
    print(f"дети всего ({len(kids)}):                 {pct(kids_cpu):.2f} % ядра")
    for name, rec in sorted(by_name.items(), key=lambda kv: -kv[1]["cpu"]):
        long_note = f", жили до окна: {rec['long']}" if rec.get("long") else ""
        runs = f"{rec['runs']} запусков" if rec["runs"] else "без разовых"
        print(f"    {name:<14} {rec['cpu'] * 1000:8.0f} мс CPU за окно · {runs}"
              f"{long_note} → {pct(rec['cpu']):.2f} %/с")
    print(f"ИТОГО дерево:                      {pct(root + kids_cpu):.2f} % ядра")
    return 0


if __name__ == "__main__":
    sys.exit(main())
