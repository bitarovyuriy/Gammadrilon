# -*- coding: utf-8 -*-
"""Диагностика окружения: что установлено, что мешает, что делать.

Зачем: жалобы «не работает» на чужом ПК приходят без деталей. Модуль собирает
факты (права, GPU, датчики, .NET, zapret, пути, прокси) и отдаёт обезличенный
отчёт, который можно прислать в переписку, не вынося имя пользователя и сети.

Правило: каждая проверка ловит свои ошибки и возвращает статус, а не бросает
исключение — диагностика обязана работать даже когда всё сломано.
"""
from __future__ import annotations

import ctypes
import os
import platform
import re
import socket
from pathlib import Path

import gd_i18n as i18n
import gd_paths

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"

MARKS = {OK: "✓", WARN: "!", FAIL: "×", SKIP: "—"}


def _check(key: str, title: str, status: str, detail: str, action: str = "") -> dict:
    return {"key": key, "title": title, "status": status, "detail": detail, "action": action}


# --- отдельные проверки -----------------------------------------------------
def check_windows() -> dict:
    version = platform.version()
    release = platform.release()
    build = ""
    match = re.search(r"\.(\d+)$", version)
    if match:
        build = match.group(1)
    title = i18n.t("Windows")
    try:
        major = int(version.split(".")[2])
    except (IndexError, ValueError):
        major = 0
    if major >= 22000:
        return _check("windows", title, OK, f"Windows 11, сборка {build or version}")
    if major >= 10240:
        return _check("windows", title, OK, f"{release}, сборка {build or version}")
    return _check("windows", title, WARN, i18n.t("старая версия Windows: {v0}", v0=version),
                  i18n.t("нужна Windows 10 или 11"))


def check_admin() -> dict:
    try:
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        admin = False
    if admin:
        return _check("admin", i18n.t("Права"), OK,
                      i18n.t("запущено от администратора"))
    return _check("admin", i18n.t("Права"), OK,
                  i18n.t("обычные права; привилегированные действия спросят UAC"))


def check_paths() -> list[dict]:
    """Режим, путь приложения, запись — самый частый источник «не работает»."""
    result = []
    app_dir = gd_paths.app_dir()
    portable = gd_paths.portable_enabled()
    mode = i18n.t("портативный") if portable else i18n.t("установленный")
    result.append(_check("mode", i18n.t("Режим"), OK,
                         i18n.t("{v0}: данные в {v1}", v0=mode, v1=gd_paths.data_dir())))

    ascii_ok = str(app_dir).isascii()
    result.append(_check(
        "path_ascii", i18n.t("Путь приложения"),
        OK if ascii_ok else WARN,
        str(app_dir) + ("" if ascii_ok else i18n.t(" — есть не-ASCII символы")),
        "" if ascii_ok else i18n.t("на таком пути Qt может не найти плагины; "
                                   "для сборки используйте ASCII-путь"),
    ))

    target = gd_paths.config_path().parent
    try:
        gd_paths.ensure(target)
        probe = Path(target) / ".diag-write"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        status, detail = OK, i18n.t("запись в {v0} есть", v0=target)
    except OSError as exc:
        status, detail = FAIL, i18n.t("нет записи в {v0}: {v1}", v0=target, v1=exc)
    result.append(_check("write", i18n.t("Запись данных"), status, detail))
    return result


def check_qt() -> dict:
    plugins = os.environ.get("QT_PLUGIN_PATH") or os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH") or ""
    if not plugins:
        return _check("qt", "Qt", SKIP, i18n.t("переменная плагинов не задана (обычный путь)"))
    qwindows = Path(plugins) / "platforms" / "qwindows.dll"
    if qwindows.is_file():
        return _check("qt", "Qt", OK, i18n.t("плагины найдены: {v0}", v0=plugins))
    return _check("qt", "Qt", WARN, i18n.t("в {v0} нет platforms\\qwindows.dll", v0=plugins))


def check_gpu() -> dict:
    try:
        import gd_gpu

        smi = gd_gpu._smi_path()  # noqa: SLF001 — путь нужен для отчёта
        adapter = gd_gpu.adapter_info() or {}
        name = adapter.get("name") or ""
        if smi:
            return _check("gpu", i18n.t("Видеокарта"), OK,
                          f"{name or 'NVIDIA'}; nvidia-smi: {smi}")
        if name:
            return _check("gpu", i18n.t("Видеокарта"), WARN,
                          i18n.t("{v0}; nvidia-smi не найден — управление лимитом недоступно",
                                 v0=name))
        return _check("gpu", i18n.t("Видеокарта"), FAIL,
                      i18n.t("адаптер не определён"))
    except Exception as exc:  # noqa: BLE001
        return _check("gpu", i18n.t("Видеокарта"), FAIL, f"{type(exc).__name__}: {exc}")


