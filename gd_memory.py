
# -*- coding: utf-8 -*-
"""Очистка ОЗУ и таблица памяти (ТЗ 6.1).

Честное имя функции: «Очистить standby list и рабочие наборы». Это не «ускорить ПК».

- standby list: `NtSetSystemInformation(SystemMemoryListInformation, MemoryPurgeStandbyList)`
  — требует привилегии `SeProfileSingleProcessPrivilege`, то есть прав администратора;
- рабочие наборы: `EmptyWorkingSet` по процессам — для чужих процессов требует
  `SeIncreaseQuotaPrivilege`.

Оба действия выполняет elevated-хелпер (у пользователя UAC может быть настроен без
диалога). Без прав — понятная ошибка, никакого «тихого no-op».

Таблица памяти — через счётчики PDH `\\Memory\\*` (Standby = сумма трёх Standby Cache,
Free = Free & Zero Page List) и `GetPerformanceInfo` (Total).
"""
from __future__ import annotations

import gd_i18n as i18n

import ctypes
import ctypes.wintypes as wt
import time

try:
    import psutil
except Exception:  # noqa: BLE001
    psutil = None

k32 = ctypes.WinDLL("kernel32")
advapi32 = ctypes.WinDLL("advapi32")
psapi = ctypes.WinDLL("psapi")
ntdll = ctypes.WinDLL("ntdll")
pdh = ctypes.WinDLL("pdh")

# --- привилегии -------------------------------------------------------------
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x0002
ERROR_NOT_ALL_ASSIGNED = 1300


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wt.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wt.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]


k32.GetCurrentProcess.restype = wt.HANDLE
k32.GetCurrentProcess.argtypes = []
advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi32.OpenProcessToken.restype = wt.BOOL
advapi32.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.POINTER(LUID)]
advapi32.LookupPrivilegeValueW.restype = wt.BOOL
advapi32.AdjustTokenPrivileges.argtypes = [
    wt.HANDLE, wt.BOOL, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p
]
advapi32.AdjustTokenPrivileges.restype = wt.BOOL
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
psapi.EmptyWorkingSet.argtypes = [wt.HANDLE]
psapi.EmptyWorkingSet.restype = wt.BOOL
ntdll.NtSetSystemInformation.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong]
ntdll.NtSetSystemInformation.restype = ctypes.c_long

SystemMemoryListInformation = 0x50
MemoryPurgeStandbyList = 4
STATUS_SUCCESS = 0

PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

NTSTATUS_TEXT = {
    0: i18n.t("успех"),
    -1073741727: i18n.t("нужны права администратора (STATUS_PRIVILEGE_NOT_HELD)"),
    -1073741790: i18n.t("доступ запрещён"),
}


def enable_privilege(name: str) -> tuple[bool, str]:
    """Включить привилегию в текущем токене. Честно: 1300 означает «не назначена»."""
    h = wt.HANDLE()
    if not advapi32.OpenProcessToken(
        k32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(h)
    ):
        return False, i18n.t("не удалось открыть токен процесса (код {v0})",
                             v0=ctypes.get_last_error())
    luid = LUID()
    if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
        return False, i18n.t("привилегия {v0} не найдена в системе", v0=name)
    tp = TOKEN_PRIVILEGES()
    tp.PrivilegeCount = 1
    tp.Privileges[0].Luid = luid
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
    ctypes.set_last_error(0)
    ok = advapi32.AdjustTokenPrivileges(h, False, ctypes.byref(tp), 0, None, None)
    err = ctypes.get_last_error()
    if not ok:
        return False, i18n.t("AdjustTokenPrivileges не сработал (код {v0})", v0=err)
    if err == ERROR_NOT_ALL_ASSIGNED:
        return False, i18n.t("привилегии нет в токене — нужен запуск от администратора")
    return True, i18n.t("включена")


def purge_standby_native() -> tuple[bool, str]:
    """Очистить standby list. Из обычного процесса обычно упирается в права."""
    for priv in ("SeProfileSingleProcessPrivilege", "SeIncreaseQuotaPrivilege"):
        enable_privilege(priv)
    cmd = ctypes.c_ulong(MemoryPurgeStandbyList)
    ret = ntdll.NtSetSystemInformation(
        SystemMemoryListInformation, ctypes.byref(cmd), ctypes.sizeof(cmd)
    )
    if ret == STATUS_SUCCESS:
        return True, i18n.t("standby list очищен")
    return False, NTSTATUS_TEXT.get(ret, i18n.t("код 0x{v0:08X}", v0=ret & 0xFFFFFFFF))


