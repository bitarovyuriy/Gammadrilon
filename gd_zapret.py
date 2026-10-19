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
import gd_paths
import gd_proc
import gd_state

HERE = os.path.dirname(os.path.abspath(__file__))

_DATA_DIR = gd_paths.log_dir()
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


#: диапазоны GameFilter по умолчанию — те же, что в service.bat
GAME_FILTER_DEFAULT_TCP = "1024-65535"
GAME_FILTER_DEFAULT_UDP = "1024-65535"
#: проверка формата пользовательского диапазона портов: «1024-1934,1936-65535»
_PORT_RANGE_RE = re.compile(r"^\d{1,5}(?:-\d{1,5})?(?:,\d{1,5}(?:-\d{1,5})?)*$")


def valid_port_range(text: str) -> str | None:
    """Нормализовать диапазон портов или вернуть None (как service.bat)."""
    text = "".join(str(text or "").split())
    if not text or not _PORT_RANGE_RE.match(text):
        return None
    for part in text.split(","):
        pieces = [int(p) for p in part.split("-")]
        if any(p < 1 or p > 65535 for p in pieces):
            return None
        if len(pieces) == 2 and pieces[0] > pieces[1]:
            return None
    return text


def game_filter_state(root: str | None) -> dict:
    """Разбор ``utils\\game_filter.enabled`` в формате service.bat 1.10.3.

    Возвращает ``{'mode': off|all|tcp|udp, 'tcp': '…', 'udp': '…'}``.
    Старый формат (одна строка ``all`` / ``tcp`` / ``udp``) тоже понимается.
    """
    state = {"mode": "off", "tcp": GAME_FILTER_DEFAULT_TCP,
             "udp": GAME_FILTER_DEFAULT_UDP}
    if not root:
        return state
    f = Path(root).joinpath(*GAME_FILTER_FILE)
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return state
    tcp, udp = None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            if line.lower() in GAME_FILTER_MODES:
                state["mode"] = line.lower()
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        key = key.lower()
        if key == "mode" and value.lower() in GAME_FILTER_MODES:
            state["mode"] = value.lower()
        elif key == "all":
            state["mode"] = "all"
            tcp = udp = value or None
        elif key == "tcp":
            if value:
                tcp = value
            else:
                state["mode"] = "tcp"
        elif key == "udp":
            if value:
                udp = value
            else:
                state["mode"] = "udp"
    if tcp:
        state["tcp"] = valid_port_range(tcp) or GAME_FILTER_DEFAULT_TCP
    if udp:
        state["udp"] = valid_port_range(udp) or GAME_FILTER_DEFAULT_UDP
    return state


def game_filter(root: str | None) -> str:
    """Режим GameFilter: off / all / tcp / udp (файл utils\\game_filter.enabled)."""
    return game_filter_state(root)["mode"]


def set_game_filter(root: str | None, tcp: bool, udp: bool) -> tuple[bool, str]:
    """Совместимая обёртка: записать GameFilter по двум галочкам."""
    mode = "all" if (tcp and udp) else ("tcp" if tcp else ("udp" if udp else "off"))
    return set_game_filter_mode(root, mode)


