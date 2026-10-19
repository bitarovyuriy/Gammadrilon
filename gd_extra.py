
# -*- coding: utf-8 -*-
"""Прочие утилиты (ТЗ 6.3, п.1–2): точность системного таймера и сетевой чек.

Timer resolution: прирост в основном мифический, честно показываем фактическое значение.
Сетевой чек: TCP retransmits, jitter, потери и bufferbloat (рост задержки под нагрузкой) —
то, что реально объясняет лаги, в отличие от «оптимизатора пинга».
"""
from __future__ import annotations

import gd_i18n as i18n

import ctypes
import socket
import statistics
import threading
import time
import urllib.request
from ctypes import wintypes

TIMER_SIGNATURE = (
    i18n.t("0,5 мс — в основном миф: эффект заметен лишь в отдельных играх. Действует, пока Gammadrilon запущен, и сбрасывается при выходе.")
)
NET_SIGNATURE = (
    i18n.t("Измерение, а не «оптимизатор пинга»: повторные сегменты TCP, джиттер, потери и рост задержки под нагрузкой (bufferbloat).")
)
TARGET_100NS = 5000          # 0,5 мс: 1 мс = 10000 единиц по 100 нс
#: сколько байт нагрузки считать «нагрузка была»: меньше — класс bufferbloat не считаем
MIN_LOAD_BYTES = 100_000
TCP_PROBES = [("1.1.1.1", 443), ("8.8.8.8", 53), ("77.88.8.8", 443)]
LOAD_URL = "https://speed.cloudflare.com/__down?bytes=5000000"


# -- таймер ------------------------------------------------------------------
def _ntdll():
    return ctypes.WinDLL("ntdll")


def set_timer_resolution(enabled: bool = True, target_100ns: int = TARGET_100NS):
    """Запросить/освободить точность таймера. Возвращает (ok, фактическое_100нс).

    ``ok`` — принял ли запрос сам вызов, а не «получилось ли запрошенное»: 0,5 мс
    доступны не на всех платформах, и система может дать 1 мс. Фактическое значение
    возвращается рядом именно поэтому — вызывающий обязан показать его, а не
    запрошенное (см. ``gd_extra_ui._timer_toggle``).
    """
    try:
        ntdll = _ntdll()
        ntdll.NtSetTimerResolution.argtypes = [
            ctypes.c_ulong, ctypes.c_ubyte, ctypes.POINTER(ctypes.c_ulong)]
        ntdll.NtSetTimerResolution.restype = ctypes.c_long
        cur = ctypes.c_ulong(0)
        st = ntdll.NtSetTimerResolution(ctypes.c_ulong(target_100ns),
                                        ctypes.c_ubyte(1 if enabled else 0),
                                        ctypes.byref(cur))
        time.sleep(0.05)
        now = timer_current_100ns()
        return st == 0, (now or cur.value)
    except Exception:  # noqa: BLE001
        return False, 0


def timer_current_100ns() -> int | None:
    """Фактическая точность таймера системы, в единицах по 100 нс."""
    try:
        ntdll = _ntdll()
        ntdll.NtQueryTimerResolution.argtypes = [
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong)]
        ntdll.NtQueryTimerResolution.restype = ctypes.c_long
        mn, cur, mx = ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0)
        ntdll.NtQueryTimerResolution(ctypes.byref(mn), ctypes.byref(cur), ctypes.byref(mx))
        return cur.value
    except Exception:  # noqa: BLE001
        return None


def ms_from_100ns(value: int | None) -> float | None:
    """Точность таймера из единиц по 100 нс в миллисекунды (None, если значения нет).

    Отдельная функция нужна, чтобы UI сравнивал **факт с запрошенным** и не показывал
    «0,5 мс» там, где система дала другое значение.
    """
    return round(value / 10000.0, 3) if value else None


def timer_ms() -> float | None:
    v = timer_current_100ns()
    return round(v / 10000.0, 3) if v else None


