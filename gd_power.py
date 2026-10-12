# -*- coding: utf-8 -*-
"""Ядро управления профилями питания CPU через powrprof (Windows).

Уровни доступа:
  * чтение — без прав (активная схема, значения настроек), см. :func:`state_snapshot`;
  * запись/клонирование/активация — прав тоже не требует, но делается компаньоном
    (gd_power_service.py): он живёт дольше UI и откатывает схему, если UI умрёт.

Подтверждено на живой системе (Win11 26200, Ryzen 5 5600, 2026-10-09):
  * все GUID'ы найдены в PowerEnumerate подгруппы SUB_PROCESSOR (95 настроек);
  * PERFEPP — шкала 0–100 %: 0 = производительность, 100 = энергосбережение
    (energy performance preference register: 0 -> favor performance,
     100 -> favor energy savings);
  * PERFBOOSTMODE: 0 Disabled / 1 Enabled / 2 Aggressive / 3 Efficient Enabled
    / 4 Efficient Aggressive / 5,6 ... At Guaranteed;
  * SYSCOOLPOL: 0 Passive / 1 Active.
"""

from __future__ import annotations

import ctypes
import json
import os
import time
import uuid
from ctypes import POINTER, byref, wintypes
from typing import Optional

import gd_i18n as i18n

# --------------------------------------------------------------------------
# GUID'ы (подтверждены на живой системе, см. шапку файла)
# --------------------------------------------------------------------------

SUB_PROCESSOR = "54533251-82be-4824-96c1-47b60b740d00"

SETTINGS = {
    "max_state":    "bc5038f7-23e0-4960-96da-33abaf5935ec",  # макс. состояние, %
    "min_state":    "893dee8e-2bef-41e0-89c6-b55d0929964c",  # мин. состояние, %
    "boost_mode":   "be337238-0d82-4146-a960-4f3749d470c7",  # режим усиления
    "epp":          "36687f9e-e3a5-4dbf-b1dc-15eb381c6863",  # 0=perf .. 100=power
    "cool_policy":  "94d3a615-a899-4ac5-ae2b-e4d8f634367f",  # 0=passive, 1=active
}

# --------------------------------------------------------------------------
# Профили
# --------------------------------------------------------------------------
# EPP-шкала адаптирована под современную Windows (0–100 %, 0 = макс.
# производительность, 100 = макс. энергосбережение) — не старая 0–4.

PROFILES = {
    "eco":    {"max_state": 50,  "boost_mode": 0, "epp": 100, "cool_policy": 0},
    "normal": {"max_state": 99,  "boost_mode": 1, "epp": 50,  "cool_policy": 1},
    "boost":  {"max_state": 100, "boost_mode": 2, "epp": 0,   "cool_policy": 1},
}

# --------------------------------------------------------------------------
# ctypes / powrprof
# --------------------------------------------------------------------------


class GUID(ctypes.Structure):
    _fields_ = [
        ("d1", wintypes.DWORD),
        ("d2", wintypes.WORD),
        ("d3", wintypes.WORD),
        ("d4", ctypes.c_ubyte * 8),
    ]


def guid_from_str(s: str) -> GUID:
    u = uuid.UUID(s)
    return GUID(u.time_low, u.time_mid, u.time_hi_version,
                (ctypes.c_ubyte * 8)(*u.bytes[8:]))


def guid_to_str(g: GUID) -> str:
    return "%08X-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X" % (
        g.d1, g.d2, g.d3, g.d4[0], g.d4[1], g.d4[2],
        g.d4[3], g.d4[4], g.d4[5], g.d4[6], g.d4[7])


_p = ctypes.WinDLL("powrprof.dll")

_p.PowerGetActiveScheme.argtypes = [ctypes.c_void_p, POINTER(POINTER(GUID))]
_p.PowerGetActiveScheme.restype = wintypes.DWORD

_p.PowerReadACValueIndex.argtypes = [ctypes.c_void_p, POINTER(GUID), POINTER(GUID),
                                     POINTER(GUID), POINTER(wintypes.DWORD)]
_p.PowerReadACValueIndex.restype = wintypes.DWORD
_p.PowerReadDCValueIndex.argtypes = _p.PowerReadACValueIndex.argtypes
_p.PowerReadDCValueIndex.restype = wintypes.DWORD

_p.PowerWriteACValueIndex.argtypes = [ctypes.c_void_p, POINTER(GUID), POINTER(GUID),
                                      POINTER(GUID), wintypes.DWORD]
