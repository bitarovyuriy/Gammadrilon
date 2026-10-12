
# -*- coding: utf-8 -*-
"""DNS-модуль (ТЗ 6.2).

Замер — реальные DNS-запросы (A, UDP/53) на IP резолвера, не ICMP-пинг.
Смена — только явный список резолверов, через `netsh interface ipv4 set dnsservers`,
со **сначала прочитанной и сохранённой** текущей конфигурацией (включая DHCP-случай),
чтобы откат был точным. DoH — только если ОС поддерживает (Windows 11).

Подпись обязательна: «Смена DNS может ухудшить резолв в вашей сети. Сравнивайте
латентность до и после.»
"""
from __future__ import annotations

import gd_i18n as i18n
import gd_proc
import gd_state

import os
import random
import re
import socket
import struct
import time
from pathlib import Path

RESOLVERS = [
    {"key": "cloudflare", "label": "Cloudflare", "servers": ["1.1.1.1", "1.0.0.1"],
     "doh": "https://cloudflare-dns.com/dns-query"},
    {"key": "google", "label": "Google", "servers": ["8.8.8.8", "8.8.4.4"],
     "doh": "https://dns.google/dns-query"},
    {"key": "quad9", "label": "Quad9", "servers": ["9.9.9.9", "149.112.112.112"],
     "doh": "https://dns.quad9.net/dns-query"},
    {"key": "adguard", "label": "AdGuard", "servers": ["94.140.14.14", "94.140.15.15"],
     "doh": "https://dns.adguard-dns.com/dns-query"},
    {"key": "yandex", "label": i18n.t("Яндекс"), "servers": ["77.88.8.8", "77.88.8.1"],
     "doh": None},
    {"key": "custom", "label": i18n.t("Свой"), "servers": [], "doh": None},
]

SIGNATURE = (
    i18n.t("Смена DNS может ухудшить резолв в вашей сети. Сравнивайте латентность до и после.")
)
PROBE_NAME = "example.com"


def _base_dir() -> Path:
    base = os.environ.get("ProgramData", r"C:\ProgramData")
    return Path(base) / "Gammadrilon"


def state_path() -> Path:
    return _base_dir() / "dns_state.json"


# -- запросы -----------------------------------------------------------------
def dns_query_time(server: str, name: str = PROBE_NAME, timeout: float = 2.0):
    """Время ответа DNS в мс. Возвращает (мс, ok). Реальный A-запрос по UDP/53."""
    tid = random.randint(0, 65535)
    header = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.split(".")) + b"\x00"
    packet = header + qname + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(timeout)
        t0 = time.perf_counter()
        s.sendto(packet, (server, 53))
        data, _ = s.recvfrom(2048)
        dt = (time.perf_counter() - t0) * 1000.0
        if len(data) < 12:
            return None, False
        rid = struct.unpack(">H", data[:2])[0]
        rcode = data[3] & 0x0F
        return (dt, True) if rid == tid and rcode == 0 else (None, False)
    except OSError:
        return None, False
    finally:
        s.close()


