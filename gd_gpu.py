
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

import gd_i18n as i18n

import os
import subprocess
import sys

SMI = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
PIPE_NAME = r"\\.\pipe\GammadrilonGpu"

# Связь UI ↔ elevated-хелпер. Именованный канал, созданный процессом с правами
# администратора, обычному (не-elevated) клиенту недоступен — Windows отдаёт
# «Отказано в доступе» на уровне ACL. Поэтому хелпер слушает только loopback,
# а целостность связи обеспечивает встроенный handshake (authkey) из
# multiprocessing.connection.
TCP_HOST = "127.0.0.1"
TCP_PORT = 47318


def new_token() -> str:
    """Случайный ключ сессии: UI передаёт его хелперу при запуске."""
    import secrets

    return secrets.token_hex(16)


HERE = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Gammadrilon", "gpu_session.json"
)


class GpuClient:
    """Клиент elevated-хелпера: подключение по loopback, запуск через UAC."""

    def __init__(self) -> None:
        self._conn = None
        self.port: int | None = None
        self.token: str | None = None

    # -- сессия ------------------------------------------------------------
    def _load_session(self) -> dict | None:
        try:
            import json

            with open(SESSION_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _save_session(self, port: int, token: str) -> None:
        try:
            import json

            os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
            with open(SESSION_FILE, "w", encoding="utf-8") as fh:
                json.dump({"port": port, "token": token}, fh)
        except OSError:
            pass

    # -- соединение --------------------------------------------------------
    def _connect(self, port: int, token: str) -> bool:
        from multiprocessing.connection import Client

        try:
            conn = Client((TCP_HOST, port), family="AF_INET", authkey=bytes.fromhex(token))
        except OSError:
            return False
        self._conn = conn
        self.port = port
        self.token = token
        return True

    def ensure(self) -> bool:
        if self._conn is not None:
            return True
        sess = self._load_session()
        if sess and self._connect(int(sess["port"]), sess["token"]):
            return True

        port = TCP_PORT
        token = new_token()
        self._save_session(port, token)
        self._launch(port, token)
        import time

        for _ in range(60):
            time.sleep(0.25)
            if self._connect(port, token):
                return True
        return False

    def _launch(self, port: int, token: str) -> None:
        """Запустить хелпер с правами администратора (однократный UAC)."""
        svc = os.path.join(HERE, "gd_gpu_service.py")
        if getattr(sys, "frozen", False):
            # в собранном exe сервис — тот же исполняемый файл с флагом режима
            args = (
                f"-ArgumentList '--gpu-service','--port','{port}','--token','{token}',"
                f"'--ui-pid','{os.getpid()}'"
            )
        else:
            args = (
                f"-ArgumentList '-u','{svc}','--port','{port}','--token','{token}',"
                f"'--ui-pid','{os.getpid()}'"
            )
        cmd = (
            f"Start-Process -FilePath '{sys.executable}' "
            f"{args} -Verb RunAs -WindowStyle Hidden"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", cmd],
            creationflags=0x08000000,
        )

    # -- протокол ----------------------------------------------------------
    def call(self, obj: dict, timeout: float = 15.0) -> dict:
        if not self.ensure():
            return {"ok": False, "error": i18n.t("хелпер GPU не запустился (нужны права администратора)")}
        try:
            self._conn.send(obj)
            if not self._conn.poll(timeout):
                self._conn = None
                return {"ok": False, "error": i18n.t("таймаут ответа хелпера")}
            return self._conn.recv()
        except (OSError, EOFError):
            self._conn = None
            return {"ok": False, "error": i18n.t("связь с хелпером потеряна")}

    def shutdown(self, restore: bool = True) -> None:
        if self._conn is not None:
            try:
                self.call({"cmd": "shutdown", "restore": restore})
            except Exception:  # noqa: BLE001
                pass
        self._conn = None

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
        proc = subprocess.run(
            [SMI, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except (OSError, subprocess.TimeoutExpired):
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
