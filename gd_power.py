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

#: метка в имени своей копии схемы. Нужна для одного случая: файл состояния
#: пропал или повреждён, и без метки копию в списке схем Windows невозможно
#: отличить от пользовательской — она осталась бы в системе навсегда и молча.
CLONE_MARK = "Gammadrilon"

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

# BufferSize здесь — по значению (в PowerReadFriendlyName это указатель, поэтому
# argtypes НЕ переиспользуем: с чужим argtypes в размер уезжал мусор, и вызов
# падал access violation — проверено на живой системе)
_p.PowerWriteFriendlyName.argtypes = [ctypes.c_void_p, POINTER(GUID), POINTER(GUID),
                                      POINTER(GUID), ctypes.c_void_p, wintypes.DWORD]
_p.PowerWriteFriendlyName.restype = wintypes.DWORD

# перечисление схем питания: ACCESS_SCHEME, пока не вернётся ERROR_NO_MORE_ITEMS
_p.PowerEnumerate.argtypes = [ctypes.c_void_p, POINTER(GUID), POINTER(GUID),
                              wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                              POINTER(wintypes.DWORD)]
_p.PowerEnumerate.restype = wintypes.DWORD

ACCESS_SCHEME = 16
ERROR_NO_MORE_ITEMS = 259

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


def list_schemes() -> list[str]:
    """GUID'ы всех схем питания в системе (порядок Windows). Без прав."""
    out = []
    index = 0
    while True:
        buf = (ctypes.c_ubyte * 16)()
        size = wintypes.DWORD(16)
        # буфер — массивом (см. rename_scheme: ctypes.cast роняет вызов)
        r = _p.PowerEnumerate(None, None, None, ACCESS_SCHEME, index, buf, byref(size))
        if r != 0:                      # ERROR_NO_MORE_ITEMS или отказ — список кончился
            return out
        out.append(guid_to_str(ctypes.cast(buf, POINTER(GUID)).contents))
        index += 1


def rename_scheme(scheme_guid: str, name: str) -> bool:
    """Переименовать схему питания. False — не получилось (не повод падать)."""
    g = guid_from_str(scheme_guid)
    raw = name.encode("utf-16-le") + b"\x00\x00"
    buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    try:
        r = _p.PowerWriteFriendlyName(None, byref(g), None, None, buf,
                                      wintypes.DWORD(len(raw)))
    except OSError:
        # нативный отказ (в т.ч. access violation при неверном argtypes) — это
        # украшение имени копии, оно не повод валить применение профиля
        return False
    return r == 0


def clone_name(src_guid: str) -> str:
    """Имя своей копии: метка + имя исходной схемы (видно и в панели Windows)."""
    base = scheme_name(src_guid) or i18n.t("схема питания")
    return f"{CLONE_MARK} — {base}"[:80]


def list_clones() -> list[dict]:
    """Свои копии схем питания, оставшиеся в системе: ``[{guid, name, active}]``.

    Нужны ровно для одного случая: файл состояния пропал или повреждён, и копия
    осталась жить. Раньше о ней никто и никогда не узнавал — приложение считало,
    что профиль не применён, а схема с нашими настройками висела в списке Windows.
    """
    active = ""
    try:
        active = get_active_scheme()
    except OSError:
        pass
    out = []
    for guid in list_schemes():
        try:
            name = scheme_name(guid)
        except OSError:
            continue
        if name.startswith(CLONE_MARK):
            out.append({"guid": guid, "name": name, "active": guid == active})
    return out


def remove_clone(scheme_guid: str) -> tuple[bool, str]:
    """Убрать свою копию схемы. Возвращает (получилось?, причина отказа)."""
    try:
        if get_active_scheme() == scheme_guid:
            return False, i18n.t(
                "копия сейчас активна: Windows не даст её удалить — сначала выберите "
                "другую схему питания")
    except OSError:
        pass
    if delete_scheme(scheme_guid):
        return True, ""
    return False, i18n.t("PowerDeleteScheme не сработал")


def cleanup_clones() -> dict:
    """Убрать все свои оставшиеся копии: ``{removed, failed}`` (guid'ы копий)."""
    removed, failed = [], {}
    for clone in list_clones():
        ok, note = remove_clone(clone["guid"])
        if ok:
            removed.append(clone["guid"])
        else:
            failed[clone["guid"]] = note
    return {"removed": removed, "failed": failed}


# --------------------------------------------------------------------------
# Состояние (файл для отката; пишет тот, кто применяет профиль — компаньон)
# --------------------------------------------------------------------------

STATE_DIR = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Gammadrilon")
STATE_FILE = os.path.join(STATE_DIR, "power_state.json")


