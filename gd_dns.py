
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

import json
import os
import random
import re
import socket
import struct
import subprocess
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
        p = subprocess.run(args, capture_output=True, timeout=25)
        raw = (p.stdout or b"") + (p.stderr or b"")
        # netsh и часть утилит Windows отдают UTF-8; запасной путь — OEM-кодировка
        try:
            out = raw.decode("utf-8")
        except UnicodeDecodeError:
            out = raw.decode("cp866", errors="replace")
        return p.returncode, out
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def active_interface() -> str | None:
    # интерфейс, через который идёт маршрут по умолчанию (есть шлюз)
    code, out = _run([
        "powershell", "-NoProfile", "-Command",
        "(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | Select-Object -First 1).InterfaceAlias",
    ])
    if code == 0 and out.strip():
        # первая непустая строка, которая не является заголовком/предупреждением
        for line in out.strip().splitlines():
            line = line.strip()
            if line and not line.startswith("[") and "InterfaceAlias" not in line:
                return line
    # запасной путь: netsh, пропуская псевдо-интерфейсы
    code, out = _run(["netsh", "interface", "ipv4", "show", "interfaces"])
    if code != 0:
        return None
    for line in out.splitlines():
        if re.search(r"\b(connected|подключ\w*)\b", line, re.IGNORECASE):
            parts = line.split()
            if len(parts) >= 5:
                name = " ".join(parts[4:])
                if re.search(r"loopback|pseudo|tunnel|туннель", name, re.IGNORECASE):
                    continue
                return name
    return None


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
    # статические записи важнее: если они есть — режим статический
    mode = "static" if static_servers else ("dhcp" if dhcp_servers else None)
    servers = static_servers if static_servers else dhcp_servers
    if not servers:
        # надёжный запасной путь: PowerShell-оbject
        code2, out2 = _run([
            "powershell", "-NoProfile", "-Command",
            f"(Get-DnsClientServerAddress -InterfaceAlias '{iface}' -AddressFamily IPv4).ServerAddresses -join ','",
        ])
        if code2 == 0:
            servers = [s.strip() for s in out2.strip().split(",") if re.match(r"^\d+\.\d+\.\d+\.\d+$", s.strip())]
    return {"iface": iface, "mode": mode or ("static" if servers else "dhcp"), "servers": servers}


def save_state() -> dict:
    st = current_state()
    try:
        p = state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return st


def saved_state() -> dict | None:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


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