def set_game_filter_mode(root: str | None, mode: str, tcp_range: str = "",
                         udp_range: str = "") -> tuple[bool, str]:
    """Записать GameFilter так, как это делает `service.bat :game_switch` 1.10.3.

    Поддерживает диапазоны портов: ``tcp=1024-1934,1936-65535``. Батники
    читают этот файл при каждом запуске (`load_game_filter`), поэтому смена
    тумблера при работающем winws требует повторного освоения — иначе тумблер
    соврёт о состоянии (ТЗ 8.3).
    """
    if not root:
        return False, i18n.t("папка zapret не выбрана")
    mode = (mode or "off").lower()
    if mode not in GAME_FILTER_MODES:
        return False, i18n.t("неизвестный режим GameFilter")
    tcp = valid_port_range(tcp_range) or GAME_FILTER_DEFAULT_TCP
    udp = valid_port_range(udp_range) or GAME_FILTER_DEFAULT_UDP
    if tcp_range and tcp != "".join(str(tcp_range).split()):
        return False, i18n.t("неверный диапазон TCP-портов")
    if udp_range and udp != "".join(str(udp_range).split()):
        return False, i18n.t("неверный диапазон UDP-портов")
    f = Path(root).joinpath(*GAME_FILTER_FILE)
    try:
        if mode == "off":
            if f.exists():
                f.unlink()
            return True, mode
        f.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"mode={mode}", f"tcp={tcp}", f"udp={udp}"]
        f.write_text("\n".join(lines) + "\n", encoding="ascii")
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
        if not m:
            continue
        state = _SC_STATES.get(int(m.group(1)))
        if state:
            return state
        # Строка «Тип : 10 WIN32_OWN_PROCESS» тоже подходит под шаблон:
        # неизвестный код — не повод считать, что службы нет, читаем дальше.
    return None


# --------------------------------------------------------------------------
# служба zapret: установка/запуск/остановка без консоли service.bat
# --------------------------------------------------------------------------


SERVICE_NAME = "zapret"
SERVICE_DISPLAY = "zapret"
SERVICE_DESC = "Zapret DPI bypass software"
SERVICE_REG_VALUE = "zapret-discord-youtube"
_WINDIVERT = ("WinDivert", "WinDivert14")


