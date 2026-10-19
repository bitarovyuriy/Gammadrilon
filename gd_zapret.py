# -*- coding: utf-8 -*-
"""Ядро интеграции с zapret-discord-youtube (ТЗ 8, уровень A «освоение»).

Gammadrilon — контроллер и UI, папка zapret остаётся внешней и обновляется
автором. Батники не парсим: внутри `service.bat` есть досклейка аргументов и
разворачивание @-файлов — повторять этот парсер у себя значит ломаться на
каждом апдейте. Вместо этого «осваиваем» стратегию: запускаем выбранный
`general*.bat`, ловим появившийся `winws.exe` вместе с его argv, кэшируем.

Дальше переключение — прямой запуск `winws.exe` с освоенным argv:
без cmd.exe, с контролем статуса и stdout, точным убийством по PID.

Модуль без Qt и без прав администратора для чтения; привилегированные
операции (recon/start/stop) вызываются из elevated-хелпера
(`gd_gpu_service.py`), сюда же их можно звать из тестов напрямую.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
from pathlib import Path

import gd_i18n as i18n
import gd_proc
import gd_state

HERE = os.path.dirname(os.path.abspath(__file__))

_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "Gammadrilon"
#: состояние процесса, запущенного Gammadrilon (pid, стратегия, argv)
STATE_FILE = _DATA_DIR / "zapret_state.json"
#: кэш argv по стратегиям, с подписью версии папки
CACHE_FILE = _DATA_DIR / "zapret_argv.json"
#: stdout/stderr winws.exe — хелпер пишет сюда при прямом запуске
LOG_FILE = _DATA_DIR / "zapret_winws.log"

GAME_FILTER_MODES = ("off", "all", "tcp", "udp")
#: файл-маркер GameFilter (пишет/читает сам zapret, см. service.bat :game_switch)
GAME_FILTER_FILE = ("utils", "game_filter.enabled")
#: заглушка «ipset выключен» — тот же адрес, что ставит service.bat
IPSET_PLACEHOLDER = "203.0.113.113/32"

#: состояния службы sc по номеру (sc печатает имя состояния по-английски)
_SC_STATES = {1: "STOPPED", 2: "START_PENDING", 3: "STOP_PENDING",
              4: "RUNNING", 5: "CONTINUE_PENDING", 6: "PAUSE_PENDING", 7: "PAUSED"}


# --------------------------------------------------------------------------
# путь и стратегии
# --------------------------------------------------------------------------


def guess_path() -> str | None:
    """Подсказка папки zapret для первого запуска (слот ТЗ и типовые места).

    Это только подсказка: путь не применяется сам, пользователь подтверждает
    его в диалоге выбора папки. Если версий несколько — берём старшую по имени
    (лексикографически: 1.10.2 > 1.10.0).
    """
    import glob

    candidates: list[str] = []
    patterns = (
        r"D:\za\zapret-discord-youtube*",
        os.path.join(HERE, "zapret*"),
        os.path.join(os.path.expanduser("~"), "Downloads", "zapret-discord-youtube*"),
    )
    for pat in patterns:
        candidates.extend(glob.glob(pat))
    valid = sorted(c for c in candidates if (Path(c) / "bin" / "winws.exe").is_file())
    return valid[-1] if valid else None


def validate(root: str | None) -> dict:
    """Проверка папки: ошибки (не работать) и предупреждения (работать можно).

    Путь проверяется в момент выбора (ТЗ 8.4): zapret требует ASCII-путь без
    спецсимволов — иначе winws.exe падает уже после запуска, без внятной
    причины. Кириллица — предупреждение, а не запрет: отказ показываем только
    на структурных проблемах.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not root:
        errors.append(i18n.t("папка zapret не выбрана"))
        return {"errors": errors, "warnings": warnings}
    p = Path(root)
    if not p.is_dir():
        errors.append(i18n.t("папка не существует"))
        return {"errors": errors, "warnings": warnings}
    if not (p / "bin" / "winws.exe").is_file():
        errors.append(i18n.t("в bin\\ нет winws.exe — это не папка zapret-discord-youtube"))
    if not (p / "service.bat").is_file():
        errors.append(i18n.t("в корне нет service.bat — неполный комплект"))
    if not scan_strategies(root):
        errors.append(i18n.t("не найдено ни одного general*.bat — нечего осваивать"))
    if not str(root).isascii():
        warnings.append(
            i18n.t("путь содержит не-ASCII символы (кириллица и т.п.) — zapret "
                   "требует ASCII-путь, иначе winws.exe может не запуститься")
        )
    return {"errors": errors, "warnings": warnings}