def check_sensors() -> dict:
    try:
        import gd_sensors

        if not gd_sensors.driver_present():
            return _check("sensors", i18n.t("Датчики"), WARN,
                          i18n.t("драйвер PawnIO не установлен — температуры платы недоступны"),
                          i18n.t("поставьте PawnIO и перезапустите приложение"))
        helper = gd_sensors.helper_path()
        if helper is None:
            return _check("sensors", i18n.t("Датчики"), FAIL,
                          i18n.t("PawnIO есть, но GammadrilonSensors.exe не найден"))
        fresh = gd_sensors.service_running()
        return _check("sensors", i18n.t("Датчики"), OK if fresh else WARN,
                      i18n.t("PawnIO есть; хелпер {v0}", v0=i18n.t("пишет замеры") if fresh
                             else i18n.t("ещё не запускался")))
    except Exception as exc:  # noqa: BLE001
        return _check("sensors", i18n.t("Датчики"), FAIL, f"{type(exc).__name__}: {exc}")


def check_dotnet() -> dict:
    """.NET Framework 4.x нужен C#-хелперу датчиков."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as key:
            release = int(winreg.QueryValueEx(key, "Release")[0])
    except (OSError, ValueError, TypeError):
        return _check("dotnet", ".NET Framework", FAIL,
                      i18n.t("не найден .NET Framework 4.x"),
                      i18n.t("поставьте .NET Framework 4.8"))
    if release >= 528040:
        return _check("dotnet", ".NET Framework", OK, f"4.8 ({release})")
    return _check("dotnet", ".NET Framework", WARN, i18n.t("версия {v0}", v0=release),
                  i18n.t("для датчиков нужен 4.8"))


def check_zapret() -> dict:
    path = ""
    try:
        import json

        data = json.loads(gd_paths.config_path().read_text(encoding="utf-8"))
        path = str((data.get("settings") or {}).get("zapret_path") or "")
    except Exception:  # noqa: BLE001
        path = ""
    if not path:
        return _check("zapret", "zapret", SKIP, i18n.t("папка не выбрана"))
    try:
        import gd_zapret

        problems = gd_zapret.validate(path)
        errors = problems.get("errors") or []
        warnings = problems.get("warnings") or []
        if errors:
            return _check("zapret", "zapret", FAIL, "; ".join(errors),
                          i18n.t("выберите полный комплект zapret-discord-youtube"))
        if warnings:
            return _check("zapret", "zapret", WARN, f"{path}: {'; '.join(warnings)}",
                          i18n.t("перенесите комплект в ASCII-путь"))
        return _check("zapret", "zapret", OK, path)
    except Exception as exc:  # noqa: BLE001
        return _check("zapret", "zapret", FAIL, f"{type(exc).__name__}: {exc}")


def check_proxy() -> dict:
    """Локальный прокси 10808: без него в РФ часто не работает автообновление."""
    for port in (10808,):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return _check("proxy", i18n.t("Прокси"), OK,
                              i18n.t("127.0.0.1:{v0} слушает", v0=port))
    return _check("proxy", i18n.t("Прокси"), SKIP,
                  i18n.t("локальный прокси 10808 не слушает — автообновление пойдёт напрямую"))


def check_uac() -> dict:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System") as key:
            value = int(winreg.QueryValueEx(key, "ConsentPromptBehaviorAdmin")[0])
    except (OSError, ValueError, TypeError):
        return _check("uac", "UAC", SKIP, i18n.t("настройка не читается"))
    if value == 0:
        return _check("uac", "UAC", OK, i18n.t("запросы прав без окна (политика 0)"))
    return _check("uac", "UAC", OK, i18n.t("обычные запросы прав (политика {v0})", v0=value))


def check_display() -> dict:
    """Яркость матрицы (DDC/CI) и частота обновления: что реально доступно.

    Первый вызов dxva2 занимает секунды — проверка идёт последней и без
    спешки; её же видит человек, когда ползунок подсветки не двигается.
    """
    try:
        import gd_ddc
        import gd_hz
    except Exception as exc:  # noqa: BLE001
        return _check("display", i18n.t("Монитор"), SKIP, f"{type(exc).__name__}: {exc}")

    parts: list[str] = []
    ddc = None
    level = None
    try:
        ddc = gd_ddc.Ddc()
        ddc.load()
        monitor = ddc.main()
        if monitor is not None:
            level = monitor.brightness()
            parts.append(i18n.t("DDC/CI: яркость {v0}%", v0=level)
                         if level is not None else i18n.t("DDC/CI: есть, яркость не читается"))
        else:
            parts.append(i18n.t("DDC/CI: нет ({v0})", v0=ddc.problem or "—"))
    except Exception as exc:  # noqa: BLE001
        parts.append(i18n.t("DDC/CI: ошибка — {v0}", v0=exc))
    finally:
        if ddc is not None:
            try:
                ddc.close()
            except Exception:  # noqa: BLE001
                pass

    try:
        rate = gd_hz.refresh_rate()
        rates = gd_hz.supported_rates()
        if rate:
            parts.append(i18n.t("частота {v0} Гц (режимов: {v1})", v0=rate, v1=len(rates)))
    except Exception as exc:  # noqa: BLE001
        parts.append(i18n.t("частота не читается: {v0}", v0=exc))

    status = OK if level is not None else WARN
    action = "" if level is not None else i18n.t(
        "монитор не умеет DDC/CI — ползунок подсветки будет выключен")
    return _check("display", i18n.t("Монитор"), status, "; ".join(parts) or "—", action)


def check_config_size() -> dict:
    """Размер presets.json: распухший конфиг замедляет старт и раздувает копии.

    История автопрофилей и профили запусков растут молча: за месяц работы
    файл может перевалить за мегабайт, и тогда каждая запись начинает
    переписывать его целиком.
    """
    path = gd_paths.config_path()
    if not path.is_file():
        return _check("config_size", i18n.t("Размер конфига"), SKIP,
                      i18n.t("файла ещё нет"))
    size = path.stat().st_size
    shown = f"{size / 1024:.0f} КБ" if size >= 1024 else f"{size} Б"
    if size > 512 * 1024:
        return _check("config_size", i18n.t("Размер конфига"), WARN, shown,
                      i18n.t("файл распух: почистите историю автопрофилей"))
    return _check("config_size", i18n.t("Размер конфига"), OK, shown)


def check_start_time() -> dict:
    """Сколько программа стартовала в прошлый раз.

    Замер пишет само приложение (`settings.last_boot_ms`) — это реальный путь
    от импорта до готового окна, а не «кажется, долго».
    """
    try:
        import json

        data = json.loads(gd_paths.config_path().read_text(encoding="utf-8"))
        value = float((data.get("settings") or {}).get("last_boot_ms") or 0)
    except Exception:  # noqa: BLE001
        value = 0.0
    if not value:
        return _check("start_time", i18n.t("Время старта"), SKIP,
                      i18n.t("ещё не измерялось"))
    shown = f"{value / 1000:.1f} с"
    if value > 3000:
        return _check("start_time", i18n.t("Время старта"), WARN, shown,
                      i18n.t("долгий старт: проверьте антивирус и папку данных"))
    return _check("start_time", i18n.t("Время старта"), OK, shown)


def checks() -> list[dict]:
    """Полный прогон. Ни одна проверка не роняет остальные."""
    items: list[dict] = []
    for fn in (check_windows, check_admin):
        try:
            items.append(fn())
        except Exception as exc:  # noqa: BLE001
            items.append(_check(fn.__name__, fn.__name__, FAIL, f"{type(exc).__name__}: {exc}"))
    for fn in (check_paths,):
        try:
            items.extend(fn())
        except Exception as exc:  # noqa: BLE001
            items.append(_check("paths", i18n.t("Пути"), FAIL, f"{type(exc).__name__}: {exc}"))
    for fn in (check_qt, check_gpu, check_sensors, check_dotnet, check_zapret,
               check_proxy, check_uac, check_display, check_config_size,
               check_start_time):
        try:
            items.append(fn())
        except Exception as exc:  # noqa: BLE001
            items.append(_check(fn.__name__, fn.__name__, FAIL, f"{type(exc).__name__}: {exc}"))
    return items


# --- отчёт ------------------------------------------------------------------
_USER = os.environ.get("USERNAME") or ""
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")


def anonymize(text: str) -> str:
    """Убрать из отчёта имя пользователя, IP и MAC.

    127.0.0.1 оставляем: это не адрес человека, а смысл проверки («локальный
    прокси слушает»). Остальные адреса и физические адреса вырезаем.
    """
    if _USER:
        text = text.replace(_USER, "<USER>")
    text = text.replace(str(Path.home()), "<HOME>")
    text = _MAC.sub("<MAC>", text)
    text = _IPV4.sub(lambda m: m.group(0) if m.group(0).startswith("127.") else "<IP>", text)
    return text


def report_text(items: list[dict] | None = None, ascii_only: bool = False) -> str:
    """Текст отчёта. `ascii_only` — для консоли Windows с кодовой страницей 866/1251:
    символы ✓ и ! там падают UnicodeEncodeError ещё до вывода."""
    items = items if items is not None else checks()
    marks = {"ok": "OK ", "warn": "!  ", "fail": "X  "} if ascii_only else MARKS
    lines = [f"Gammadrilon {_version()} · {platform.platform()} · {platform.machine()}"]
    for item in items:
        mark = marks.get(item["status"], "?")
        line = f"{mark} {item['title']}: {item['detail']}"
        if item.get("action"):
            line += f" → {item['action']}"
        lines.append(line)
    text = anonymize("\n".join(lines))
    if ascii_only:
        # стрелка и длинное тире есть не в каждой кодовой странице
        text = text.replace("→", "->").replace("·", "-").replace("—", "-")
    return text


def report_json(items: list[dict] | None = None) -> dict:
    items = items if items is not None else checks()
    return {
        "app": _version(),
        "os": platform.platform(),
        "arch": platform.machine(),
        "checks": [{k: anonymize(str(v)) for k, v in item.items()} for item in items],
    }


def _version() -> str:
    try:
        import main

        return str(getattr(main, "APP_VERSION", ""))
    except Exception:  # noqa: BLE001
        return ""