def _run_sc(args: list[str], timeout: float = 20.0):
    """Запустить sc.exe и вернуть (код, текст) без исключений."""
    try:
        proc = gd_proc.run(["sc", *args], timeout=timeout, text=True)
    except (OSError, gd_proc.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _service_wait(target: str, timeout: float = 15.0) -> bool:
    """Дождаться состояния службы (RUNNING/STOPPED) после команды sc."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if service_state(SERVICE_NAME) == target:
            return True
        time.sleep(0.4)
    return service_state(SERVICE_NAME) == target


def service_registry_strategy() -> str | None:
    """Имя стратегии, которым служба была установлена (пишет service.bat)."""
    return _service_reg_value(SERVICE_REG_VALUE)


def _service_reg_value(name: str) -> str | None:
    """Прочитать строковое значение из ключа службы (ImagePath, Start, …)."""
    key = rf"HKLM\System\CurrentControlSet\Services\{SERVICE_NAME}"
    try:
        proc = gd_proc.run(["reg", "query", key, "/v", name], timeout=8, text=True)
    except (OSError, gd_proc.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].lower() == name.lower():
            return " ".join(parts[2:]).strip()
    return None


def _service_config() -> dict:
    """Снимок текущей службы — чтобы вернуть её при неудачной переустановке."""
    return {
        "exists": service_state(SERVICE_NAME) is not None,
        "image": _service_reg_value("ImagePath"),
        "display": _service_reg_value("DisplayName"),
        "start": _service_reg_value("Start"),
        "strategy": service_registry_strategy(),
    }


def _restore_service(config: dict) -> bool:
    """Восстановить службу из снимка (после неудачного `sc create`)."""
    image = config.get("image")
    if not image:
        return False
    _run_sc(["delete", SERVICE_NAME], timeout=15)
    code, _ = _run_sc(["create", SERVICE_NAME, "binPath=", image,
                       "DisplayName=", config.get("display") or SERVICE_DISPLAY,
                       "start=", "auto"], timeout=25)
    if code != 0 or service_state(SERVICE_NAME) is None:
        return False
    if config.get("strategy"):
        key = rf"HKLM\System\CurrentControlSet\Services\{SERVICE_NAME}"
        try:
            gd_proc.run(["reg", "add", key, "/v", SERVICE_REG_VALUE, "/t", "REG_SZ",
                         "/d", str(config["strategy"]), "/f"], timeout=10, text=True)
        except (OSError, gd_proc.TimeoutExpired):
            pass
    service_start()
    return True


def service_status() -> dict:
    """Состояние службы: installed/state/strategy (без прав администратора)."""
    state = service_state(SERVICE_NAME)
    strategy = service_registry_strategy()
    installed = state is not None or strategy is not None
    return {"installed": installed, "state": state, "strategy": strategy}


def service_binpath(root: str, argv: list) -> str:
    """Командная строка процесса для ``sc create binPath=``."""
    exe = Path(root) / "bin" / "winws.exe"
    return subprocess.list2cmdline([str(exe), *[str(a) for a in (argv or [])]])


def _valid_argv(argv: list) -> bool:
    """Аргументы winws не должны ломать ``sc create``: ни переводов строк, ни кавычек.

    Кавычки внутри argv практически не встречаются (psutil отдаёт готовые
    элементы), но ``sc`` сохраняет их как часть ImagePath — с экранированием,
    которое winws потом не поймёт. Поэтому лучше отказаться, чем испортить службу.
    """
    return bool(argv) and all(
        isinstance(a, str) and "\n" not in a and "\r" not in a and '"' not in a
        for a in argv)


def service_stop(timeout: float = 20.0) -> tuple[bool, str]:
    """Остановить службу zapret (нужны права администратора)."""
    state = service_state(SERVICE_NAME)
    if state is None:
        return True, i18n.t("служба не установлена")
    if state != "STOPPED":
        _run_sc(["stop", SERVICE_NAME], timeout=timeout)
        _service_wait("STOPPED", timeout=timeout)
    return (service_state(SERVICE_NAME) == "STOPPED",
            i18n.t("служба остановлена") if service_state(SERVICE_NAME) == "STOPPED"
            else i18n.t("служба не остановилась"))


def service_start(timeout: float = 25.0) -> tuple[bool, str]:
    """Запустить службу zapret и дождаться RUNNING."""
    if service_state(SERVICE_NAME) is None:
        return False, i18n.t("служба не установлена")
    if service_state(SERVICE_NAME) != "RUNNING":
        _run_sc(["start", SERVICE_NAME], timeout=timeout)
        _service_wait("RUNNING", timeout=timeout)
    ok = service_state(SERVICE_NAME) == "RUNNING"
    return ok, i18n.t("служба запущена") if ok else i18n.t("служба не запустилась")


def service_restart(timeout: float = 30.0) -> tuple[bool, str]:
    """Перезапустить службу (после смены стратегии или настроек)."""
    if service_state(SERVICE_NAME) is None:
        return False, i18n.t("служба не установлена")
    _run_sc(["stop", SERVICE_NAME], timeout=timeout)
    _service_wait("STOPPED", timeout=timeout)
    return service_start(timeout=timeout)


def service_install(root: str, strategy: str, argv: list,
                    start: bool = True) -> tuple[bool, str]:
    """Поставить/переустановить службу zapret с готовым argv (нужен админ).

    Полностью повторяет то, что делает ``service.bat``: binPath из winws.exe
    и argv, DisplayName «zapret», автозапуск, реестровая метка стратегии.
    """
    if not root:
        return False, i18n.t("папка zapret не выбрана")
    if not (Path(root) / "bin" / "winws.exe").is_file():
        return False, i18n.t("в bin\\ нет winws.exe")
    if not _valid_argv(argv):
        return False, i18n.t("нет готового argv — сначала «Распознать»")

    previous = _service_config()
    stop_ok, stop_msg = service_stop()
    if not stop_ok:
        return False, stop_msg
    _run_sc(["delete", SERVICE_NAME], timeout=15)
    if service_state(SERVICE_NAME) is not None:
        # Windows не сразу отпускает службу после delete
        for _ in range(20):
            if service_state(SERVICE_NAME) is None:
                break
            time.sleep(0.4)

    binpath = service_binpath(root, argv)
    code, out = _run_sc(["create", SERVICE_NAME, "binPath=", binpath,
                         "DisplayName=", SERVICE_DISPLAY, "start=", "auto"], timeout=25)
    if code != 0 or service_state(SERVICE_NAME) is None:
        # службу уже могли удалить — возвращаем прежнюю, чтобы не оставить
        # пользователя без обхода после неудачной переустановки
        restored = _restore_service(previous) if previous.get("exists") else False
        hint = i18n.t("sc create: {v0}", v0=out or code)
        if previous.get("exists"):
            hint += " " + (i18n.t("прежняя служба восстановлена") if restored
                           else i18n.t("прежнюю службу вернуть не удалось"))
        return False, hint
    _run_sc(["description", SERVICE_NAME, SERVICE_DESC], timeout=10)
    key = rf"HKLM\System\CurrentControlSet\Services\{SERVICE_NAME}"
    try:
        gd_proc.run(["reg", "add", key, "/v", SERVICE_REG_VALUE, "/t", "REG_SZ",
                     "/d", strategy or "?", "/f"], timeout=10, text=True)
    except (OSError, gd_proc.TimeoutExpired):
        pass
    if start:
        return service_start()
    return True, i18n.t("служба установлена")


def service_remove(timeout: float = 25.0) -> tuple[bool, str]:
    """Удалить службу zapret и связанные WinDivert (нужен админ).

    Как и официальный ``service.bat``, чистит драйверные службы WinDivert:
    без этого после удаления остаётся занятый фильтр и следующий запуск
    обхода упирается в «WinDivert уже загружен».
    """
    stop_ok, stop_msg = service_stop(timeout=timeout)
    if not stop_ok:
        return False, stop_msg
    code, out = _run_sc(["delete", SERVICE_NAME], timeout=15)
    for name in _WINDIVERT:
        _run_sc(["stop", name], timeout=15)
        _run_sc(["delete", name], timeout=15)
    if service_state(SERVICE_NAME) is not None:
        return False, i18n.t("sc delete: {v0}", v0=out or code)
    return True, i18n.t("служба удалена")


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


#: предел журнала winws.exe: файл живёт месяцами, а winws пишет много
LOG_MAX_BYTES = 1024 * 1024


def trim_log(path=None) -> None:
    """Оставить последние ~512 КБ журнала winws.exe.

    Журнал открывается на дозапись при каждом запуске обхода и между сессиями
    не чистится (обещание «хвост виден» сохраняем: обрезаем только историю).
    Вызывается перед открытием файла на запись — в фоне, не в GUI-потоке.
    """
    p = Path(path) if path else LOG_FILE
    try:
        if p.stat().st_size <= LOG_MAX_BYTES:
            return
        with p.open("rb") as fh:
            fh.seek(-LOG_MAX_BYTES // 2, os.SEEK_END)
            tail = fh.read()
        p.write_bytes(tail)
    except OSError:
        pass


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
        "game_filter_state": game_filter_state(root),
        "ipset": ipset_info(root),
        "ours": None,
        "our_stale": False,
        "foreign": [],
        "service_zapret": None,
        "service_windivert": None,
        "service_strategy": None,
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
    snap["service_strategy"] = service_registry_strategy()
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


def strategy_bat_name(strategy: str) -> str | None:
    """Имя батника стратегии: UI отдаёт ``general (ALT2)``, файл — с ``.bat``."""
    name = os.path.basename(strategy or "")
    if name != strategy or not name.lower().startswith("general"):
        return None
    if not name.lower().endswith(".bat"):
        name += ".bat"
    return name


def recon(root: str, strategy: str, timeout: float = 20.0) -> tuple[bool, dict | str]:
    """Освоение стратегии (ТЗ 8.2): запустить батник и поймать argv winws.exe.

    Батник запускается в режиме разведки, ловится появившийся winws.exe вместе
    с командной строкой, процесс убивается строго по PID (не по имени — иначе
    можно снести чужой экземпляр, см. ТЗ 8.4).
    """
    import psutil

    name = strategy_bat_name(strategy)
    if not name:
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
        if log_file:
            trim_log(log_file)
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

