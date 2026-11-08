# -*- coding: utf-8 -*-
"""Игровой буст и умная очистка ОЗУ.

Почему не «кнопка очистить всё»: purge standby list вымывает файловый кэш,
включая только что загруженные шейдеры и текстуры самой игры — после этого
подгрузка идёт с диска и появляются фризы. Работающий приём другой:

1. сжать рабочие наборы **фонового** софта (браузеры, мессенджеры, Steam);
2. понизить приоритет их страниц, чтобы ядро вытесняло их, а не игру;
3. включить им EcoQoS, чтобы не занимали буст частот;
4. standby list трогать только когда свободной памяти мало И кэш разросся;
5. всё изменённое — откатывать, в том числе после падения приложения.

Модуль ничего не знает про Qt: ядро можно проверять из консоли и вызывать из
компаньона.
"""
from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes as wt
from pathlib import Path

import psutil

import gd_i18n as i18n
import gd_memory
import gd_paths

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

# --- права и константы WinAPI ----------------------------------------------
PROCESS_SET_QUOTA = 0x0100
PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

ProcessMemoryPriority = 0
ProcessPowerThrottling = 4

MEMORY_PRIORITY_VERY_LOW = 1
MEMORY_PRIORITY_LOW = 2
MEMORY_PRIORITY_MEDIUM = 3
MEMORY_PRIORITY_NORMAL = 5

PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1

HIGH_PRIORITY_CLASS = 0x00000080
NORMAL_PRIORITY_CLASS = 0x00000020

ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87

#: системные процессы: их рабочий набор трогать нельзя ни при каких условиях
SYSTEM_NAMES = frozenset({
    "system", "registry", "memcompression", "smss.exe", "csrss.exe",
    "wininit.exe", "services.exe", "lsass.exe", "svchost.exe", "dwm.exe",
    "explorer.exe", "audiodg.exe", "winws.exe", "gammadrilon.exe",
})

#: типичные фоновые пожиратели памяти, которых бустим «вниз»
BACKGROUND_NAMES = frozenset({
    "chrome.exe", "msedge.exe", "browser.exe", "firefox.exe", "opera.exe",
    "vivaldi.exe", "yandexbrowser.exe", "discord.exe", "steamwebhelper.exe",
    "telegram.exe", "spotify.exe", "slack.exe", "teams.exe", "zoom.exe",
})

#: ядерные античиты: с их процессами и с процессом игры рядом не работаем
ANTICHEAT_NAMES = frozenset({
    "vgc.exe", "vgtray.exe", "vgk.exe", "easyanticheat.exe", "eac_launcher.exe",
    "beservice.exe", "bedaisy.exe", "faceit.exe", "faceitclient.exe",
    "battleye.exe", "anticheatexpert.exe",
})

#: процесс игры не открываем с правами записи, если рядом античит:
#: такие обращения античиты считают вмешательством и могут забанить
DEFAULT_FREE_THRESHOLD_MB = 1024
DEFAULT_STANDBY_THRESHOLD_MB = 4096
MIN_RSS_MB = 50

STATE_FILE = "game_boost.json"


class MEMORY_PRIORITY_INFORMATION(ctypes.Structure):
    _fields_ = [("MemoryPriority", wt.ULONG)]


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", wt.ULONG),
        ("ControlMask", wt.ULONG),
        ("StateMask", wt.ULONG),
    ]


k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CloseHandle.restype = wt.BOOL
k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
k32.SetProcessInformation.restype = wt.BOOL
k32.GetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
k32.GetProcessInformation.restype = wt.BOOL
k32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
k32.SetPriorityClass.restype = wt.BOOL
k32.GetPriorityClass.argtypes = [wt.HANDLE]
k32.GetPriorityClass.restype = wt.DWORD
psapi.EmptyWorkingSet.argtypes = [wt.HANDLE]
psapi.EmptyWorkingSet.restype = wt.BOOL
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD


# --- одна ручка на процесс --------------------------------------------------
def _open(pid: int, access: int):
    return k32.OpenProcess(access, False, int(pid))


def _with_process(pid: int, access: int, action):
    """Открыть процесс, выполнить действие, гарантированно закрыть ручку."""
    handle = _open(pid, access)
    if not handle:
        return None
    try:
        return action(handle)
    finally:
        k32.CloseHandle(handle)