def measure(servers: list[str], per_server: int = 3, timeout: float = 2.0) -> dict:
    """Медиана и jitter по каждому серверу. Первый запрос отбрасывается как прогрев."""
    out = {}
    for srv in servers:
        times = []
        losses = 0
        for i in range(per_server):
            dt, ok = dns_query_time(srv, timeout=timeout)
            if ok and dt is not None:
                if i > 0:                     # первый — прогрев
                    times.append(dt)
            else:
                losses += 1
        if times:
            times.sort()
            median = times[len(times) // 2] if len(times) % 2 else sum(times) / len(times)
            jitter = (max(times) - min(times)) if len(times) > 1 else 0.0
            out[srv] = {"median": round(median, 1), "jitter": round(jitter, 1),
                        "loss_pct": round(losses / per_server * 100), "ok": True}
        else:
            out[srv] = {"median": None, "jitter": None,
                        "loss_pct": round(losses / per_server * 100), "ok": False}
    return out


def measure_resolvers(timeout: float = 2.0) -> dict:
    """Замер по всем резолверам списка (по первому адресу каждого)."""
    out = {}
    for r in RESOLVERS:
        if not r["servers"]:
            continue
        srv = r["servers"][0]
        out[r["key"]] = {"server": srv, **measure([srv], timeout=timeout).get(srv, {})}
    return out


# -- текущая конфигурация ----------------------------------------------------
def _run(args: list[str]) -> tuple[int, str]:
    try:
        # через gd_proc: без окна консоли (иначе netsh/powershell мелькают чёрным)
        p = gd_proc.run(args, timeout=25)
        raw = (p.stdout or b"") + (p.stderr or b"")
        # netsh и часть утилит Windows отдают UTF-8; запасной путь — OEM-кодировка
        try:
            out = raw.decode("utf-8")
        except UnicodeDecodeError:
            out = raw.decode("cp866", errors="replace")
        return p.returncode, out
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


#: кэш активного интерфейса: сеть меняется редко, а каждый опрос — это работа
_IFACE_TTL = 60.0
_iface_cache: tuple[str | None, float] = (None, 0.0)


def _iface_native() -> str | None:
    """Интерфейс маршрута по умолчанию через Win32 (iphlpapi) — ~30 мкс.

    ``GetBestRoute(0, 0)`` отдаёт строку маршрутной таблицы, по которой пойдёт
    трафик «в интернет»; ``ConvertInterfaceIndexToLuid`` +
    ``ConvertInterfaceLuidToAlias`` превращают индекс в имя (FriendlyName) —
    ровно то, которое понимает ``netsh ... name=``.
    """
    try:
        import ctypes
        from ctypes import wintypes

        iphlpapi = ctypes.WinDLL("iphlpapi.dll")

        class MIB_IPFORWARDROW(ctypes.Structure):
            _fields_ = [(field, wintypes.DWORD) for field in (
                "dwForwardDest", "dwForwardMask", "dwForwardPolicy", "dwForwardNextHop",
                "dwForwardIfIndex", "dwForwardType", "dwForwardProto", "dwForwardAge",
                "dwForwardNextHopAS", "dwForwardMetric1", "dwForwardMetric2",
                "dwForwardMetric3", "dwForwardMetric4", "dwForwardMetric5")]

        row = MIB_IPFORWARDROW()
        if iphlpapi.GetBestRoute(0, 0, ctypes.byref(row)) != 0:
            return None
        luid = ctypes.c_ulonglong(0)
        if iphlpapi.ConvertInterfaceIndexToLuid(
                ctypes.c_ulong(row.dwForwardIfIndex), ctypes.byref(luid)) != 0:
            return None
        alias = ctypes.create_unicode_buffer(257)
        if iphlpapi.ConvertInterfaceLuidToAlias(
                ctypes.byref(luid), alias, ctypes.c_size_t(257)) != 0:
            return None
        return alias.value or None
    except Exception:  # noqa: BLE001
        return None


def _iface_netsh() -> str | None:
    """Запасной путь: первый подключённый интерфейс из `netsh`, без псевдо-имён."""
    code, out = _run(["netsh", "interface", "ipv4", "show", "interfaces"])
    if code != 0:
        return None
    for line in out.splitlines():
        # i18n: skip (регулярка под вывод netsh на двух языках — не UI-строка)
        if re.search(r"\b(connected|подключ\w*)\b", line, re.IGNORECASE):
            parts = line.split()
            if len(parts) >= 5:
                name = " ".join(parts[4:])
                # i18n: skip (регулярка под вывод netsh на двух языках — не UI-строка)
                if re.search(r"loopback|pseudo|tunnel|туннель", name, re.IGNORECASE):
                    continue
                return name
    return None


def active_interface(force: bool = False) -> str | None:
    """Имя интерфейса с маршрутом по умолчанию (то, что ждёт ``netsh name=``).

    Грабли: `Get-NetIPConfiguration` в PowerShell на этой системе стоит **~3 с CPU**
    за запуск (CIM + автозагрузка модуля), а вызывался он в фоновом опросе раз в
    30 с — это ~8 % ядра постоянно (замер `_tmp_idle.py`). Поэтому основной путь —
    Win32 (~30 мкс), запасной — `netsh`, а результат кэшируется на :data:`_IFACE_TTL`.
    """
    global _iface_cache
    name, ts = _iface_cache
    if not force and name and (time.monotonic() - ts) < _IFACE_TTL:
        return name

    name = _iface_native() or _iface_netsh()
    if name:
        _iface_cache = (name, time.monotonic())
    return name


def current_state(iface: str | None = None) -> dict:
    """{'iface','mode':'dhcp'|'static','servers':[...]} — читается ДО любых изменений."""
    iface = iface or active_interface()
    if not iface:
        return {"iface": None, "mode": None, "servers": []}
    code, out = _run(["netsh", "interface", "ipv4", "show", "config", f"name={iface}"])
    dhcp_servers: list[str] = []
    static_servers: list[str] = []
    grab = None
    if code == 0:
        for line in out.splitlines():
            # i18n: skip-start — регулярки под вывод netsh RU/EN, не UI-строки
            if re.search(r"DNS-сервер\w*.*(?:через DHCP|through DHCP)", line):
                grab = "dhcp"
                tail = line.split(":", 1)[-1].strip()
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", tail):
                    dhcp_servers.append(tail)
                continue
            if re.search(r"DNS-сервер\w*.*(?:статически|statically)", line):
                grab = "static"
                tail = line.split(":", 1)[-1].strip()
                for part in re.findall(r"\d+\.\d+\.\d+\.\d+", tail):
                    static_servers.append(part)
                continue
            if grab:
                m = re.match(r"^\s*(\d+\.\d+\.\d+\.\d+)\s*$", line)
                if m:
                    (static_servers if grab == "static" else dhcp_servers).append(m.group(1))
                elif line.strip() and not line.strip().startswith("("):
                    grab = None
            # i18n: skip-end
    # статические записи важнее: если они есть — режим статический
    mode = "static" if static_servers else ("dhcp" if dhcp_servers else None)
    servers = static_servers if static_servers else dhcp_servers
    if not servers:
        # запасной путь — тоже netsh (PowerShell стоит ~3 с CPU за запуск)
        code2, out2 = _run(["netsh", "interface", "ipv4", "show", "dnsservers", f"name={iface}"])
        if code2 == 0:
            servers = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", out2)
    return {"iface": iface, "mode": mode or ("static" if servers else "dhcp"), "servers": servers}


def save_state() -> bool:
    """Запомнить текущую конфигурацию DNS как «как было» — атомарно.

    False — записать не удалось: «Вернуть как было» после перезапуска не сработает.
    Вызывающий должен сказать об этом честно, а не считать, что запомнил.
    """
    return gd_state.write_json_atomic(state_path(), current_state())


def saved_state() -> dict | None:
    """Запомненная конфигурация «как было». Не объект/битый/нет файла — None."""
    data = gd_state.read_json(state_path())
    return data if isinstance(data, dict) else None


def remember_before_change() -> tuple[bool, dict]:
    """Прочитать текущую конфигурацию и запомнить её как «как было» — до смены.

    Запоминаем один раз на интерфейс: иначе вторая смена DNS затрёт исходное
    состояние, и «Вернуть как было» вернёт не исходное, а предыдущее изменение.
    Один код и для вкладки «DNS», и для действия правила («Сменить DNS»).

    Возвращает (запомнено?, конфигурация «как было»): отказ записи не прячем —
    откат после перезапуска будет недоступен, и пользователь должен знать.
    """
    before = current_state()
    saved = saved_state() or {}
    if not saved.get("iface") or saved.get("iface") != before.get("iface"):
        return save_state(), before
    return True, before


# -- применение (только через elevated-хелпер) --------------------------------
def apply_native(mode: str, servers: list[str], iface: str) -> tuple[bool, str]:
    """Установить DNS. Вызывается из elevated-хелпера (netsh требует админа)."""
    if mode == "dhcp":
        code, out = _run(["netsh", "interface", "ipv4", "set", "dnsservers",
                          f"name={iface}", "source=dhcp"])
        if code != 0 or i18n.t("требуется повышение прав") in out:
            return False, out.strip() or i18n.t("netsh вернул ошибку")
        _run(["netsh", "interface", "ipv6", "set", "dnsservers",
              f"name={iface}", "source=dhcp"])
    else:
        if not servers:
            return False, i18n.t("не заданы адреса DNS")
        code, out = _run(["netsh", "interface", "ipv4", "set", "dnsservers",
                          f"name={iface}", "source=static",
                          f"address={servers[0]}", "register=primary", "validate=no"])
        if code != 0 or i18n.t("требуется повышение прав") in out:
            return False, out.strip() or i18n.t("netsh вернул ошибку")
        for i, srv in enumerate(servers[1:], start=2):
            _run(["netsh", "interface", "ipv4", "add", "dnsservers",
                  f"name={iface}", f"address={srv}", f"index={i}", "validate=no"])
    flush_native()
    return True, i18n.t("DNS обновлён")


def doh_supported() -> bool:
    code, out = _run(["netsh", "dns", "show", "encryption"])
    return code == 0 and "HTTPS" in out


def doh_native(servers: list[str], template: str | None, enable: bool) -> tuple[bool, str]:
    """Включить/выключить DoH для адресов (Windows 11)."""
    if not template:
        return False, i18n.t("для этого резолвера нет шаблона DoH")
    last = ""
    for srv in servers:
        args = ["powershell", "-NoProfile", "-Command"]
        if enable:
            cmd = (f"Set-DnsClientDohServerAddress -ServerAddress '{srv}' "
                   f"-DohTemplate '{template}' -AllowFallbackToUdp $false -AutoUpgrade $true")
        else:
            cmd = f"Remove-DnsClientDohServerAddress -ServerAddress '{srv}' -ErrorAction SilentlyContinue"
        code, out = _run(args + [cmd])
        last = out.strip()
        if code != 0:
            return False, last or i18n.t("не удалось настроить DoH")
    return True, (i18n.t("DoH включён") if enable else i18n.t("DoH выключен"))


def flush_native() -> tuple[bool, str]:
    """Сбросить кэш DNS."""
    import ctypes

    try:
        dnsapi = ctypes.WinDLL("dnsapi")
        dnsapi.DnsFlushResolverCache()
    except Exception:  # noqa: BLE001
        pass
    code, out = _run(["ipconfig", "/flushdns"])
    ok = i18n.t("Не удалось") not in out and i18n.t("не удалось") not in out
    return ok, (i18n.t("кэш DNS сброшен") if ok else out.strip())
