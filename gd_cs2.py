# -*- coding: utf-8 -*-
"""Gammadrilon v3 — «Оптимизация CS2»: один рычаг под игру.

Модель без Qt: что именно меняется в системе, как прочитать это обратно и как
вернуть исходное. Все правки — пользовательские (HKCU) плюс активная схема
питания, поэтому прав администратора не нужно. Перед первым изменением каждой
записи сохраняется её прежнее значение в ``data_dir()/cs2_backup.json``, и
«выключить» возвращает именно его, а не «настройки Windows по умолчанию».

Что входит и почему:

* оптимизации полноэкранного режима для cs2.exe — DWM перестаёт перехватывать
  кадр: уходят рывки на alt-tab и лишний кадр задержки;
* приоритет GPU «высокая производительность» для cs2.exe — Windows не уводит
  игру на энергосберегающий путь (на десктопе безвредно, на гибридной графике
  обязательно);
* режим игры Windows — планировщик перестаёт резать игровые потоки фоновой
  работой;
* Game DVR выключен — фоновая запись Xbox забирает кадры и диск;
* схема питания «Высокая производительность» — без парковки ядер и
  энергосберегающих пауз; прежняя схема возвращается на «выключить».

Чего здесь сознательно нет: launch options (у Valve правило «лучшие launch
options — никаких», популярные флаги либо мертвы, либо ничего не дают),
правок HKLM (нужен администратор и перезагрузка), отключения VBS и HAGS — это
защита и драйвер, за них приложение не решает. Диагностика показывает их
состояние, но не трогает.
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

GAME_DIR = "Counter-Strike Global Offensive"
EXE_REL = Path("game/bin/win64/cs2.exe")

REG_SZ = 1
REG_DWORD = 4

#: тумблеры рычага в порядке показа. needs_exe — без пути к игре такой тумблер
#: не к чему привязать (запись в реестре именуется полным путём к exe)
TWEAKS: tuple[dict, ...] = (
    {
        "id": "power_plan",
        "title": "Схема питания «Высокая производительность»",
        "why": "Процессор не паркует ядра и не уходит в энергосберегающие паузы: "
               "на «Сбалансированной» Ryzen успевает сбросить частоту между тиками. "
               "Прежняя схема возвращается, когда рычаг выключен.",
        "needs_exe": False,
    },
    {
        "id": "fs_opt",
        "title": "Отключить оптимизации полноэкранного режима",
        "why": "Windows перестаёт растягивать кадр игры через DWM: меньше задержка "
               "и нет рывков при переключении окон. Самый заметный пункт из всех.",
        "needs_exe": True,
    },
    {
        "id": "gpu_pref",
        "title": "GPU для CS2: высокая производительность",
        "why": "Windows не выбирает для игры энергосберегающий адаптер и режим. "
               "На одном мониторе от дискретной карты это страховка, а не чудо.",
        "needs_exe": True,
    },
    {
        "id": "game_mode",
        "title": "Режим игры Windows включён",
        "why": "Планировщик держит игровые потоки в приоритете и не отдаёт ядра "
               "фоновым задачам во время матча.",
        "needs_exe": False,
    },
    {
        "id": "game_dvr",
        "title": "Game DVR (запись Xbox) выключен",
        "why": "Фоновая запись Xbox забирает кадры и диск. На стабильность кадра "
               "влияет сильнее, чем любой launch option.",
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
    "Почти всё, что советуют в гайдах, в CS2 не работает: -novid, -tickrate 128, "
    "-d3d9ex, -limitvsconst остались в Source 1. -threads гайды прямо не советуют — "
    "игра сама раздаёт потоки, а флаг добавляет нестабильности. -nojoy даёт копейки. "
    "+fps_max 0 снимает лишь встроенный предел в 400 кадров, и это удобнее выставить "
    "в самой игре. -high повторяет то, что уже делает рычаг. Вывод: оставь строку "
    "пустой — launch options тут ничего не решают."
)


# --------------------------------------------------------------------------
# реестр
# --------------------------------------------------------------------------


class WinRegistry:
    """Реестр Windows по именам ветвей: чтение, запись, удаление значения."""

    HIVES = {"HKCU": 0x80000001, "HKLM": 0x80000002}

    def read(self, hive: str, key: str, name: str):
        """Значение или None — отсутствие ключа и значения неотличимы, нам это и нужно."""
        if winreg is None:
            return None
        try:
            with winreg.OpenKey(self.HIVES[hive], key) as handle:
                value, _kind = winreg.QueryValueEx(handle, name)
                return value
        except OSError:
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
        except OSError:
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
    path = WinRegistry().read("HKCU", r"Software\Valve\Steam", "SteamPath")
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
        return isinstance(value, str) and "GpuPreference=2" in value
    if tweak_id == "fs_opt":
        value = registry.read(
            "HKCU", r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers", exe)
        return isinstance(value, str) and "DISABLEDXMAXIMIZEDWINDOWEDMODE" in str(value).upper()
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
    items = {spec["id"]: _applied(spec["id"], exe, registry, active) for spec in TWEAKS}
    return {
        "exe": exe,
        "found": bool(exe),
        "items": items,
        "power": active,
        "on": bool(items) and all(items.values()),
    }


def backup_path() -> Path:
    return Path(gd_paths.data_dir()) / "cs2_backup.json"


def _load_backup(path: Path) -> dict:
    data = gd_state.read_json(path)
    if not isinstance(data, dict):
        return {}
    items = data.get("items")
    return items if isinstance(items, dict) else {}


def _save_backup(path: Path, items: dict) -> bool:
    return gd_state.write_json_atomic(path, {"version": 1, "items": items})


def selected_ids(items) -> list[str]:
    """Из настроек — только известные id и только в порядке каталога."""
    chosen = set(items or ())
    return [spec["id"] for spec in TWEAKS if spec["id"] in chosen]


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
    report: dict = {"ok": False, "applied": [], "errors": []}
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
            entries = _entries(spec["id"], exe)
            if spec["id"] == "power_plan":
                backup.setdefault("power|active", {"exist": True, "value": power.active(), "kind": "sz"})
            for hive, key, name, value, kind in entries:
                mark = f"{hive}|{key}|{name}"
                if mark not in backup:
                    old = registry.read(hive, key, name)
                    backup[mark] = {"exist": old is not None, "value": old,
                                    "kind": "dword" if isinstance(old, int) else "sz"}
            planned.append((spec, entries))
        except OSError as exc:
            report["errors"].append(f"{spec['title']}: {exc}")
    if not _save_backup(path, backup):
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
    for mark, spec in items.items():
        spec = spec if isinstance(spec, dict) else {}
        try:
            if mark == "power|active":
                guid = str(spec.get("value") or "")
                if guid and not power.set(guid):
                    report["errors"].append(i18n.t("powercfg не вернул прежнюю схему"))
                continue
            hive, key, name = mark.split("|", 2)
            if spec.get("exist"):
                kind = REG_DWORD if str(spec.get("kind")) == "dword" else REG_SZ
                registry.write(hive, key, name, spec.get("value"), kind)
            else:
                registry.drop(hive, key, name)
            report["restored"] += 1
        except (OSError, ValueError) as exc:
            report["errors"].append(f"{mark}: {exc}")
    if not report["errors"]:
        try:
            path.unlink()
        except OSError:
            pass
    report["ok"] = not report["errors"]
    return report


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

    hags = registry.read("HKLM", r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode")
    rows.append({
        "title": i18n.t("Аппаратное планирование GPU (HAGS)"),
        "value": i18n.t("включено") if hags == 2 else i18n.t("выключено"),
        "note": i18n.t("В CS2 разницы не даёт; менять только если есть чужие замеры на твоей карте."),
        "level": "info",
    })

    vbs = registry.read("HKLM", r"SYSTEM\CurrentControlSet\Control\DeviceGuard",
                        "EnableVirtualizationBasedSecurity")
    rows.append({
        "title": i18n.t("Виртуализация (VBS)"),
        "value": i18n.t("включена") if vbs == 1 else i18n.t("выключена"),
        "note": i18n.t("С включённой VBS игры теряют несколько процентов кадров: "
                       "это защита, и отключать её — решение пользователя, не приложения."),
        "level": "warn" if vbs == 1 else "ok",
    })

    hvci = registry.read(
        "HKLM",
        r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
        "Enabled")
    rows.append({
        "title": i18n.t("Целостность памяти"),
        "value": i18n.t("включена") if hvci == 1 else i18n.t("выключена"),
        "note": i18n.t("Часть той же защиты, что и VBS; влияет на кадры примерно так же."),
        "level": "warn" if hvci == 1 else "ok",
    })

    timer = registry.read("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel",
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