def group_of(name: str) -> str:
    """Группа стратегии для списка: BASE / ALT / FAKE TLS AUTO / SIMPLE FAKE / EXP."""
    upper = name.upper()
    if upper.startswith("ALT"):
        return "ALT"
    if upper.startswith("FAKE TLS AUTO"):
        return "FAKE TLS AUTO"
    if upper.startswith("SIMPLE FAKE"):
        return "SIMPLE FAKE"
    if upper.startswith("EXP"):
        return "EXP"
    return "BASE"


def scan_strategies(root: str | None) -> list[dict]:
    """Стратегии — файлы `general*.bat` в корне папки (ТЗ 8.2, п.1).

    Имя файла без расширения и есть ID стратегии; в списке показываем
    человекочитаемое имя (`ALT11`) рядом с реальным именем файла.
    """
    if not root:
        return []
    rootp = Path(root)
    if not rootp.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(rootp.glob("general*.bat")):
        stem = f.stem                      # "general (ALT11)" / "general"
        tail = stem[len("general"):].strip().strip("()")
        name = tail or "BASE"
        out.append({"id": stem, "name": name, "file": f.name, "group": group_of(name)})

    order = {"BASE": 0, "ALT": 1, "FAKE TLS AUTO": 2, "SIMPLE FAKE": 3, "EXP": 4}

    def sort_key(s: dict):
        digits = "".join(ch for ch in s["name"] if ch.isdigit())
        return (order.get(s["group"], 9), int(digits) if digits else 0, s["name"])

    return sorted(out, key=sort_key)


# --------------------------------------------------------------------------
# тумблеры: GameFilter и IPSet — в родных файлах zapret, не «флаги в реестре»
# --------------------------------------------------------------------------


def game_filter(root: str | None) -> str:
    """Режим GameFilter: off / all / tcp / udp (файл utils\\game_filter.enabled)."""
    if not root:
        return "off"
    f = Path(root).joinpath(*GAME_FILTER_FILE)
    try:
        mode = f.read_text(encoding="utf-8", errors="replace").strip().lower()
    except OSError:
        return "off"
    return mode if mode in GAME_FILTER_MODES else "off"


def set_game_filter(root: str | None, tcp: bool, udp: bool) -> tuple[bool, str]:
    """Записать GameFilter так, как это делает `service.bat :game_switch`.

    Батники читают этот файл при каждом запуске (`load_game_filter`), поэтому
    смена тумблера при работающем winws требует повторного освоения — иначе
    тумблер соврёт о состоянии (ТЗ 8.3).
    """
    if not root:
        return False, i18n.t("папка zapret не выбрана")
    mode = "all" if (tcp and udp) else ("tcp" if tcp else ("udp" if udp else "off"))
    f = Path(root).joinpath(*GAME_FILTER_FILE)
    try:
        if mode == "off":
            if f.exists():
                f.unlink()
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(mode + "\n", encoding="ascii")
    except OSError as exc:
        return False, i18n.t("не удалось изменить game_filter.enabled: {v0}", v0=exc)
    return True, mode


def _ipset_file(root: str) -> Path:
    return Path(root) / "lists" / "ipset-all.txt"


def ipset_info(root: str | None) -> dict:
    """Состояние ipset-списка, как его видит `service.bat :ipset_switch_status`.

    any — файл пуст (фильтруются любые IP), none — стоит заглушка (фильтр
    выключен), loaded — рабочий список, missing — файла нет.
    """
    info = {"mode": "missing", "lines": 0, "size": 0, "mtime": None, "backup": False}
    if not root:
        return info
    f = _ipset_file(root)
    backup = Path(str(f) + ".backup")
    info["backup"] = backup.is_file()
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
        st = f.stat()
    except OSError:
        return info
    info["size"] = st.st_size
    info["mtime"] = st.st_mtime
    lines = text.splitlines()
    info["lines"] = len(lines)
    if not lines or (len(lines) == 1 and not lines[0].strip()):
        info["mode"] = "any"
    elif IPSET_PLACEHOLDER in text:
        info["mode"] = "none"
    else:
        info["mode"] = "loaded"
    return info


