# -*- coding: utf-8 -*-
"""Снимок временных изменений: чтобы сбой или выключение не оставили следов.

Приложение меняет не только свои настройки, но и состояние машины: частоту
монитора под игру, гамму, лимит GPU. Если окно упало или компьютер выключили,
пока действовал автопрофиль, настройки останутся «игровыми» — и человек
однажды включит рабочий стол на 165 Гц с контрастом для ночной игры.

Что здесь: минимальный снимок «что поставили временно и что было до этого».
Файл живёт в `%LOCALAPPDATA%\\Gammadrilon\\rollback_state.json` и помнит pid
процесса-владельца. Живой pid — изменения ещё в силе, мёртвый — хозяин
пропал, и снимок надо применить при следующем старте.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from threading import RLock

import gd_paths
import gd_state

_STATE_LOCK = RLock()


def state_path() -> Path:
    return gd_paths.data_dir() / "rollback_state.json"


def _read() -> dict | None:
    path = state_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save(entries: dict, pid: int | None = None) -> bool:
    """Записать снимок. `entries` — разделы: hz, gamma, preset, gpu, dns."""
    if not entries:
        return clear()
    data = dict(entries)
    data["pid"] = int(pid if pid is not None else _current_pid())
    data["at"] = time.time()
    with _STATE_LOCK:
        return gd_state.write_json_atomic(state_path(), data)


def load() -> dict | None:
    return _read()


def clear() -> bool:
    with _STATE_LOCK:
        try:
            state_path().unlink(missing_ok=True)
            return True
        except OSError:
            return False


def merge(section: str, values: dict) -> bool:
    """Дописать раздел, не потеряв остальные."""
    with _STATE_LOCK:
        data = _read() or {}
        data[section] = values
        data["pid"] = _current_pid()
        data["at"] = time.time()
        return gd_state.write_json_atomic(state_path(), data)


def saved_hz(data: dict | None) -> int:
    """Прочитать как текущую вложенную запись, так и прежнее число."""
    value = (data or {}).get("hz")
    if isinstance(value, dict):
        value = value.get("hz")
    return value if type(value) is int and value > 0 else 0


def discard_hz(expected: dict) -> bool:
    """Удалить восстановленную частоту, сохранив остальные разделы снимка."""
    with _STATE_LOCK:
        data = _read() or {}
        if any(data.get(key) != expected.get(key) for key in ("hz", "pid", "at")):
            return False
        data.pop("hz", None)
        if not (data.keys() - {"pid", "at"}):
            return clear()
        return gd_state.write_json_atomic(state_path(), data)


def _current_pid() -> int:
    import os

    return os.getpid()


def owner_alive(data: dict | None = None) -> bool:
    """Жив ли процесс, который оставил снимок."""
    data = data if data is not None else _read()
    if not data:
        return False
    pid = data.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001 — без psutil считаем, что хозяин пропал
        return False


def orphaned(data: dict | None = None) -> dict | None:
    """Снимок, чей хозяин уже не работает (или файл битый)."""
    data = data if data is not None else _read()
    if not data or owner_alive(data):
        return None
    return data


def describe(data: dict | None) -> str:
    """Короткая строка для журнала: что именно откатываем."""
    if not data:
        return ""
    parts = []
    hertz = saved_hz(data)
    if hertz:
        parts.append(f"частота {hertz} Гц")
    if data.get("preset") is not None:
        parts.append(f"пресет #{int(data['preset']) + 1}")
    if data.get("gamma") is not None:
        parts.append("гамма")
    if data.get("gpu"):
        parts.append("лимит GPU")
    if data.get("dns"):
        parts.append("DNS")
    return ", ".join(parts) if parts else "ничего"