def set_memory_priority(pid: int, priority: int = MEMORY_PRIORITY_VERY_LOW) -> bool:
    """Приоритет страниц процесса: чем ниже, тем раньше ядро вытеснит их в подкачку."""
    info = MEMORY_PRIORITY_INFORMATION(int(priority))

    def action(handle):
        return k32.SetProcessInformation(
            handle, ProcessMemoryPriority, ctypes.byref(info), ctypes.sizeof(info)
        )

    return bool(_with_process(pid, PROCESS_SET_INFORMATION, action))


def get_memory_priority(pid: int) -> int | None:
    """Текущий приоритет страниц — для снимка перед изменением и диагностики."""
    info = MEMORY_PRIORITY_INFORMATION()

    def action(handle):
        return k32.GetProcessInformation(
            handle, ProcessMemoryPriority, ctypes.byref(info), ctypes.sizeof(info)
        )

    if not _with_process(pid, PROCESS_QUERY_LIMITED_INFORMATION, action):
        return None
    return int(info.MemoryPriority)


def set_eco_qos(pid: int, enabled: bool = True) -> bool:
    """EcoQoS: планировщик держит процесс на эффективных ядрах и без буста частот."""
    state = PROCESS_POWER_THROTTLING_STATE(
        PROCESS_POWER_THROTTLING_CURRENT_VERSION,
        PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
        PROCESS_POWER_THROTTLING_EXECUTION_SPEED if enabled else 0,
    )

    def action(handle):
        return k32.SetProcessInformation(
            handle, ProcessPowerThrottling, ctypes.byref(state), ctypes.sizeof(state)
        )

    return bool(_with_process(pid, PROCESS_SET_INFORMATION, action))


def get_eco_qos(pid: int) -> bool | None:
    state = PROCESS_POWER_THROTTLING_STATE(
        PROCESS_POWER_THROTTLING_CURRENT_VERSION, 0, 0
    )

    def action(handle):
        return k32.GetProcessInformation(
            handle, ProcessPowerThrottling, ctypes.byref(state), ctypes.sizeof(state)
        )

    if not _with_process(pid, PROCESS_QUERY_LIMITED_INFORMATION, action):
        return None
    return bool(state.StateMask & PROCESS_POWER_THROTTLING_EXECUTION_SPEED)


def set_cpu_priority(pid: int, priority_class: int = HIGH_PRIORITY_CLASS) -> bool:
    return bool(_with_process(pid, PROCESS_SET_INFORMATION,
                              lambda h: k32.SetPriorityClass(h, priority_class)))


def get_cpu_priority(pid: int) -> int | None:
    def action(handle):
        value = k32.GetPriorityClass(handle)
        return value if value else None

    result = _with_process(pid, PROCESS_QUERY_LIMITED_INFORMATION, action)
    return int(result) if result else None


def _process_created(pid: int) -> float | None:
    """Время создания процесса: по нему отличаем «тот же» от «тот же PID»."""
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return None


def _same_process(pid: int, created: float | None) -> bool:
    """Жив ли исходный процесс.

    Без этой проверки сохранённые приоритеты после перезапуска Windows могли
    достаться совсем другому процессу с тем же PID.
    """
    if created is None:
        # снимок старого формата: времени создания нет, проверять нечем —
        # ведём себя как раньше и просто пробуем вернуть приоритеты
        return True
    try:
        proc = psutil.Process(pid)
        return abs(float(proc.create_time()) - float(created)) < 0.5
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False


