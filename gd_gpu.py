
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

import csv
import json
import math
import os
import re

import gd_helper
import gd_i18n as i18n
import gd_proc

SMI = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")

#: Запасные места: старые драйверы (до 2019) кладут утилиту в Program Files,
#: а часть ноутбучных сборок — только туда. System32 остаётся первым: его
#: современный установщик драйвера обновляет сам.
SMI_FALLBACKS = (
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                 "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe"),
)


def _smi_path() -> str | None:
    """Где лежит рабочий nvidia-smi, или None."""
    for candidate in (SMI, *SMI_FALLBACKS):
        if os.path.isfile(candidate):
            return candidate
    return None


#: кэш ответа WMI: спрашиваем один раз за процесс
_adapter: dict | None = None
_adapter_checked = False


def adapter_info() -> dict | None:
    """Имя и драйвер любого GPU через WMI — для AMD/Intel и диагностики.

    Нужен, когда nvidia-smi недоступен: пустая вкладка без объяснения выглядит
    как поломка, а «найдена Radeon RX 6600, управление только для NVIDIA» —
    честный ответ. PowerShell запускается один раз, результат кэшируется.
    """
    global _adapter, _adapter_checked
    if _adapter_checked:
        return _adapter
    _adapter_checked = True
    try:
        proc = gd_proc.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object -First 1 "
             "Name,DriverVersion | ConvertTo-Json -Compress"],
            text=True, timeout=12,
        )
        data = json.loads((proc.stdout or "").strip() or "null")
        if isinstance(data, dict) and data.get("Name"):
            _adapter = {
                "name": str(data["Name"]).strip(),
                "driver": str(data.get("DriverVersion") or "").strip(),
            }
    except Exception:  # noqa: BLE001 — WMI может быть выключен, это не ошибка приложения
        pass
    return _adapter


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


#: один экземпляр NVML на процесс: nvmlInit не бесплатный
_NVML: list = []


def _nvml():
    """Ленивая инициализация NVML. None — библиотека недоступна."""
    import gd_nvml

    if not _NVML:
        try:
            _NVML.append(gd_nvml.Nvml())
        except Exception:  # noqa: BLE001 — нет NVIDIA или драйвера
            _NVML.append(None)
    return _NVML[0]


def available() -> bool:
    """Есть ли NVIDIA GPU: сначала NVML, затем nvidia-smi."""
    try:
        instance = _nvml()
        if instance is not None and instance.available():
            return True
    except Exception:  # noqa: BLE001
        pass
    if _smi_path() is None:
        return False
    try:
        return _smi(["--query-gpu=name", "--format=csv,noheader"], timeout=6) is not None
    except Exception:  # noqa: BLE001
        return False


def _smi(args: list[str], timeout: float = 8.0) -> str | None:
    path = _smi_path()
    if path is None:
        return None
    try:
        proc = gd_proc.run([path, *args], text=True, timeout=timeout)
    except (OSError, gd_proc.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


_FIELDS = (
    "name", "driver_version", "power.limit", "power.min_limit", "power.max_limit",
    "power.default_limit", "clocks.gr", "clocks.max.gr", "clocks.mem",
    "temperature.gpu", "utilization.gpu", "memory.used", "memory.total",
    "fan.speed", "uuid",
)


def valid_uuid(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(
        r"GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value, re.IGNORECASE))


def read_state() -> dict | None:
    """Снимок GPU: NVML, при недоступности — nvidia-smi. None — нет данных."""
    try:
        instance = _nvml()
        if instance is not None and instance.available():
            state = instance.read()
            if state and valid_uuid(state.get("uuid")):
                return state
    except Exception:  # noqa: BLE001 — NVML может отказать на любом шаге
        pass

    out = _smi([
        "-i", "0",
        f"--query-gpu={','.join(_FIELDS)}",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return None
    parts = [p.strip() for p in next(csv.reader(out.splitlines(), skipinitialspace=True))]
    if len(parts) != len(_FIELDS):
        return None

    def num(idx):
        try:
            value = float(parts[idx])
            return value if math.isfinite(value) else None
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
        "uuid": parts[14] if valid_uuid(parts[14]) else None,
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