def set_ipset(root: str | None, enabled: bool) -> tuple[bool, str]:
    """Включить/выключить ipset-фильтр — та же логика, что в `service.bat`.

    loaded → none: текущий список уходит в `.backup`, на его месте заглушка;
    none → loaded: список возвращается из `.backup`;
    any (пустой файл) → loaded: восстанавливаем из `.backup`, если он есть.
    """
    if not root:
        return False, i18n.t("папка zapret не выбрана")
    f = _ipset_file(root)
    backup = Path(str(f) + ".backup")
    mode = ipset_info(root)["mode"]
    try:
        if enabled:
            if mode == "loaded":
                return True, i18n.t("уже загружен")
            if mode in ("none", "any"):
                if not backup.is_file():
                    return False, i18n.t(
                        "резервной копии списка нет — сначала обновите "
                        "списки через service.bat (пункт «Update IPSet List»)")
                if f.exists():
                    f.unlink()
                backup.rename(f)
                return True, i18n.t("список возвращён из резервной копии")
            return False, i18n.t("файл ipset-all.txt отсутствует")
        # выключение
        if mode == "none":
            return True, i18n.t("уже выключен")
        if mode == "missing":
            return False, i18n.t("файл ipset-all.txt отсутствует")
        if mode == "loaded" and not backup.exists():
            f.rename(backup)
        f.write_text(IPSET_PLACEHOLDER + "\n", encoding="ascii")
        return True, i18n.t("список отключён (заглушка, копия сохранена)")
    except OSError as exc:
        return False, i18n.t("не удалось изменить ipset-all.txt: {v0}", v0=exc)


# --------------------------------------------------------------------------
# процессы и службы
# --------------------------------------------------------------------------


def processes() -> list[dict]:
    """Все winws.exe в системе: pid, командная строка, время старта."""
    import psutil

    out: list[dict] = []
    for proc in psutil.process_iter(["name", "pid", "create_time"]):
        if (proc.info.get("name") or "").lower() != "winws.exe":
            continue
        try:
            cmdline = proc.cmdline()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            cmdline = []
        out.append({"pid": proc.info["pid"], "cmdline": cmdline,
                    "started": proc.info.get("create_time")})
    return out


def other_bypasses() -> list[dict]:
    """Посторонние обходчики DPI — конфликт по драйверу WinDivert (ТЗ 8.4)."""
    import psutil

    names = {"goodbyedpi.exe", "goodbye-dpi.exe"}
    out: list[dict] = []
    for proc in psutil.process_iter(["name", "pid"]):
        if (proc.info.get("name") or "").lower() in names:
            out.append({"pid": proc.info["pid"], "name": proc.info["name"]})
    return out


def pid_alive(pid, expect: str = "winws.exe") -> bool:
    """Живой ли процесс с ожидаемым именем (чужой PID не считаем своим)."""
    import psutil

    try:
        proc = psutil.Process(int(pid))
        return (proc.name() or "").lower() == expect.lower()
    except (psutil.Error, ValueError, TypeError):
        return False


def service_state(name: str) -> str | None:
    """Состояние службы через `sc query`; None — службы нет.

    Метка «STATE» локализована в русской Windows, а значение (RUNNING,
    STOPPED, …) — нет; поэтому парсим номер состояния из строки.
    """
    try:
        proc = gd_proc.run(["sc", "query", name], timeout=8, text=True)
    except (OSError, gd_proc.TimeoutExpired):
        return None
    text = (proc.stdout or "") + (proc.stderr or "")
    for line in text.splitlines():
        m = re.search(r":\s*(\d+)\s+[A-Z_]+", line)
        if m:
            return _SC_STATES.get(int(m.group(1)))
    return None


# --------------------------------------------------------------------------
# состояние «нашего» процесса и кэш argv
# --------------------------------------------------------------------------


def our_state() -> dict | None:
    """Процесс, запущенный Gammadrilon: {path, strategy, pid, argv, started_at}."""
    data = gd_state.read_json(STATE_FILE)
    return data if isinstance(data, dict) else None


def save_our_state(path: str, strategy: str, pid: int, argv: list,
                   external: bool = False) -> bool:
    """Запомнить запущенный нами winws — атомарно.

    False — записать не удалось: после перезапуска приложение не будет знать,
    какой процесс запускало, и остановить его из окна не сможет. Вызывающий
    должен сказать об этом честно.
    """
    data = {"path": path, "strategy": strategy, "pid": int(pid), "argv": list(argv),
            "started_at": time.time(), "external": bool(external)}
    return gd_state.write_json_atomic(STATE_FILE, data)