def read_state_report() -> tuple[Optional[dict], str]:
    """Состояние для отката + причина, если файлом нельзя воспользоваться.

    Возвращает ``(state|None, problem)``. Пустая причина — два разных «нормальных»
    случая: файла нет (профиль не применяли) и файл цел. Непустая — файл есть, но
    откатывать по нему нельзя: обрезанный JSON, отказ чтения или не объект.

    Молчать об этом нельзя: по этому файлу приложение решает, что «профиль не
    применён», а клон схемы с нашими настройками при этом живёт в системе.
    """
    if not os.path.exists(STATE_FILE):
        return None, ""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, f"JSON: {exc}"
    if not isinstance(data, dict):
        return None, i18n.t("в файле не объект, а {v0}", v0=type(data).__name__)
    if not data.get("original_scheme") and not data.get("clone_scheme"):
        return None, i18n.t("в файле нет ни исходной схемы, ни клона")
    return data, ""


def load_state() -> Optional[dict]:
    """Состояние отката или None (см. :func:`read_state_report`)."""
    return read_state_report()[0]


def state_problem() -> str:
    """Почему файл состояния непригоден ("" — файла нет или он цел)."""
    return read_state_report()[1]


def save_state(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def clear_state() -> bool:
    """Убрать файл состояния. False — файл остался (откат нельзя считать полным)."""
    try:
        os.remove(STATE_FILE)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


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
    state, problem = read_state_report()
    state = state or {}
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
    if problem:
        # «файл есть, но непригоден» — не то же самое, что «профиль не применён»:
        # откатывать нечем, а клон схемы с нашими настройками может быть жив
        data["state_problem"] = problem
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
    # метка в имени копии: её видно в панели управления Windows и по ней потом
    # находится остаток, если файл состояния пропадёт (см. list_clones)
    rename_scheme(clone, clone_name(original))
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


def restore_report(state: Optional[dict] = None) -> dict:
    """Откат + честный отчёт: ``{ok, applied, note, clones}``.

    * ``ok`` — откат завершён: схема возвращена, клон удалён, файл состояния убран;
    * ``applied`` — было ли что откатывать;
    * ``note`` — что именно не получилось ("" — всё хорошо);
    * ``clones`` — свои копии схем, найденные в системе (могут остаться от старой
      версии или из-за потерянного файла состояния).

    При неполном откате файл состояния **остаётся**: он и есть признак того, что
    остаток профиля жив. Значит следующая сессия попробует снова, а UI/компаньон
    скажут причину. Раньше state удалялся всегда — повторной попытки не было,
    и о неудаче не узнавал никто.
    """
    st = state if state is not None else load_state()
    problem = state_problem()
    if not st:
        clones = list_clones()
        note = ""
        if problem:
            note = i18n.t("файл состояния питания непригоден: {v0}", v0=problem)
        if clones:
            names = ", ".join(c["name"] for c in clones)
            extra = i18n.t("в системе осталась наша копия схемы питания: {v0}", v0=names)
            note = f"{note}. {extra}" if note else extra
        return {"ok": not note, "applied": False, "note": note, "clones": clones}

    problems = []
    orig = st.get("original_scheme")
    clone = st.get("clone_scheme")
    if orig and not set_active_scheme(orig):
        problems.append(i18n.t("не удалось вернуть исходную схему {v0}", v0=orig))
    if clone and clone != orig and not delete_scheme(clone):
        problems.append(i18n.t("не удалось удалить клон схемы {v0}", v0=clone))
    if problems:
        # state не трогаем: попробуем откатить снова, а не «забудем» остаток
        return {"ok": False, "applied": True, "note": "; ".join(problems), "clones": []}
    if not clear_state():
        return {"ok": False, "applied": True,
                "note": i18n.t("не удалось убрать файл состояния {v0}", v0=STATE_FILE),
                "clones": []}
    return {"ok": True, "applied": True, "note": "", "clones": []}


def restore(state: Optional[dict] = None) -> bool:
    """Вернуть исходную схему и удалить клон. Идемпотентно (см. restore_report)."""
    return restore_report(state)["ok"]


# --------------------------------------------------------------------------
# CLI для отладки: python gd_power.py read|state|schemes|clones|clean-clones|
#                 clone-test|clone-mark-test|apply <id>|restore
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
        print(json.dumps(restore_report(), indent=2, ensure_ascii=False))
    elif cmd == "state":
        st, problem = read_state_report()
        print("state:", json.dumps(st, ensure_ascii=False))
        print("problem:", problem or "—")
    elif cmd == "schemes":
        for guid in list_schemes():
            print(guid, scheme_name(guid))
    elif cmd == "clones":
        print(json.dumps(list_clones(), indent=2, ensure_ascii=False))
    elif cmd == "clean-clones":
        print(json.dumps(cleanup_clones(), indent=2, ensure_ascii=False))
    elif cmd == "clone-mark-test":
        # проверка метки на живой системе: клон создаётся, называется, удаляется
        a = get_active_scheme()
        c = duplicate_scheme(a)
        renamed = rename_scheme(c, clone_name(a))
        found = [x for x in list_clones() if x["guid"] == c]
        removed, note = remove_clone(c)
        print("clone:", c)
        print("renamed:", renamed, "| named:", scheme_name(c))
        print("found by mark:", found)
        print("removed:", removed, note)
    else:
        # i18n: skip (CLI-подсказка для разработчика, не UI)
        print("команды: read | clone-test | clone-mark-test | schemes | clones | "
              "clean-clones | state | apply <eco|normal|boost> | restore")
