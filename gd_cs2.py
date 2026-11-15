# -*- coding: utf-8 -*-
"""Gammadrilon v3 — «Оптимизация CS2»: один рычаг под игру.

Модель без Qt: что именно меняется в системе, как прочитать это обратно и как
вернуть исходное. Все правки — пользовательские (HKCU) плюс активная схема
питания, поэтому прав администратора не нужно. Перед первым изменением каждой
записи сохраняется её прежнее значение в ``data_dir()/cs2_backup.json``, и
«выключить» возвращает именно его, а не «настройки Windows по умолчанию».

Мягкий профиль включает штатный режим игры Windows. Дополнительные параметры
совместимости, захвата, GPU и питания применяются по выбору пользователя.
Сетевая диагностика опирается на внутриигровую телеметрию Valve.
"""
from __future__ import annotations

import ctypes
import re
from pathlib import Path

try:
    import winreg
except ImportError:  # не Windows: модуль обязан импортироваться и для тестов
    winreg = None

import gd_i18n as i18n
import gd_paths
import gd_proc
import gd_state

#: схема питания «Высокая производительность» (есть в любой Windows)
HIGH_PERF_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
#: схема «Максимальная производительность» (создаётся вручную, может не быть)
ULTIMATE_GUID = "e9a42b02-d5df-448d-aa00-03f14749eb61"
_POWER_BACKUP = "power|active"

GAME_DIR = "Counter-Strike Global Offensive"
EXE_REL = Path("game/bin/win64/cs2.exe")
SOFT_DEFAULT = ("game_mode",)

REG_SZ = 1
REG_DWORD = 4

#: тумблеры рычага в порядке показа. needs_exe — без пути к игре такой тумблер
#: не к чему привязать (запись в реестре именуется полным путём к exe)
TWEAKS: tuple[dict, ...] = (
    {
        "id": "power_plan",
        "title": "Схема питания «Высокая производительность»",
        "why": "Меняет схему питания всего компьютера и может увеличить энергопотребление. "
               "Сравните время кадра на обеих схемах в одинаковых условиях.",
        "needs_exe": False,
    },
    {
        "id": "fs_opt",
        "title": "Отключить оптимизации полноэкранного режима",
        "why": "Параметр совместимости для проверки воспроизводимых проблем с полноэкранным режимом. "
               "Сравните работу CS2 в обоих вариантах после перезапуска игры.",
        "needs_exe": True,
    },
    {
        "id": "gpu_pref",
        "title": "GPU для CS2: высокая производительность",
        "why": "Полезно на компьютере с несколькими GPU, если Windows запускает CS2 "
               "на энергосберегающем адаптере. Перезапустите CS2 после изменения.",
        "needs_exe": True,
    },
    {
        "id": "game_mode",
        "title": "Режим игры Windows включён",
        "why": "Включает штатный игровой режим Windows для всех игр. "
               "Результат зависит от фоновой нагрузки и компьютера.",
        "needs_exe": False,
    },
    {
        "id": "game_dvr",
        "title": "Game DVR (запись Xbox) выключен",
        "why": "Отключает захват Xbox Game Bar для всех игр. "
               "Попробуйте при рывках, если во время игры используется запись.",
        "needs_exe": False,
    },
)

_STEAM_HINTS = (
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
    r"D:\Steam",
    r"E:\Steam",
    r"F:\Steam",
)

#: подписи значений cs2_video.txt (только те, в которых нет сомнений)
_MSAA = {"0": "выключено", "2": "CMAA2", "4": "2x MSAA", "8": "4x MSAA", "16": "8x MSAA"}
_QUALITY = {"0": "низко", "1": "средне", "2": "высоко", "3": "максимум"}
_REFLEX = {"0": "выключен", "1": "включён", "2": "включён + boost"}

LAUNCH_ADVICE = (
    "Строку параметров запуска можно оставить пустой. Для сравнения FPS запускайте CS2 с одними "
    "и теми же параметрами. Флаги вроде -threads, -high и -novid не гарантируют прирост FPS."
)