def foreground_pid() -> int:
    """PID процесса, чьё окно сейчас в фокусе (0 — нет такого)."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


# --- выбор целей ------------------------------------------------------------
def _name_of(proc) -> str:
    try:
        return (proc.info.get("name") or "").lower()
    except Exception:  # noqa: BLE001 — процесс мог умереть между опросом и чтением
        return ""


def protected(name: str) -> bool:
    """Системный процесс: не трогать никогда."""
    return name.lower() in SYSTEM_NAMES


def background(name: str) -> bool:
    return name.lower() in BACKGROUND_NAMES


def anticheat_present() -> str | None:
    """Имя найденного ядерного античита или None."""
    for proc in psutil.process_iter(["name"]):
        if _name_of(proc) in ANTICHEAT_NAMES:
            return _name_of(proc)
    return None


def collect_targets(min_rss_mb: int = MIN_RSS_MB, only_background: bool = True,
                    exclude_pids=()) -> list[dict]:
    """Процессы, которые имеет смысл сжать.

    ``only_background=True`` — только известные фоновые пожиратели (безопасный
    режим «умной очистки»). ``False`` — все пользовательские процессы крупнее
    порога, кроме системных, Gammadrilon и того, что сейчас в фокусе.
    """
    focus = foreground_pid()
    skip = set(exclude_pids) | {focus, os.getpid()}
    targets = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            name = _name_of(proc)
            if not name or protected(name) or proc.pid in skip:
                continue
            if only_background and not background(name):
                continue
            rss = getattr(proc.info.get("memory_info"), "rss", 0) or 0
            rss_mb = rss / (1024 * 1024)
            if rss_mb < min_rss_mb:
                continue
            targets.append({"pid": proc.pid, "name": name, "rss_mb": round(rss_mb, 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    targets.sort(key=lambda item: item["rss_mb"], reverse=True)
    return targets


def trim_working_sets(pids=None, min_rss_mb: int = MIN_RSS_MB,
                      only_background: bool = True) -> dict:
    """Сжать рабочие наборы и честно посчитать, сколько реально освободилось.

    ``psutil.available`` включает standby, поэтому после purge standby цифра
    почти не меняется. Здесь меряем до/после именно сжатия — это и есть
    настоящая прибавка свободной памяти.
    """
    targets = (collect_targets(min_rss_mb, only_background) if pids is None
               else [{"pid": int(pid), "name": "", "rss_mb": 0.0} for pid in pids])
    before = psutil.virtual_memory().available
    ok, message, done = gd_memory.empty_working_sets_native([t["pid"] for t in targets])
    time.sleep(0.5)
    after = psutil.virtual_memory().available
    freed_mb = round((after - before) / (1024 * 1024), 1)
    return {
        "ok": ok,
        "message": message,
        "count": done,
        "targets": len(targets),
        "freed_mb": freed_mb,
    }


def should_purge(free_mb: float, standby_mb: float,
                 free_threshold_mb: float = DEFAULT_FREE_THRESHOLD_MB,
                 standby_threshold_mb: float = DEFAULT_STANDBY_THRESHOLD_MB) -> bool:
    """Чистить standby только когда свободной памяти мало И кэш разросся.

    Иначе кнопка «оптимизировать» вымывает кэш игры и делает хуже — ровно та
    ошибка, из-за которой такие утилиты считают вредными.
    """
    return free_mb < free_threshold_mb and standby_mb > standby_threshold_mb


# --- игровой режим ----------------------------------------------------------
def _state_path() -> Path:
    return gd_paths.data_dir() / STATE_FILE


class GameBoost:
    """Включение игрового буста с восстановлением в любом исходе.

    Снимок исходных значений пишется на диск: если приложение упадёт, при
    следующем старте ``restore_from_disk()`` вернёт приоритеты фоновых
    процессов и снимет таймер.
    """

    def __init__(self) -> None:
        self._active = False
        self._game_pid: int | None = None
        self._snapshot: dict[str, dict] = {}
        self._timer_held = False
        self._note = ""

    # состояние
    def active(self) -> bool:
        return self._active

    def status(self) -> dict:
        return {
            "active": self._active,
            "game_pid": self._game_pid,
            "affected": len(self._snapshot),
            "timer": self._timer_held,
            "note": self._note,
        }

    def _save(self) -> None:
        payload = {
            "active": self._active,
            "game_pid": self._game_pid,
            "timer": self._timer_held,
            "affected": self._snapshot,
        }
        path = _state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def _clear_file(self) -> None:
        try:
            _state_path().unlink(missing_ok=True)
        except OSError:
            pass

    # запуск
    def start(self, game_pid: int | None = None) -> tuple[bool, str]:
        if self._active:
            return False, i18n.t("Игровой буст уже включён.")

        cheat = anticheat_present()
        notes = []

        targets = collect_targets(only_background=True)
        changed = 0
        for target in targets:
            pid = target["pid"]
            entry = {
                "name": target["name"],
                "memory_priority": get_memory_priority(pid),
                "eco_qos": get_eco_qos(pid),
                "cpu_priority": get_cpu_priority(pid),
            }
            touched = False
            if set_memory_priority(pid, MEMORY_PRIORITY_VERY_LOW):
                touched = True
            if set_eco_qos(pid, True):
                touched = True
            if touched:
                entry["created"] = _process_created(pid)
                self._snapshot[str(pid)] = entry
                changed += 1

        # сжимаем один раз: дальше Windows сама держит их страницы вытесненными
        if targets:
            gd_memory.empty_working_sets_native([t["pid"] for t in targets])

        if game_pid:
            if cheat:
                notes.append(i18n.t("рядом античит {v0} — процесс игры не трогаю", v0=cheat))
            else:
                if set_cpu_priority(int(game_pid), HIGH_PRIORITY_CLASS):
                    notes.append(i18n.t("игра получила высокий приоритет CPU"))
                else:
                    notes.append(i18n.t("приоритет игры не изменился (нет прав или процесс закрыт)"))
                set_memory_priority(int(game_pid), MEMORY_PRIORITY_NORMAL)
                set_eco_qos(int(game_pid), False)
            self._game_pid = int(game_pid)

        try:
            import gd_extra

            result = gd_extra.set_timer_resolution(True)
            ok, actual = result if isinstance(result, tuple) else (bool(result), 5000)
            if ok:
                self._timer_held = True
                notes.append(i18n.t("таймер {v0} мс", v0=actual / 10000.0))
        except Exception:  # noqa: BLE001 — таймер не обязателен для буста
            pass

        self._active = True
        self._note = "; ".join(notes)
        self._save()
        if not changed and not notes:
            return False, i18n.t("Не нашлось фоновых процессов для сжатия.")
        return True, i18n.t("Буст включён: фоновых процессов изменено {v0}. {v1}",
                            v0=changed, v1=self._note).strip()

    # остановка
    def stop(self) -> tuple[bool, str]:
        if not self._active and not self._snapshot:
            return True, i18n.t("Игровой буст и так выключен.")

        restored = 0
        pending = {}
        for pid_text, entry in self._snapshot.items():
            pid = int(pid_text)
            if not _same_process(pid, entry.get("created")):
                # процесса уже нет или PID достался другому — трогать его нельзя
                restored += 1
                continue
            priority = entry.get("memory_priority")
            eco = entry.get("eco_qos")
            cpu = entry.get("cpu_priority")
            failed = False
            if priority is not None and not set_memory_priority(pid, priority):
                failed = True
            if eco is not None and not set_eco_qos(pid, bool(eco)):
                failed = True
            if cpu and not set_cpu_priority(pid, int(cpu)):
                failed = True
            if failed:
                pending[pid_text] = entry
            if pid_text not in pending:
                restored += 1

        if self._timer_held:
            try:
                import gd_extra

                result = gd_extra.set_timer_resolution(False)
                timer_ok = result[0] if isinstance(result, tuple) else bool(result)
            except Exception:  # noqa: BLE001
                timer_ok = False
            self._timer_held = not timer_ok

        self._snapshot = pending
        self._active = bool(pending or self._timer_held)
        self._game_pid = None
        self._note = ""
        if self._active:
            self._save()
            return False, i18n.t("Не удалось полностью восстановить игровой буст.")
        self._clear_file()
        return True, i18n.t("Буст выключен, восстановлено процессов: {v0}.", v0=restored)

    def restore_from_disk(self) -> bool:
        """После падения приложения вернуть приоритеты, сохранённые в снимке."""
        path = _state_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        snapshot = data.get("affected") or {}
        if not isinstance(snapshot, dict) or not snapshot:
            self._clear_file()
            return False
        self._snapshot = snapshot
        self._timer_held = bool(data.get("timer"))
        ok, _ = self.stop()
        return ok


class HelperBoost:
    """UI-клиент буста: изменение чужих процессов выполняет elevated-помощник."""

    def __init__(self):
        import gd_helper

        self.client = gd_helper.HelperClient()
        self._active = False

    def active(self):
        return self._active

    def _call(self, command):
        response = self.client.call({"cmd": command}, timeout=120)
        state = response.get("data") or {}
        self._active = bool(state.get("active", self._active))
        return bool(response.get("ok")), response.get("message") or response.get("error") or ""

    def start(self):
        return self._call("boost_start")

    def stop(self):
        if not self._active:
            return True, i18n.t("Игровой буст и так выключен.")
        return self._call("boost_stop")