def empty_working_sets_native(pids: list[int]) -> tuple[bool, str, int]:
    """Сжать рабочие наборы указанных процессов. Возвращает (успех, сообщение, штук)."""
    done = 0
    failed = 0
    for pid in pids:
        h = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            failed += 1
            continue
        try:
            if psapi.EmptyWorkingSet(h):
                done += 1
            else:
                failed += 1
        finally:
            k32.CloseHandle(h)
    if done == 0:
        return False, i18n.t("ни один процесс не удалось сжать (отказов: {v0})", v0=failed), 0
    msg = i18n.t("сжато процессов: {v0}", v0=done)
    if failed:
        msg += i18n.t(", отказов: {v0} (чужие процессы требуют прав администратора)",
                      v0=failed)
    return True, msg, done


# --- таблица памяти ---------------------------------------------------------
PDH_FMT_DOUBLE = 0x00000200


class _PDH_VALUE(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_ulong), ("doubleValue", ctypes.c_double)]


class _PERF_INFO(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("CommitTotal", ctypes.c_size_t), ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t), ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t), ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t), ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t), ("PageSize", ctypes.c_size_t),
        ("HandleCount", wt.DWORD), ("ProcessCount", wt.DWORD), ("ThreadCount", wt.DWORD),
    ]


psapi.GetPerformanceInfo.argtypes = [ctypes.c_void_p, wt.DWORD]
psapi.GetPerformanceInfo.restype = wt.BOOL


class MemorySensors:
    """Один экземпляр на процесс; sample() можно звать раз в секунду."""

    PATHS = {
        "Available": r"\Memory\Available Bytes",
        "Modified": r"\Memory\Modified Page List Bytes",
        "StandbyCore": r"\Memory\Standby Cache Core Bytes",
        "StandbyNormal": r"\Memory\Standby Cache Normal Priority Bytes",
        "StandbyReserve": r"\Memory\Standby Cache Reserve Bytes",
        "Free": r"\Memory\Free & Zero Page List Bytes",
    }

    def __init__(self) -> None:
        self._q = ctypes.c_void_p()
        self._counters: dict[str, ctypes.c_void_p] = {}
        self._ok = False
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._q)) != 0:
            return
        for name, path in self.PATHS.items():
            c = ctypes.c_void_p()
            if pdh.PdhAddEnglishCounterW(self._q, path, 0, ctypes.byref(c)) == 0:
                self._counters[name] = c
        self._ok = "Available" in self._counters
        if self._ok:
            pdh.PdhCollectQueryData(self._q)
            time.sleep(0.15)
            pdh.PdhCollectQueryData(self._q)

    def _read(self, name: str):
        c = self._counters.get(name)
        if not c:
            return None
        val = _PDH_VALUE()
        t = ctypes.c_ulong()
        if pdh.PdhGetFormattedCounterValue(c, PDH_FMT_DOUBLE, ctypes.byref(t), ctypes.byref(val)) != 0:
            return None
        return val.doubleValue

    def sample(self) -> dict:
        """Байты: Total / In Use / Modified / Standby / Free."""
        pi = _PERF_INFO()
        pi.cb = ctypes.sizeof(pi)
        psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb)
        total = pi.PhysicalTotal * pi.PageSize if pi.PhysicalTotal else None

        if self._ok:
            pdh.PdhCollectQueryData(self._q)
        avail = self._read("Available")
        modified = self._read("Modified")
        free = self._read("Free")
        standby_parts = [self._read(n) for n in ("StandbyCore", "StandbyNormal", "StandbyReserve")]
        standby = None
        if any(p is not None for p in standby_parts):
            standby = sum(p or 0 for p in standby_parts)

        in_use = None
        if total is not None and avail is not None:
            in_use = total - avail
        return {
            "Total": total,
            "In Use": in_use,
            "Modified": modified,
            "Standby": standby,
            "Free": free,
        }

    def close(self) -> None:
        if self._q:
            pdh.PdhCloseQuery(self._q)
            self._q = ctypes.c_void_p()


# подпись обязательно показывается рядом с кнопкой (ТЗ 6.1)
SIGNATURE = (
    i18n.t("Очищает кэш файлов, который Windows держит намеренно. Может убрать микрофризы при нехватке памяти; в остальных случаях даст просадку — кэш придётся читать с диска заново. Это не освобождение «занятой» памяти и не ускорение системы.")
)

AUTO_DEFAULTS = {"standby_mb": 1024, "free_mb": 1024}


def list_processes(limit: int = 40) -> list[dict]:
    """Процессы по убыванию памяти: [{'pid','name','rss'}]."""
    if psutil is None:
        return []
    out = []
    try:
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                info = p.info
                rss = getattr(info.get("memory_info"), "rss", 0) or 0
                if rss <= 0:
                    continue
                out.append({"pid": info["pid"], "name": info.get("name") or "?", "rss": rss})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:  # noqa: BLE001
        return []
    out.sort(key=lambda x: -x["rss"])
    return out[:limit]