_p.PowerWriteACValueIndex.restype = wintypes.DWORD
_p.PowerWriteDCValueIndex.argtypes = _p.PowerWriteACValueIndex.argtypes
_p.PowerWriteDCValueIndex.restype = wintypes.DWORD

_p.PowerDuplicateScheme.argtypes = [ctypes.c_void_p, POINTER(GUID), POINTER(POINTER(GUID))]
_p.PowerDuplicateScheme.restype = wintypes.DWORD

_p.PowerDeleteScheme.argtypes = [ctypes.c_void_p, POINTER(GUID)]
_p.PowerDeleteScheme.restype = wintypes.DWORD

_p.PowerSetActiveScheme.argtypes = [ctypes.c_void_p, POINTER(GUID)]
_p.PowerSetActiveScheme.restype = wintypes.DWORD

_p.PowerReadFriendlyName.argtypes = [ctypes.c_void_p, POINTER(GUID), POINTER(GUID),
                                     POINTER(GUID), ctypes.c_void_p, POINTER(wintypes.DWORD)]
_p.PowerReadFriendlyName.restype = wintypes.DWORD

_SUB = guid_from_str(SUB_PROCESSOR)
_G = {name: guid_from_str(s) for name, s in SETTINGS.items()}

_local_free = ctypes.windll.kernel32.LocalFree

# --------------------------------------------------------------------------
# Операции
# --------------------------------------------------------------------------


def get_active_scheme() -> str:
    """GUID активной схемы питания, строка в верхнем регистре."""
    pg = POINTER(GUID)()
    r = _p.PowerGetActiveScheme(None, byref(pg))
    if r != 0:
        raise OSError(i18n.t("PowerGetActiveScheme: код {v0}", v0=r))
    try:
        return guid_to_str(pg.contents)
    finally:
        _local_free(ctypes.cast(pg, ctypes.c_void_p))


def _read_index(scheme: GUID, setting: GUID, ac: bool) -> Optional[int]:
    v = wintypes.DWORD(0)
    fn = _p.PowerReadACValueIndex if ac else _p.PowerReadDCValueIndex
    r = fn(None, byref(scheme), byref(_SUB), byref(setting), byref(v))
    return v.value if r == 0 else None


def read_values(scheme_guid: str) -> dict:
    """Все наши настройки из схемы: {имя: {'ac': int|None, 'dc': int|None}}."""
    sg = guid_from_str(scheme_guid)
    return {name: {"ac": _read_index(sg, g, True), "dc": _read_index(sg, g, False)}
            for name, g in _G.items()}


def write_values(scheme_guid: str, values: dict) -> dict:
    """Пишет значения в схему (AC и DC). Возвращает {'ok': [...], 'failed': {...}}."""
    sg = guid_from_str(scheme_guid)
    ok, failed = [], {}
    for name, val in values.items():
        g = _G[name]
        errs = []
        for ac in (True, False):
            fn = _p.PowerWriteACValueIndex if ac else _p.PowerWriteDCValueIndex
            r = fn(None, byref(sg), byref(_SUB), byref(g), wintypes.DWORD(int(val)))
            if r != 0:
                errs.append((("AC" if ac else "DC"), r))
        if errs:
            failed[name] = errs
        else:
            ok.append(name)
    return {"ok": ok, "failed": failed}


def duplicate_scheme(src_guid: str) -> str:
    """Клонирует схему, возвращает GUID клона."""
    src = guid_from_str(src_guid)
    out = POINTER(GUID)()
    r = _p.PowerDuplicateScheme(None, byref(src), byref(out))
    if r != 0:
        raise OSError(i18n.t("PowerDuplicateScheme: код {v0}", v0=r))
    try:
        return guid_to_str(out.contents)
    finally:
        _local_free(ctypes.cast(out, ctypes.c_void_p))


def delete_scheme(scheme_guid: str) -> bool:
    g = guid_from_str(scheme_guid)
    return _p.PowerDeleteScheme(None, byref(g)) == 0


def set_active_scheme(scheme_guid: str) -> bool:
    g = guid_from_str(scheme_guid)
    return _p.PowerSetActiveScheme(None, byref(g)) == 0


def scheme_name(scheme_guid: str) -> str:
    """Читаемое (локализованное) имя схемы питания."""
    g = guid_from_str(scheme_guid)
    sz = wintypes.DWORD(0)
    if _p.PowerReadFriendlyName(None, byref(g), None, None, None, byref(sz)) != 0 or not sz.value:
        return ""
    buf = (ctypes.c_ubyte * sz.value)()
    if _p.PowerReadFriendlyName(None, byref(g), None, None, buf, byref(sz)) != 0:
        return ""
    try:
        return bytes(buf).decode("utf-16-le").rstrip("\x00")
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------
# Состояние (файл для отката; пишет тот, кто применяет профиль — компаньон)
# --------------------------------------------------------------------------