def clear_our_state() -> None:
    try:
        STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _as_pid(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


#: PID, о пропаже которых уже сказали: иначе и таймер окна, и опрос вкладки
#: сообщали бы об одной и той же смерти по очереди
_vanished: set[int] = set()


def forget_previous_run() -> None:
    """Забыть состояние прошлого запуска приложения, не выдавая его за смерть.

    Если приложение закрыли (или оно упало) вместе с побеждённым winws.exe,
    файл состояния остаётся с мёртвым PID. Про такой процесс говорить «он
    завершился сам» — враньё: его мог убить наш же выход. Поэтому при старте
    окна такое состояние молча снимается, а живой наш процесс не трогается.
    """
    state = our_state()
    if not state:
        return
    pid = _as_pid(state.get("pid"))
    if pid is not None and pid_alive(pid):
        return                     # наш winws жив с прошлого запуска — оставляем
    if pid is not None:
        _vanished.add(pid)
    clear_our_state()


def take_vanished() -> dict | None:
    """Взять уведомление «наш winws исчез» — по одному разу на каждый запуск.

    Проверка дёшева (файл состояния + имя одного процесса), поэтому её может
    звать и таймер главного окна, и опрос вкладки: кто спросил первым — тот и
    получает состояние, остальные получают ``None``. Молчать нельзя: обход
    могли включить и свернуть окно, а смерть процесса — новость пользователя,
    а не деталь строки на закрытой вкладке.
    """
    state = our_state()
    if not state:
        return None
    pid = _as_pid(state.get("pid"))
    if pid is None:
        clear_our_state()
        return None
    if pid_alive(pid):
        _vanished.discard(pid)     # PID переиспользован живым процессом
        return None
    clear_our_state()
    if pid in _vanished:
        return None
    _vanished.add(pid)
    return state


def _signature(root: str) -> str:
    """Подпись версии папки: дата+размер winws.exe, service.bat и version.txt.

    Кэш argv инвалидируется при смене любого из них — после апдейта комплекта
    старые аргументы могут быть несовместимы (ТЗ 8.2, п.5).
    """
    parts = []
    for rel in ("bin/winws.exe", "service.bat", "version.txt"):
        try:
            st = (Path(root) / rel).stat()
            parts.append(f"{rel}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{rel}:нет")  # i18n: skip (ключ кэша, не UI)
    return "|".join(parts)


def _cache_load() -> dict:
    data = gd_state.read_json(CACHE_FILE, {})
    return data if isinstance(data, dict) else {}


def _cache_save(data: dict) -> None:
    # кэш argv восстанавливается освоением заново — молчаливый отказ допустим
    gd_state.write_json_atomic(CACHE_FILE, data)


def cache_get(root: str | None, strategy: str) -> list | None:
    """argv из кэша, если подпись папки совпадает; иначе None (нужно освоение)."""
    if not root:
        return None
    entry = _cache_load().get(os.path.normcase(str(root)), {}).get(strategy)
    if not isinstance(entry, dict) or entry.get("sig") != _signature(root):
        return None
    argv = entry.get("argv")
    return list(argv) if isinstance(argv, list) and argv else None


def cache_put(root: str, strategy: str, argv: list, source: str = "recon") -> None:
    data = _cache_load()
    key = os.path.normcase(str(root))
    data.setdefault(key, {})[strategy] = {
        "argv": list(argv), "sig": _signature(root),
        "ts": time.time(), "source": source,
    }
    _cache_save(data)


def cache_drop(root: str | None, strategy: str) -> None:
    """Инвалидировать кэш стратегии (например, запуск с этим argv упал)."""
    if not root:
        return
    data = _cache_load()
    key = os.path.normcase(str(root))
    if data.get(key, {}).pop(strategy, None) is not None:
        _cache_save(data)


def log_path() -> str:
    return str(LOG_FILE)


def log_tail(n: int = 200) -> list[str]:
    """Хвост stdout/stderr winws.exe: последние n строк без чтения всего файла."""
    try:
        size = LOG_FILE.stat().st_size
        with LOG_FILE.open("rb") as fh:
            fh.seek(max(0, size - 65536))
            raw = fh.read()
    except OSError:
        return []
    # seek может попасть в середину UTF-8 символа — errors="replace" это переживёт
    return raw.decode("utf-8", "replace").splitlines()[-int(n):]




# --------------------------------------------------------------------------
# снимок состояния для вкладки
# --------------------------------------------------------------------------


def snapshot(root: str | None) -> dict:
    """Полный срез состояния без привилегий: процессы, службы, тумблеры, списки."""
    snap: dict = {
        "path": root or "",
        "problems": validate(root),
        "strategies": scan_strategies(root),
        "game_filter": game_filter(root),
        "ipset": ipset_info(root),
        "ours": None,
        "our_stale": False,
        "foreign": [],
        "service_zapret": None,
        "service_windivert": None,
        "others": [],
    }
    ours = our_state()
    if ours and pid_alive(ours.get("pid")):
        started = None
        try:
            import psutil

            started = psutil.Process(int(ours["pid"])).create_time()
        except Exception:  # noqa: BLE001 — время старта не критично
            started = ours.get("started_at")
        snap["ours"] = dict(ours, started=started)
    elif ours:
        snap["our_stale"] = True
    our_pid = (snap["ours"] or {}).get("pid")
    snap["foreign"] = [p for p in processes() if p["pid"] != our_pid]
    snap["service_zapret"] = service_state("zapret")
    snap["service_windivert"] = service_state("WinDivert")
    snap["others"] = other_bypasses()
    return snap


# --------------------------------------------------------------------------
# машинный контур: освоение и запуск winws.exe
# --------------------------------------------------------------------------


def _winws_pids() -> set:
    import psutil

    out = set()
    for proc in psutil.process_iter(["name", "pid"]):
        if (proc.info.get("name") or "").lower() == "winws.exe":
            out.add(proc.info["pid"])
    return out


def _tail_text(raw: bytes, max_lines: int = 8) -> str:
    """Хвост вывода батника (chcp 65001 → UTF-8) для честного сообщения об ошибке."""
    text = raw.decode("utf-8", "replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " | ".join(lines[-max_lines:])


def recon(root: str, strategy: str, timeout: float = 20.0) -> tuple[bool, dict | str]:
    """Освоение стратегии (ТЗ 8.2): запустить батник и поймать argv winws.exe.

    Батник запускается в режиме разведки, ловится появившийся winws.exe вместе
    с командной строкой, процесс убивается строго по PID (не по имени — иначе
    можно снести чужой экземпляр, см. ТЗ 8.4).
    """
    import psutil

    name = os.path.basename(strategy or "")
    if name != strategy or not (name.lower().startswith("general")
                                and name.lower().endswith(".bat")):
        return False, i18n.t("недопустимое имя стратегии")
    rootp = Path(root or "")
    bat = rootp / name
    exe = rootp / "bin" / "winws.exe"
    if not bat.is_file():
        return False, i18n.t("нет файла стратегии: {v0}", v0=name)
    if not exe.is_file():
        return False, i18n.t("в bin\\ нет winws.exe")

    before = _winws_pids()
    try:
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(bat)],
            cwd=str(rootp),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **gd_proc.hidden_kwargs(),
        )
    except OSError as exc:
        return False, i18n.t("не удалось запустить батник: {v0}", v0=exc)

    argv, pid = None, None
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        time.sleep(0.3)
        for p in psutil.process_iter(["name", "pid"]):
            if (p.info.get("name") or "").lower() != "winws.exe":
                continue
            if p.info["pid"] in before:
                continue
            try:
                argv = p.cmdline()
            except psutil.Error:
                continue
            pid = p.info["pid"]
            break
        if pid:
            break

    if not pid:
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.terminate()
        raw = proc.stdout.read() if proc.stdout else b""
        tail = _tail_text(raw)
        msg = i18n.t("winws.exe не появился за {v0:.0f} с", v0=timeout)
        if tail:
            msg += i18n.t("; вывод батника: {v0}", v0=tail)
        return False, msg

    # убить разведочный процесс строго по конкретному PID (ТЗ 8.2, п.3)
    try:
        psutil.Process(pid).kill()
    except psutil.Error:
        pass
    for _ in range(20):                      # дождаться выгрузки драйвера
        if not psutil.pid_exists(pid):
            break
        time.sleep(0.1)
    if proc.poll() is None:
        proc.terminate()
    raw = proc.stdout.read() if proc.stdout else b""
    # argv[0] — путь к exe; кэшируем только аргументы (exe подставляем при запуске)
    args = [a for a in (argv or [])]
    if args and args[0].lower().endswith("winws.exe"):
        args = args[1:]
    if not args:
        return False, i18n.t("не удалось прочитать аргументы winws.exe")
    time.sleep(0.6)                          # WinDivert освобождается не мгновенно
    return True, {"argv": args, "pid": pid, "out": _tail_text(raw)}