# --------------------------------------------------------------------------
# реестр
# --------------------------------------------------------------------------


class WinRegistry:
    """Реестр Windows по именам ветвей: чтение, запись, удаление значения."""

    HIVES = {"HKCU": 0x80000001, "HKLM": 0x80000002}

    def read(self, hive: str, key: str, name: str):
        """None означает отсутствие значения. Ошибки доступа передаются вызывающему."""
        if winreg is None:
            raise OSError(i18n.t("реестр недоступен"))
        try:
            with winreg.OpenKey(self.HIVES[hive], key) as handle:
                value, _kind = winreg.QueryValueEx(handle, name)
                return value
        except FileNotFoundError:
            return None

    def write(self, hive: str, key: str, name: str, value, kind: int = REG_DWORD) -> None:
        if winreg is None:
            raise OSError(i18n.t("реестр недоступен"))
        with winreg.CreateKeyEx(self.HIVES[hive], key, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.SetValueEx(handle, name, 0, kind, value)

    def drop(self, hive: str, key: str, name: str) -> None:
        """Удалить значение; отсутствие — не ошибка (возврат к «как было»)."""
        if winreg is None:
            return
        try:
            with winreg.OpenKey(self.HIVES[hive], key, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            return


class PowerScheme:
    """Активная схема питания. Чтение — без прав, запись — powercfg."""

    def active(self) -> str:
        try:
            import gd_power

            return gd_power.get_active_scheme()
        except Exception:  # noqa: BLE001 — схему не прочитать, покажем пустую строку
            return ""

    def set(self, guid: str) -> bool:
        if not guid:
            return False
        try:
            result = gd_proc.run(["powercfg", "/setactive", guid], timeout=15)
        except (OSError, gd_proc.TimeoutExpired):
            return False
        return result.returncode == 0

    def available(self, guid: str) -> bool:
        import gd_power

        return guid.upper() in {scheme.upper() for scheme in gd_power.list_schemes()}


def _status_value(registry, hive: str, key: str, name: str):
    try:
        return registry.read(hive, key, name)
    except OSError:
        return None


# --------------------------------------------------------------------------
# поиск игры
# --------------------------------------------------------------------------


def _library_folders(steam: Path) -> list[Path]:
    """Библиотеки Steam из libraryfolders.vdf (в файле пути с двойным слэшем)."""
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [Path(raw.replace("\\\\", "\\")) for raw in re.findall(r'"path"\s+"([^"]+)"', text)]


def steam_root() -> Path | None:
    """Корень Steam: реестр точнее догадок, догадки — на случай чистого реестра."""
    path = _status_value(WinRegistry(), "HKCU", r"Software\Valve\Steam", "SteamPath")
    if path and Path(str(path)).is_dir():
        return Path(str(path))
    for hint in _STEAM_HINTS:
        if Path(hint).is_dir():
            return Path(hint)
    return None


def find_cs2() -> str:
    """Путь к cs2.exe по библиотекам Steam. Пустая строка — игра не найдена."""
    roots: list[Path] = []
    root = steam_root()
    if root is not None:
        roots.append(root)
    for hint in _STEAM_HINTS:
        path = Path(hint)
        if path.is_dir() and path not in roots:
            roots.append(path)
    seen: set[Path] = set()
    for steam in roots:
        for library in (steam, *_library_folders(steam)):
            exe = library / "steamapps" / "common" / GAME_DIR / EXE_REL
            if exe in seen:
                continue
            seen.add(exe)
            if exe.is_file():
                return str(exe)
    return ""


# --------------------------------------------------------------------------
# состояние и переключение
# --------------------------------------------------------------------------


def _entries(tweak_id: str, exe: str) -> list[tuple[str, str, str, object, int]]:
    """Записи реестра включённого тумблера: (ветвь, ключ, имя, значение, тип)."""
    if tweak_id == "gpu_pref":
        return [("HKCU", r"Software\Microsoft\DirectX\UserGpuPreferences", exe,
                 "GpuPreference=2;", REG_SZ)]
    if tweak_id == "fs_opt":
        return [("HKCU", r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers", exe,
                 "~ DISABLEDXMAXIMIZEDWINDOWEDMODE", REG_SZ)]
    if tweak_id == "game_mode":
        return [("HKCU", r"Software\Microsoft\GameBar", "AllowAutoGameMode", 1, REG_DWORD),
                ("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1, REG_DWORD)]
    if tweak_id == "game_dvr":
        return [("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", 0, REG_DWORD),
                ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                 "AppCaptureEnabled", 0, REG_DWORD)]
    return []


def _applied(tweak_id: str, exe: str, registry, active: str) -> bool:
    """Включён ли тумблер прямо сейчас — по фактическим значениям в системе."""
    if tweak_id == "power_plan":
        return active.upper() in (HIGH_PERF_GUID.upper(), ULTIMATE_GUID.upper())
    if tweak_id == "gpu_pref":
        value = registry.read("HKCU", r"Software\Microsoft\DirectX\UserGpuPreferences", exe)
        return isinstance(value, str) and "gpupreference=2" in value.lower().split(";")
    if tweak_id == "fs_opt":
        value = registry.read(
            "HKCU", r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers", exe)
        return isinstance(value, str) and "DISABLEDXMAXIMIZEDWINDOWEDMODE" in value.upper().split()
    if tweak_id == "game_mode":
        return (registry.read("HKCU", r"Software\Microsoft\GameBar", "AllowAutoGameMode") == 1
                and registry.read("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled") == 1)
    if tweak_id == "game_dvr":
        return (registry.read("HKCU", r"System\GameConfigStore", "GameDVR_Enabled") == 0
                and registry.read("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                                  "AppCaptureEnabled") == 0)
    return False


def snapshot(exe: str = "", *, registry=None, power=None) -> dict:
    """Что включено сейчас: по тумблеру и суммарно. exe ищется, если не передан."""
    registry = registry or WinRegistry()
    power = power or PowerScheme()
    exe = exe or find_cs2()
    active = power.active()
    items = {}
    for spec in TWEAKS:
        tweak_id = spec["id"]
        try:
            items[tweak_id] = _applied(tweak_id, exe, registry, active)
        except OSError:
            items[tweak_id] = False
    backup = _load_backup(backup_path())
    saved_scheme = _saved_scheme(backup)
    missing_power = None
    try:
        if saved_scheme and saved_scheme.upper() != active.upper() and not power.available(saved_scheme):
            missing_power = saved_scheme
    except OSError:
        pass
    return {
        "exe": exe,
        "found": bool(exe),
        "items": items,
        "power": active,
        "on": bool(items) and all(items.values()),
        "can_revert": bool(backup),
        "managed_items": _managed_items(backup),
        "missing_power": missing_power,
    }


def backup_path() -> Path:
    return Path(gd_paths.data_dir()) / "cs2_backup.json"


def _load_backup(path: Path) -> dict:
    data = gd_state.read_json(path)
    if not isinstance(data, dict):
        return {}
    items = data.get("items")
    return items if isinstance(items, dict) else {}


def _registry_target(hive: str, key: str, name: str) -> tuple[str, object] | None:
    """Пункт и значение, записанные рычагом, в том числе для старых снимков."""
    for spec in TWEAKS:
        for target_hive, target_key, target_name, value, _ in _entries(spec["id"], name):
            if (target_hive, target_key, target_name) == (hive, key, name):
                return spec["id"], value
    return None


def _managed_items(backup: dict) -> list[str]:
    managed = {"power_plan"} if _POWER_BACKUP in backup else set()
    for mark in backup:
        parts = mark.split("|", 2)
        if len(parts) == 3 and (target := _registry_target(*parts)):
            managed.add(target[0])
    return selected_ids(managed)


def managed_counts(snap: dict) -> tuple[int, int]:
    """Число действующих пунктов, значения которых сохранил рычаг."""
    items = snap.get("items") or {}
    managed = snap.get("managed_items") or []
    return sum(bool(items.get(item)) for item in managed), len(managed)


def _save_backup(path: Path, items: dict) -> bool:
    return gd_state.write_json_atomic(path, {"version": 1, "items": items})


def _saved_scheme(items: dict) -> str:
    spec = items.get(_POWER_BACKUP)
    return str(spec.get("value") or "") if isinstance(spec, dict) else ""


def _save_remaining(path: Path, items: dict) -> bool:
    if items:
        return _save_backup(path, items)
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def missing_scheme_note(guid: str) -> str:
    return i18n.t("Windows больше не видит прежнюю схему питания {v0}, поэтому её нельзя восстановить.",
                  v0=guid)


def _restore_scheme(guid: str, power) -> tuple[bool, str]:
    if not guid or guid.upper() == power.active().upper():
        return False, ""
    if not power.available(guid):
        return False, missing_scheme_note(guid)
    active = power.active().upper()
    if not active:
        return False, i18n.t("Не удалось прочитать активную схему питания.")
    # Если схема уже сменилась, её выбрал другой профиль или пользователь.
    if active != HIGH_PERF_GUID.upper():
        return False, ""
    if not power.set(guid):
        return False, i18n.t("powercfg не вернул прежнюю схему")
    return True, ""


def selected_ids(items) -> list[str]:
    """Из настроек — только известные id и только в порядке каталога."""
    chosen = {item for item in (items or ()) if isinstance(item, str)}
    return [spec["id"] for spec in TWEAKS if spec["id"] in chosen]


def configured_ids(items) -> list[str]:
    """Новый профиль по умолчанию, сохранённый список остаётся выбором пользователя."""
    return selected_ids(items) if isinstance(items, list) else list(SOFT_DEFAULT)


def apply(items, exe: str = "", *, registry=None, power=None, path: Path | str | None = None) -> dict:
    """Включить выбранные тумблеры, запомнив прежние значения.

    Ничего не бросает наружу: отказ одного тумблера не мешает остальным, а
    список отказов уходит в отчёт. Бэкап только дополняется — повторное
    включение не затирает исходное значение первой правки.
    """
    registry = registry or WinRegistry()
    power = power or PowerScheme()
    path = Path(path or backup_path())
    chosen = set(selected_ids(items))
    report: dict = {"ok": False, "applied": [], "already_active": [], "errors": []}
    if not chosen:
        report["errors"].append(i18n.t("ни один пункт не выбран"))
        return report
    backup = _load_backup(path)
    planned = []
    # Полный снимок сохраняется до первого изменения, включая powercfg.
    for spec in TWEAKS:
        if spec["id"] not in chosen:
            continue
        if spec["needs_exe"] and not exe:
            report["errors"].append(
                i18n.t("cs2.exe не найдена — «{v0}» пропущен", v0=spec["title"]))
            continue
        try:
            active = ""
            if spec["id"] == "power_plan":
                active = power.active()
                if not active:
                    report["errors"].append(i18n.t("Не удалось прочитать активную схему питания."))
                    continue
            if _applied(spec["id"], exe, registry, active):
                report["applied"].append(spec["id"])
                report["already_active"].append(spec["id"])
                continue
            if spec["id"] == "power_plan":
                backup.setdefault(_POWER_BACKUP, {"exist": True, "value": active, "kind": "sz"})
            entries = []
            originals = {}
            for hive, key, name, value, kind in _entries(spec["id"], exe):
                old = registry.read(hive, key, name)
                if old == value:
                    continue
                entries.append((hive, key, name, value, kind))
                mark = f"{hive}|{key}|{name}"
                if mark not in backup:
                    originals[mark] = {"exist": old is not None, "value": old,
                                       "kind": "dword" if isinstance(old, int) else "sz"}
            backup.update(originals)
            planned.append((spec, entries))
        except OSError as exc:
            report["errors"].append(f"{spec['title']}: {exc}")
    if planned and not _save_backup(path, backup):
        report["errors"].append(i18n.t("не удалось сохранить резервную копию"))
        return report
    for spec, entries in planned:
        try:
            if spec["id"] == "power_plan":
                if not power.set(HIGH_PERF_GUID):
                    report["errors"].append(i18n.t("«{v0}»: powercfg отказал", v0=spec["title"]))
                    continue
            else:
                for hive, key, name, value, kind in entries:
                    registry.write(hive, key, name, value, kind)
            report["applied"].append(spec["id"])
        except OSError as exc:
            report["errors"].append(f"{spec['title']}: {exc}")
    report["ok"] = bool(report["applied"]) and not report["errors"]
    return report


def revert(*, registry=None, power=None, path: Path | str | None = None) -> dict:
    """Вернуть всё, что рычаг менял, к значениям из бэкапа.

    Записи, которых до включения не было, удаляются: «не было значения» — это
    тоже состояние системы, и возвращать его надо именно так.
    """
    registry = registry or WinRegistry()
    power = power or PowerScheme()
    path = Path(path or backup_path())
    items = _load_backup(path)
    report: dict = {"ok": False, "restored": 0, "errors": []}
    if not items:
        report["errors"].append(i18n.t("нечего возвращать: рычаг не включался"))
        return report
    remaining = {}
    for mark, spec in items.items():
        spec = spec if isinstance(spec, dict) else {}
        try:
            if mark == _POWER_BACKUP:
                restored, error = _restore_scheme(_saved_scheme(items), power)
                report["restored"] += int(restored)
                if error:
                    remaining[mark] = spec
                    report["errors"].append(error)
                continue
            hive, key, name = mark.split("|", 2)
            target = _registry_target(hive, key, name)
            if target is None:
                raise ValueError(i18n.t("Неизвестная настройка CS2 в резервной копии."))
            if registry.read(hive, key, name) != target[1]:
                continue
            if spec.get("exist"):
                kind = REG_DWORD if str(spec.get("kind")) == "dword" else REG_SZ
                registry.write(hive, key, name, spec.get("value"), kind)
            else:
                registry.drop(hive, key, name)
            report["restored"] += 1
        except (OSError, ValueError) as exc:
            remaining[mark] = spec
            report["errors"].append(f"{mark}: {exc}")
    if remaining != items and not _save_remaining(path, remaining):
        report["errors"].append(i18n.t("Не удалось обновить резервную копию CS2."))
    report["ok"] = not report["errors"]
    return report


def forget_missing_scheme(guid: str, *, power=None, path: Path | str | None = None) -> dict:
    """Убрать только подтверждённую пользователем запись об отсутствующей схеме."""
    power = power or PowerScheme()
    path = Path(path or backup_path())
    items = _load_backup(path)
    saved_scheme = _saved_scheme(items)
    if not saved_scheme or guid.upper() != saved_scheme.upper():
        return {"ok": False, "errors": [i18n.t("Резервная копия CS2 изменилась. Проверьте состояние ещё раз.")]}
    try:
        if saved_scheme.upper() == power.active().upper() or power.available(saved_scheme):
            return {"ok": False, "errors": [i18n.t("Прежняя схема питания снова доступна. Повторите откат.")]}
    except OSError as exc:
        return {"ok": False, "errors": [str(exc)]}
    del items[_POWER_BACKUP]
    if not _save_remaining(path, items):
        return {"ok": False, "errors": [i18n.t("Не удалось обновить резервную копию CS2.")]}
    return {"ok": True, "errors": []}


# --------------------------------------------------------------------------
# процесс игры
# --------------------------------------------------------------------------

PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
HIGH_PRIORITY_CLASS = 0x0080

_PRIORITY_NAMES = {
    0x40: "Idle",
    0x4000: "Below normal",
    0x20: "Normal",
    0x8000: "Above normal",
    0x80: "High",
    0x100: "Realtime",
}


def cs2_pids() -> list[int]:
    """Живые процессы cs2.exe (пусто — игра не запущена)."""
    try:
        import psutil
    except Exception:  # noqa: BLE001 — без psutil просто не знаем про процесс
        return []
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if str(proc.info.get("name") or "").lower() == "cs2.exe":
                pids.append(int(proc.info["pid"]))
        except Exception:  # noqa: BLE001 — процесс мог умереть между итерациями
            continue
    return pids


def priority_of(pid: int) -> str:
    """Класс приоритета процесса словами; пустая строка — не прочитать."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        return _PRIORITY_NAMES.get(int(kernel32.GetPriorityClass(handle)), "")
    finally:
        kernel32.CloseHandle(handle)


def raise_priority(pids: list[int] | None = None) -> dict:
    """Поднять приоритет cs2.exe до High. Права администратора не нужны."""
    pids = cs2_pids() if pids is None else list(pids)
    report: dict = {"ok": False, "raised": 0, "errors": []}
    if not pids:
        report["errors"].append(i18n.t("CS2 не запущена"))
        return report
    kernel32 = ctypes.windll.kernel32
    for pid in pids:
        handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
        if not handle:
            report["errors"].append(i18n.t("pid {v0}: нет доступа", v0=pid))
            continue
        try:
            if kernel32.SetPriorityClass(handle, HIGH_PRIORITY_CLASS):
                report["raised"] += 1
            else:
                report["errors"].append(i18n.t("pid {v0}: Windows отказала", v0=pid))
        finally:
            kernel32.CloseHandle(handle)
    report["ok"] = report["raised"] > 0
    return report


# --------------------------------------------------------------------------
# диагностика (только чтение)
# --------------------------------------------------------------------------


def diagnostics(*, registry=None) -> list[dict]:
    """Состояние того, что меняется только перезагрузкой или руками."""
    registry = registry or WinRegistry()
    rows: list[dict] = []

    hags = _status_value(registry, "HKLM", r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode")
    rows.append({
        "title": i18n.t("Аппаратное планирование GPU (HAGS)"),
        "value": i18n.t("включено") if hags == 2 else i18n.t("выключено"),
        "note": i18n.t("В CS2 разницы не даёт; менять только если есть чужие замеры на твоей карте."),
        "level": "info",
    })

    vbs = _status_value(registry, "HKLM", r"SYSTEM\CurrentControlSet\Control\DeviceGuard",
                        "EnableVirtualizationBasedSecurity")
    rows.append({
        "title": i18n.t("Виртуализация (VBS)"),
        "value": i18n.t("включена") if vbs == 1 else i18n.t("выключена"),
        "note": i18n.t("С включённой VBS игры теряют несколько процентов кадров: "
                       "это защита, и отключать её — решение пользователя, не приложения."),
        "level": "warn" if vbs == 1 else "ok",
    })

    hvci = _status_value(
        registry, "HKLM",
        r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
        "Enabled")
    rows.append({
        "title": i18n.t("Целостность памяти"),
        "value": i18n.t("включена") if hvci == 1 else i18n.t("выключена"),
        "note": i18n.t("Часть той же защиты, что и VBS; влияет на кадры примерно так же."),
        "level": "warn" if hvci == 1 else "ok",
    })

    timer = _status_value(registry, "HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel",
                          "GlobalTimerResolutionRequests")
    rows.append({
        "title": i18n.t("Общий таймер 0,5 мс"),
        "value": i18n.t("да") if timer == 1 else i18n.t("нет (по умолчанию 1 мс)"),
        "note": i18n.t("Правка HKLM: нужен администратор и перезагрузка, поэтому руками, а не рычагом."),
        "level": "info",
    })
    return rows


def video_file() -> Path | None:
    """Самый свежий cs2_video.txt из userdata Steam (последний аккаунт)."""
    root = steam_root()
    if root is None:
        return None
    best: tuple[float, Path] | None = None
    for path in (root / "userdata").glob("*/730/local/cfg/cs2_video.txt"):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if best is None or stamp > best[0]:
            best = (stamp, path)
    return best[1] if best else None


def video_values(path: Path | str | None = None) -> dict:
    """Пары «ключ → значение» из cs2_video.txt."""
    target = Path(path) if path else video_file()
    if target is None or not target.is_file():
        return {}
    text = target.read_text(encoding="utf-8", errors="replace")
    return {m.group(1): m.group(2) for m in re.finditer(r'"([^"]+)"\s+"([^"]*)"', text)}


def video_notes(path: Path | str | None = None) -> list[dict]:
    """Настройки игры, которые реально стоят кадров, с честной подписью."""
    values = video_values(path)
    if not values:
        return []

    def value(key: str, default: str = "") -> str:
        return str(values.get(key, default))

    rows: list[dict] = []
    width, height = value("setting.defaultres"), value("setting.defaultresheight")
    if width and height:
        mode = i18n.t("полный экран") if value("setting.fullscreen") == "1" else i18n.t("окно")
        rows.append({"title": i18n.t("Разрешение"), "value": f"{width}×{height}, {mode}",
                     "note": "", "level": "ok" if value("setting.fullscreen") == "1" else "warn"})

    msaa = value("setting.msaa_samples")
    rows.append({
        "title": i18n.t("Сглаживание"),
        "value": _MSAA.get(msaa, msaa or "—"),
        "note": i18n.t("CMAA2 почти бесплатно; MSAA стоит кадров пропорционально кратности."),
        "level": "warn" if msaa in ("4", "8", "16") else "ok",
    })

    shadows = value("setting.videocfg_shadow_quality")
    rows.append({
        "title": i18n.t("Качество теней"),
        "value": _QUALITY.get(shadows, shadows or "—"),
        "note": i18n.t("Тени — самый дорогой пункт после сглаживания."),
        "level": "warn" if shadows not in ("", "0") else "ok",
    })

    dynamic = value("setting.videocfg_dynamic_shadows")
    rows.append({
        "title": i18n.t("Динамические тени"),
        "value": i18n.t("включены") if dynamic == "1" else i18n.t("выключены"),
        "note": i18n.t("Двигающиеся тени от игроков: заметная просадка на замесах."),
        "level": "warn" if dynamic == "1" else "ok",
    })

    particles = value("setting.videocfg_particle_detail")
    rows.append({
        "title": i18n.t("Частицы"),
        "value": _QUALITY.get(particles, particles or "—"),
        "note": "",
        "level": "warn" if particles not in ("", "0") else "ok",
    })

    ao = value("setting.videocfg_ao_detail")
    rows.append({
        "title": i18n.t("Ambient occlusion"),
        "value": _QUALITY.get(ao, ao or "—"),
        "note": "",
        "level": "warn" if ao not in ("", "0") else "ok",
    })

    reflex = value("setting.r_low_latency")
    rows.append({
        "title": i18n.t("NVIDIA Reflex"),
        "value": _REFLEX.get(reflex, reflex or "—"),
        "note": i18n.t("Низкая задержка ввода; на кадры почти не влияет."),
        "level": "warn" if reflex in ("", "0") else "ok",
    })

    vsync = value("setting.mat_vsync")
    rows.append({
        "title": i18n.t("Вертикальная синхронизация"),
        "value": i18n.t("включена") if vsync == "1" else i18n.t("выключена"),
        "note": i18n.t("С ней кадры режутся до герцовки монитора."),
        "level": "warn" if vsync == "1" else "ok",
    })
    return rows


def launch_advice() -> str:
    return i18n.t(LAUNCH_ADVICE)