# -- сетевой чек --------------------------------------------------------------
def tcp_latency(host: str, port: int, timeout: float = 2.0) -> float | None:
    """Время установки TCP-соединения, мс (реальный SYN→SYN/ACK)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        t0 = time.perf_counter()
        s.connect((host, port))
        return (time.perf_counter() - t0) * 1000.0
    except OSError:
        return None
    finally:
        s.close()


def probe(hosts=None, samples: int = 8, timeout: float = 2.0) -> dict:
    """Медиана, jitter (ст. отклонение), потери по каждому узлу."""
    hosts = hosts or TCP_PROBES
    out = {}
    for host, port in hosts:
        vals, lost = [], 0
        for _ in range(samples):
            dt = tcp_latency(host, port, timeout)
            if dt is None:
                lost += 1
            else:
                vals.append(dt)
        key = f"{host}:{port}"
        if vals:
            out[key] = {
                "median": round(statistics.median(vals), 1),
                "jitter": round(statistics.pstdev(vals), 1) if len(vals) > 1 else 0.0,
                "min": round(min(vals), 1),
                "loss_pct": round(lost / samples * 100),
            }
        else:
            out[key] = {"median": None, "jitter": None, "min": None, "loss_pct": 100}
    return out


def tcp_retransmit_rate() -> dict | None:
    """Системные счётчики TCP: доля повторно переданных сегментов с момента загрузки."""
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi")

        class MIB_TCPSTATS(ctypes.Structure):
            _fields_ = [
                ("dwRtoAlgorithm", wintypes.DWORD), ("dwRtoMin", wintypes.DWORD),
                ("dwRtoMax", wintypes.DWORD), ("dwMaxConn", wintypes.DWORD),
                ("dwActiveOpens", wintypes.DWORD), ("dwPassiveOpens", wintypes.DWORD),
                ("dwAttemptFails", wintypes.DWORD), ("dwEstabResets", wintypes.DWORD),
                ("dwCurrEstab", wintypes.DWORD), ("dwInSegs", wintypes.DWORD),
                ("dwOutSegs", wintypes.DWORD), ("dwRetransSegs", wintypes.DWORD),
                ("dwInErrs", wintypes.DWORD), ("dwOutRsts", wintypes.DWORD),
                ("dwNumConns", wintypes.DWORD)]

        st = MIB_TCPSTATS()
        if iphlpapi.GetTcpStatisticsEx(ctypes.byref(st), 2) != 0 or not st.dwOutSegs:
            return None
        return {
            "out_segs": st.dwOutSegs, "retrans_segs": st.dwRetransSegs,
            "curr_estab": st.dwCurrEstab,
            "pct": round(st.dwRetransSegs / st.dwOutSegs * 100, 3),
        }
    except Exception:  # noqa: BLE001
        return None


def _download(url: str, size_limit: int, stop: threading.Event) -> tuple[int, str]:
    """Скачать до ``size_limit`` байт под нагрузкой.

    Возвращает ``(скачано_байт, причина_обрыва)``. Раньше отказ проглатывался, и
    «класс A» мог получиться из замера **без нагрузки вовсе** — то есть из воздуха:
    пустая причина и ноль байт теперь видны вызывающему (см. :func:`bufferbloat`).
    """
    got = 0
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            while not stop.is_set() and got < size_limit:
                chunk = resp.read(65536)
                if not chunk:
                    break
                got += len(chunk)
    except Exception as exc:  # noqa: BLE001
        return got, f"{type(exc).__name__}: {exc}"
    return got, ""


def bufferbloat(timeout: float = 2.0, samples: int = 15) -> dict:
    """Рост задержки под нагрузкой: idle-медиана → loaded-медиана до того же узла.

    Класс (A/B/C) считается только когда измерены **оба** конца: база без нагрузки и
    сама нагрузка. Иначе возвращается ``increase=None`` и ``grade="—"`` — выдуманная
    база (прежний ``median([0])``) превращала любой размер задержки под нагрузкой в
    «класс C», а «класс A» мог получиться при полностью неудавшейся загрузке.
    """
    host, port = TCP_PROBES[0]
    idle_vals = [tcp_latency(host, port, timeout) for _ in range(5)]
    idle_measured = [v for v in idle_vals if v is not None]
    idle = statistics.median(idle_measured) if idle_measured else None

    stop = threading.Event()
    load: dict = {"bytes": 0, "err": ""}

    def _do_load() -> None:
        got, err = _download(LOAD_URL, 5_000_000, stop)
        load["bytes"], load["err"] = got, err

    th = threading.Thread(target=_do_load, daemon=True)
    th.start()
    time.sleep(0.4)
    loaded_vals = []
    for _ in range(samples):
        dt = tcp_latency(host, port, timeout)
        if dt is not None:
            loaded_vals.append(dt)
    stop.set()
    th.join(timeout=3)

    out = {
        "idle": round(idle, 1) if idle is not None else None,
        "loaded": None,
        "increase": None,
        "grade": "—",
        "load_bytes": load["bytes"],
        "load_error": load["err"],
    }
    if not loaded_vals:
        return out
    loaded = statistics.median(loaded_vals)
    out["loaded"] = round(loaded, 1)
    if idle is None or load["bytes"] < MIN_LOAD_BYTES:
        # базы или нагрузки нет — считать класс не из чего
        return out
    increase = max(0.0, loaded - idle)
    out["increase"] = round(increase, 1)
    out["grade"] = "A" if increase < 30 else ("B" if increase < 100 else "C")
    return out