# -- job-объект: winws умирает вместе с хелпером (страховка от краха UI) ----

_ZAPRET_JOB = None


class _BasicLimitInfo(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimitInfo(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInfo),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _ensure_job():
    """Job-объект с JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.

    Держится процессом, который запустил winws (хелпер): его смерть — любая,
    включая убийство — закрывает job и гасит winws. Это вторая линия отката
    после явной остановки при shutdown/watchdog (ТЗ 8, DoD).
    """
    global _ZAPRET_JOB
    if _ZAPRET_JOB:
        return _ZAPRET_JOB
    k32 = ctypes.windll.kernel32
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _ExtendedLimitInfo()
    info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return None
    _ZAPRET_JOB = job
    return job


def _assign_to_job(pid: int) -> None:
    job = _ensure_job()
    if not job:
        return
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x0100 | 0x0001, False, int(pid))  # SET_QUOTA|TERMINATE
    if handle:
        k32.AssignProcessToJobObject(job, handle)
        k32.CloseHandle(handle)


def start_winws(root: str, argv: list, log_file: str = "", note: str = "") -> tuple[bool, dict | str]:
    """Прямой запуск winws.exe с освоенным argv (ТЗ 8.2, п.4).

    CREATE_NO_WINDOW, рабочая директория bin\\, stdout/stderr — в свой лог.
    Чужой winws.exe не трогаем, но и не запускаемся поверх: WinDivert — одна
    инстанция на фильтр (ТЗ 8.4), второй экземпляр просто не заработает.
    """
    rootp = Path(root or "")
    exe = rootp / "bin" / "winws.exe"
    if not exe.is_file():
        return False, i18n.t("в bin\\ нет winws.exe")
    if not isinstance(argv, list) or not argv:
        return False, i18n.t("пустой argv — сначала освоение")
    running = processes()
    if running:
        pids = ", ".join(str(p["pid"]) for p in running[:3])
        return False, i18n.t("уже запущен winws.exe (PID {v0}) — остановите его или "
                             "возьмите под управление", v0=pids)
    try:
        fh = open(log_file, "a", encoding="utf-8", errors="replace") if log_file else None
    except OSError:
        fh = None
    try:
        if fh is not None:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            # i18n: skip (строка внутри журнала winws.exe — файл, не UI)
            fh.write(f"\n--- {stamp} запуск: {note or 'winws'} ---\n")
            fh.flush()
        proc = subprocess.Popen(
            [str(exe), *[str(a) for a in argv]],
            cwd=str(rootp / "bin"),
            stdin=subprocess.DEVNULL,
            stdout=fh if fh is not None else subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            **gd_proc.hidden_kwargs(),
        )
    except OSError as exc:
        return False, i18n.t("не удалось запустить winws.exe: {v0}", v0=exc)
    finally:
        if fh is not None:
            fh.close()
    _assign_to_job(proc.pid)
    time.sleep(1.2)                          # дать драйверу загрузиться
    rc = proc.poll()
    if rc is not None:
        tail = log_tail(4)
        hint = i18n.t(" Хвост журнала: {v0}", v0=' | '.join(tail[-2:])) if tail else ""
        return False, i18n.t("winws.exe завершился сразу (код {v0}).{v1}", v0=rc, v1=hint)
    return True, {"pid": proc.pid}


def stop_winws(pid) -> tuple[bool, str]:
    """Остановить winws.exe строго по PID и только если это действительно он."""
    import psutil

    try:
        proc = psutil.Process(int(pid))
        name = (proc.name() or "").lower()
    except psutil.NoSuchProcess:
        return True, i18n.t("процесс уже завершён")
    except (psutil.Error, ValueError, TypeError) as exc:
        return False, i18n.t("не удалось открыть процесс: {v0}", v0=exc)
    if name != "winws.exe":
        return False, i18n.t("PID {v0} — это {v1}, а не winws.exe; не трогаю",
                             v0=pid, v1=name or '?')
    try:
        proc.kill()
    except psutil.Error as exc:
        return False, i18n.t("не удалось остановить winws.exe: {v0}", v0=exc)
    for _ in range(20):
        if not psutil.pid_exists(int(pid)):
            break
        time.sleep(0.1)
    return True, i18n.t("winws.exe (PID {v0}) остановлен", v0=pid)

