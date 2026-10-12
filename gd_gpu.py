
# -*- coding: utf-8 -*-
"""Gammadrilon v3 — ядро управления GPU (NVIDIA).

Чтение состояния — без администратора (nvidia-smi query).
Установка лимита мощности / частот — только администратором: драйвер NVIDIA
отвечает «Insufficient Permissions» обычному пользователю. Поэтому запись
делает elevated-компаньон `gd_gpu_service.py`, а этот модуль — тонкая обёртка
над nvidia-smi для чтения.

Интерфейс намеренно совпадает по духу с gd_power.py: read_state() → dict,
нормализованные профили, никаких магических чисел.
"""
from __future__ import annotations

import os

import gd_helper
import gd_i18n as i18n
import gd_proc

SMI = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
PIPE_NAME = r"\\.\pipe\GammadrilonGpu"

# Связь UI ↔ elevated-хелпер. Именованный канал, созданный процессом с правами
# администратора, обычному (не-elevated) клиенту недоступен — Windows отдаёт
# «Отказано в доступе» на уровне ACL. Поэтому хелпер слушает только loopback,
# а целостность связи обеспечивает встроенный handshake (authkey) из
# multiprocessing.connection.
#
# Клиент живёт в gd_helper (общий для GPU/памяти/DNS); здесь — реэкспорт для
# совместимости с gd_gpu_service и UI-модулями.
TCP_HOST = gd_helper.TCP_HOST
TCP_PORT = gd_helper.TCP_PORT
HERE = gd_helper.HERE
SESSION_FILE = gd_helper.SESSION_FILE
new_token = gd_helper.new_token


class GpuClient(gd_helper.HelperClient):
    """Клиент elevated-хелпера (обратная совместимость имени).

    Поведение полностью наследуется от gd_helper.HelperClient: подключение по
    loopback, однократный UAC, устойчивый call() без исключений.
    """

# Профили GPU — ярлыки над слайдерами (ТЗ 4.4). Значения — цель по лимиту
# мощности в ваттах; None = «взять у драйвера» (default/max).
GPU_PROFILES = {
    "quiet":   {"label": i18n.t("Тихий"),  "pl": 150},
    "default": {"label": i18n.t("Дефолт"), "pl": None},   # default_limit драйвера
    "game":    {"label": i18n.t("Игровой"), "pl": None},  # 93 % от max
    "max":     {"label": i18n.t("Макс"),   "pl": None},   # максимум драйвера
}


class GpuUnavailable(RuntimeError):
    pass


def available() -> bool:
    """Есть ли NVIDIA GPU и рабочий nvidia-smi."""
    if not os.path.isfile(SMI):
        return False
    try:
        return _smi(["--query-gpu=name", "--format=csv,noheader"], timeout=6) is not None
    except Exception:  # noqa: BLE001
        return False


def _smi(args: list[str], timeout: float = 8.0) -> str | None:
    try:
        proc = gd_proc.run([SMI, *args], text=True, timeout=timeout)
    except (OSError, gd_proc.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


_FIELDS = (
    "name", "driver_version", "power.limit", "power.min_limit", "power.max_limit",
    "power.default_limit", "clocks.gr", "clocks.max.gr", "clocks.mem",
    "temperature.gpu", "utilization.gpu", "memory.used", "memory.total",
    "fan.speed",
)


def read_state() -> dict | None:
    """Снимок GPU. None — если nvidia-smi недоступен."""
    out = _smi([
        f"--query-gpu={','.join(_FIELDS)}",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return None
    parts = [p.strip() for p in out.splitlines()[0].split(",")]
    if len(parts) < len(_FIELDS):
        return None

    def num(idx):
        try:
            return float(parts[idx])
        except (ValueError, IndexError):
            return None

    return {
        "name": parts[0],
        "driver": parts[1],
        "pl": num(2),
        "pl_min": num(3),
        "pl_max": num(4),
        "pl_default": num(5),
        "clock": num(6),
        "clock_max": num(7),
        "clock_mem": num(8),
        "temp": num(9),
        "util": num(10),
        "mem_used": num(11),
        "mem_total": num(12),
        "fan": num(13),
    }


def profile_target(state: dict, name: str) -> dict:
    """Что профиль должен сделать, с учётом фактических min/max/default."""
    spec = GPU_PROFILES.get(name)
    if spec is None or state is None:
        return {}
    target = {}
    pl = spec["pl"]
    if name == "default":
        pl = state.get("pl_default") or state.get("pl")
    elif name == "game":
        mx = state.get("pl_max") or state.get("pl")
        pl = round(mx * 0.93) if mx else None
    elif name == "max":
        pl = state.get("pl_max") or state.get("pl")
    if pl is not None:
        lo = state.get("pl_min") or 0
        hi = state.get("pl_max") or pl
        target["pl"] = int(max(lo, min(hi, pl)))
    return target


def _fmt(v, suffix=""):
    return "—" if v is None else f"{v:.0f}{suffix}"