STATE_DIR = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Gammadrilon")
STATE_FILE = os.path.join(STATE_DIR, "power_state.json")


def load_state() -> Optional[dict]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def clear_state() -> None:
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Чтение состояния (одинаково для UI и компаньона)
# --------------------------------------------------------------------------


def state_snapshot() -> dict:
    """Снимок состояния питания: активная схема, профиль из state, значения.

    Отвечает на тот же вопрос, что команда ``status`` компаньона, но выполняется
    в самом вызывающем процессе: powercfg читается через WinAPI обычным
    пользователем (``PowerGetActiveScheme``/``PowerReadACValueIndex``), без прав
    администратора и без внешних процессов. Поэтому вкладке CPU не нужно
    поднимать компаньона, чтобы просто показать текущее состояние — он нужен
    только для записи (``apply_profile``/``restore`` под присмотром watchdog'а).

    Raises:
        OSError: powercfg не отдал активную схему.
    """
    active = get_active_scheme()
    state = load_state() or {}
    data: dict = {
        "active_scheme": active,
        "profile": None,
        "clone_scheme": None,
        "values": read_values(active),
    }
    try:
        data["active_name"] = scheme_name(active)
    except OSError:
        data["active_name"] = ""
    if state:
        data["profile"] = state.get("profile")
        data["clone_scheme"] = state.get("clone_scheme")
        data["original_scheme"] = state.get("original_scheme")
        data["applied_at"] = state.get("applied_at")
        orig = state.get("original_scheme")
        try:
            data["original_values"] = read_values(orig) if orig else None
        except OSError:
            data["original_values"] = None
    return data


# --------------------------------------------------------------------------
# Высокоуровневые операции (для компаньона)
# --------------------------------------------------------------------------


def apply_profile(profile_id: str, custom: Optional[dict] = None) -> dict:
    """Клонирует активную схему, пишет профиль, активирует клон.

    Возвращает state-словарь (сохраняется в STATE_FILE для отката).
    При любой ошибке клон удаляется, активная схема не меняется.
    """
    if profile_id not in PROFILES and profile_id != "custom":
        raise ValueError(i18n.t("неизвестный профиль: {v0}", v0=profile_id))
    values = dict(custom if profile_id == "custom" else PROFILES[profile_id])

    original = get_active_scheme()
    clone = duplicate_scheme(original)
    try:
        res = write_values(clone, values)
        if res["failed"]:
            raise OSError(i18n.t("не удалось записать настройки: {v0}", v0=res['failed']))
        if not set_active_scheme(clone):
            raise OSError(i18n.t("PowerSetActiveScheme не сработал"))
    except Exception:
        delete_scheme(clone)
        raise

    state = {
        "version": 1,
        "original_scheme": original,
        "clone_scheme": clone,
        "profile": profile_id,
        "custom": custom,
        "values": values,
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_state(state)
    return state


def restore(state: Optional[dict] = None) -> bool:
    """Возвращает исходную схему и удаляет клон. Идемпотентно."""
    st = state if state is not None else load_state()
    if not st:
        return False
    ok = True
    orig = st.get("original_scheme")
    clone = st.get("clone_scheme")
    if orig:
        ok = set_active_scheme(orig) and ok
    if clone and clone != orig:
        ok = delete_scheme(clone) and ok
    clear_state()
    return ok


# --------------------------------------------------------------------------
# CLI для отладки: python gd_power.py read|clone-test|apply <id>|restore
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "read"

    if cmd == "read":
        a = get_active_scheme()
        print("active:", a)
        print(json.dumps(read_values(a), indent=2, ensure_ascii=False))
    elif cmd == "clone-test":
        a = get_active_scheme()
        c = duplicate_scheme(a)
        print("original:", a)
        print("clone:", c)
        print("clone values:", json.dumps(read_values(c), ensure_ascii=False))
        print("deleted:", delete_scheme(c))
    elif cmd == "apply":
        print(json.dumps(apply_profile(sys.argv[2]), indent=2, ensure_ascii=False))
    elif cmd == "restore":
        print("restored:", restore())
    else:
        # i18n: skip (CLI-подсказка для разработчика, не UI)
        print("команды: read | clone-test | apply <eco|normal|boost> | restore")
