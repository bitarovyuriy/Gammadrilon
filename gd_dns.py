
# -*- coding: utf-8 -*-
"""DNS-модуль (ТЗ 6.2).

Замер — реальные DNS-запросы (A, UDP/53) на IP резолвера, не ICMP-пинг.
Помощник сохраняет затрагиваемые настройки IPv4, IPv6 и DoH перед записью.

Подпись обязательна: «Смена DNS может ухудшить резолв в вашей сети. Сравнивайте
латентность до и после.»
"""
from __future__ import annotations

import base64
import ipaddress
import json
import random
import re
import socket
import struct
import time
from pathlib import Path
from uuid import UUID

import gd_i18n as i18n
import gd_paths
import gd_proc
import gd_state

RESOLVERS = [
    {"key": "cloudflare", "label": "Cloudflare", "servers": ["1.1.1.1", "1.0.0.1"],
     "doh": "https://cloudflare-dns.com/dns-query"},
    {"key": "google", "label": "Google", "servers": ["8.8.8.8", "8.8.4.4"],
     "doh": "https://dns.google/dns-query"},
    {"key": "quad9", "label": "Quad9", "servers": ["9.9.9.9", "149.112.112.112"],
     "doh": "https://dns.quad9.net/dns-query"},
    {"key": "adguard", "label": "AdGuard", "servers": ["94.140.14.14", "94.140.15.15"],
     "doh": "https://dns.adguard-dns.com/dns-query"},
    {"key": "yandex", "label": "Яндекс", "servers": ["77.88.8.8", "77.88.8.1"],
     "doh": None},
    {"key": "custom", "label": "Свой", "servers": [], "doh": None},
]

SIGNATURE = "Смена DNS может ухудшить резолв в вашей сети. Сравнивайте латентность до и после."
PROBE_NAME = "example.com"


def _base_dir() -> Path:
    # портативный режим (маркер рядом с приложением) кладёт состояние рядом с exe
    return gd_paths.data_dir()


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

        # Явные сигнатуры: на x64 необъявленный указатель ctypes передаёт как
        # 32-битный int — испорченный стек проявляется как 0xC0000409.
        iphlpapi.GetBestRoute.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        iphlpapi.GetBestRoute.restype = wintypes.DWORD
        iphlpapi.ConvertInterfaceIndexToLuid.argtypes = [wintypes.ULONG, ctypes.c_void_p]
        iphlpapi.ConvertInterfaceIndexToLuid.restype = wintypes.ULONG
        iphlpapi.ConvertInterfaceLuidToAlias.argtypes = [ctypes.c_void_p, wintypes.LPWSTR,
                                                         ctypes.c_size_t]
        iphlpapi.ConvertInterfaceLuidToAlias.restype = wintypes.ULONG

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
                name = line.split(maxsplit=4)[4]
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


def _dns_addresses(values: list[str], version: int | None = None) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(i18n.t("неверный адрес DNS"))
    addresses = []
    for value in values:
        if not isinstance(value, str) or ("%" in value and not re.fullmatch(r"[0-9]+", value.rsplit("%", 1)[1])):
            raise ValueError(i18n.t("неверный адрес DNS"))
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError(i18n.t("неверный адрес DNS")) from exc
        if version is not None and version != address.version:
            raise ValueError(i18n.t("неверный адрес DNS"))
        addresses.append(str(address))
    return addresses


def _parse_family(output: str, version: int) -> dict | None:
    # i18n: skip-start — вывод Windows, а не строки интерфейса
    dns_line = re.search(r"^[ \t]+([^:\r\n]*DNS[^:\r\n]*):[ \t]*(.*)$", output,
                         re.IGNORECASE | re.MULTILINE)
    if not dns_line:
        return None
    label, first = dns_line.groups()
    if re.search(r"статич|static", label, re.I):
        mode = "static"
    elif "dhcp" in label.lower():
        mode = "dhcp"
    else:
        return None
    fields = [first.strip()]
    for line in output[dns_line.end():].splitlines():
        line = line.strip()
        if line and not re.search(r"\s", line):
            fields.append(line)
        elif line:
            break
    if fields[0].casefold() in ("none", "нет") or not fields[0]:
        fields.pop(0)
    try:
        servers = _dns_addresses(fields, version)
    except ValueError:
        return None
    register_line = re.search(r"^\s*(?:Register with which suffix|Зарегистрировать с суффиксом)\s*:(.*)$",
                              output, re.IGNORECASE | re.MULTILINE)
    register = None
    if register_line:
        value = register_line[1].strip().casefold()
        if value in ("none", "нет"):
            register = "none"
        elif value in ("primary only", "только основной"):
            register = "primary"
        elif "primary" in value or "основн" in value:
            register = "both"
    # i18n: skip-end
    state = {"mode": mode, "servers": servers}
    if register is not None:
        state["register"] = register
    return state


def _read_family(iface: str, version: int) -> dict | None:
    code, out = _run(["netsh", "interface", f"ipv{version}", "show", "dnsservers", f"name={iface}"])
    return _parse_family(out, version) if code == 0 else None


def current_state(iface: str | None = None) -> dict:
    """Быстрое чтение IPv4 для вкладки и статусбара."""
    iface = iface or active_interface()
    state = _read_family(iface, 4) if iface else None
    return {"iface": iface, "mode": state["mode"] if state else None,
            "servers": state["servers"] if state else []}


def _adapter_id(iface: str) -> str | None:
    """Persist the NetLuid's GUID so an alias can be renamed or reused."""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("iphlpapi.dll")
    api.ConvertInterfaceAliasToLuid.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    api.ConvertInterfaceAliasToLuid.restype = wintypes.ULONG
    api.ConvertInterfaceLuidToGuid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    api.ConvertInterfaceLuidToGuid.restype = wintypes.ULONG
    luid = ctypes.c_ulonglong()
    guid = ctypes.create_string_buffer(16)
    if api.ConvertInterfaceAliasToLuid(iface, ctypes.byref(luid)) != 0:
        return None
    if api.ConvertInterfaceLuidToGuid(ctypes.byref(luid), guid) != 0:
        return None
    return str(UUID(bytes_le=guid.raw))


def _adapter_index(adapter_id: str) -> str | None:
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("iphlpapi.dll")
    api.ConvertInterfaceGuidToLuid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    api.ConvertInterfaceGuidToLuid.restype = wintypes.ULONG
    api.ConvertInterfaceLuidToIndex.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    api.ConvertInterfaceLuidToIndex.restype = wintypes.ULONG
    guid = ctypes.create_string_buffer(UUID(adapter_id).bytes_le, 16)
    luid = ctypes.c_ulonglong()
    index = wintypes.ULONG()
    if api.ConvertInterfaceGuidToLuid(guid, ctypes.byref(luid)) != 0:
        return None
    if api.ConvertInterfaceLuidToIndex(ctypes.byref(luid), ctypes.byref(index)) != 0:
        return None
    return str(index.value)


# -- применение (только через elevated-хелпер) --------------------------------
def _family_commands(iface: str, version: int, state: dict) -> list[list[str]]:
    prefix = ["netsh", "interface", f"ipv{version}"]
    mode, servers = state["mode"], state["servers"]
    command = [*prefix, "set", "dnsservers", f"name={iface}", f"source={mode}"]
    if "register" in state:
        command.append(f"register={state['register']}")
    if mode == "dhcp":
        return [command]
    command.extend([f"address={servers[0] if servers else 'none'}", "validate=no"])
    return [command, *(
        [*prefix, "add", "dnsservers", f"name={iface}", f"address={address}",
         f"index={index}", "validate=no"] for index, address in enumerate(servers[1:], 2)
    )]


def _run_checked(command: list[str], *, error: str | None = None) -> None:
    code, out = _run(command)
    if code != 0:
        raise OSError(out.strip() or error or i18n.t("netsh вернул ошибку"))


def _write_family(iface: str, version: int, state: dict) -> None:
    for command in _family_commands(iface, version, state):
        _run_checked(command)
    actual = _read_family(iface, version)
    matches = (actual and actual["mode"] == state["mode"]
               and (state["mode"] == "dhcp" or actual["servers"] == state["servers"])
               and ("register" not in state or actual.get("register") == state["register"]))
    if not matches:
        raise OSError(i18n.t("Windows не подтвердила настройки DNS для IPv{v0}.", v0=version))


def _requested_families(mode: str, servers: list[str]) -> dict[str, dict]:
    if mode not in ("static", "dhcp"):
        raise ValueError(i18n.t("неверная конфигурация DNS"))
    addresses = _dns_addresses(servers)
    if mode == "dhcp":
        return {str(version): {"mode": mode, "servers": []} for version in (4, 6)}
    if not addresses:
        raise ValueError(i18n.t("не заданы адреса DNS"))
    return {version: {"mode": mode, "servers": family} for version in ("4", "6")
            if (family := [a for a in addresses if ipaddress.ip_address(a).version == int(version)])}


def apply_native(mode: str, servers: list[str], iface: str) -> tuple[bool, str]:
    """Apply DNS with the same backup and rollback used by the helper."""
    if not isinstance(iface, str) or not iface:
        return False, i18n.t("неверная конфигурация DNS")
    result = apply_provider(servers, mode=mode, iface=iface)
    return result["ok"], result["data"] if result["ok"] else result["error"]


def doh_supported() -> bool:
    try:
        _read_doh()
    except (OSError, ValueError):
        return False
    return True


def _valid_doh(config: dict | None) -> bool:
    return isinstance(config, dict) and (
        isinstance(config.get("template"), str)
        and bool(re.fullmatch(r"https://[^\s\x00\"']+", config["template"], re.I))
        and type(config.get("upgrade")) is bool and type(config.get("fallback")) is bool
    )


def _read_doh() -> dict[str, dict]:
    # Structured output distinguishes an absent entry from an unreadable table.
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$rows = @(Get-DnsClientDohServerAddress | "
        "Select-Object ServerAddress,DohTemplate,AutoUpgrade,AllowFallbackToUdp); "
        "ConvertTo-Json -InputObject $rows -Compress"
    )
    code, out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    if code != 0:
        raise OSError(out.strip() or i18n.t("не удалось настроить DoH"))
    rows = json.loads(out)
    if not isinstance(rows, list):
        raise ValueError(i18n.t("не удалось прочитать конфигурацию DoH"))
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(i18n.t("не удалось прочитать конфигурацию DoH"))
        server, = _dns_addresses([row.get("ServerAddress")])
        config = {"template": row.get("DohTemplate"), "upgrade": row.get("AutoUpgrade"),
                  "fallback": row.get("AllowFallbackToUdp")}
        if not _valid_doh(config):
            raise ValueError(i18n.t("не удалось прочитать конфигурацию DoH"))
        result[server] = config
    return result


def _write_doh(changes: dict[str, dict | None], current: dict[str, dict]) -> None:
    commands = ["$ErrorActionPreference = 'Stop'"]
    for server, config in changes.items():
        if config == current.get(server):
            continue
        if config is None:
            commands.append(f"Remove-DnsClientDohServerAddress -ServerAddress '{server}' -Confirm:$false")
        else:
            action = "Set" if server in current else "Add"
            upgrade = "$true" if config["upgrade"] else "$false"
            fallback = "$true" if config["fallback"] else "$false"
            template = config["template"].replace("'", "''")
            commands.append(f"{action}-DnsClientDohServerAddress -ServerAddress '{server}' "
                            f"-DohTemplate '{template}' -AutoUpgrade {upgrade} -AllowFallbackToUdp {fallback} "
                            "-Confirm:$false")
    if len(commands) == 1:
        return
    payload = base64.b64encode("; ".join(commands).encode("utf-16-le")).decode("ascii")
    _run_checked(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", payload],
                 error=i18n.t("не удалось настроить DoH"))
    actual = _read_doh()
    if any(actual.get(server) != config for server, config in changes.items()):
        raise OSError(i18n.t("Windows не подтвердила настройки DoH."))


def _restore_doh(changes: dict[str, dict | None]) -> tuple[dict, str]:
    try:
        _write_doh(changes, _read_doh())
    except (OSError, ValueError) as exc:
        try:
            actual = _read_doh()
        except (OSError, ValueError):
            return changes, str(exc)
        pending = {server: config for server, config in changes.items() if actual.get(server) != config}
        return pending, str(exc)
    return {}, ""


def _validated_family(state: dict, version: int) -> dict:
    if not isinstance(state, dict) or state.get("mode") not in ("dhcp", "static"):
        raise ValueError(i18n.t("неверная конфигурация DNS"))
    family = {"mode": state["mode"], "servers": _dns_addresses(state.get("servers"), version)}
    if state.get("register") in ("none", "primary", "both"):
        family["register"] = state["register"]
    elif "register" in state:
        raise ValueError(i18n.t("неверная конфигурация DNS"))
    return family


def _bind_ipv6_scope(state: dict, previous_index: str, current_index: str) -> dict:
    own_scope = f"%{previous_index}"
    return {**state, "servers": [
        f"{address.removesuffix(own_scope)}%{current_index}"
        if address.endswith(own_scope) and ipaddress.IPv6Address(address).is_link_local else address
        for address in state["servers"]
    ]}


def _load_backup() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"adapters": {}, "doh": {}}
    if not isinstance(data, dict):
        raise ValueError(i18n.t("неверная конфигурация DNS"))
    if data == {"iface": None, "mode": None, "servers": []}:
        # Old releases wrote this placeholder when they could not select an adapter.
        return {"adapters": {}, "doh": {}}
    if "version" not in data and isinstance(data.get("iface"), str) and data["iface"]:
        # Older snapshots only read IPv4. Never infer IPv6 from them.
        alias = data["iface"]
        data = {"version": 1, "adapters": {alias: {"iface": alias, "families": {"4": data}}}, "doh": {}}
    if data.get("version") != 1 or not isinstance(data.get("adapters"), dict) or not isinstance(data.get("doh"), dict):
        raise ValueError(i18n.t("неверная конфигурация DNS"))
    adapters = {}
    for key, entry in data["adapters"].items():
        if not isinstance(entry, dict):
            raise ValueError(i18n.t("неверная конфигурация DNS"))
        name, families = entry["iface"], entry["families"]
        if not isinstance(name, str) or not name or "\x00" in name or not isinstance(families, dict):
            raise ValueError(i18n.t("неверная конфигурация DNS"))
        adapter_id = entry.get("guid")
        if (adapter_id is None and key != name) or (adapter_id is not None and str(UUID(adapter_id)) != key):
            raise ValueError(i18n.t("неверная конфигурация DNS"))
        if not set(families) <= {"4", "6"}:
            raise ValueError(i18n.t("неверная конфигурация DNS"))
        adapters[key] = {"iface": name, "guid": adapter_id, "families": {
            version: _validated_family(state, int(version)) for version, state in families.items()
        }}
        scope_index = entry.get("scope_index")
        if scope_index is not None:
            if not isinstance(scope_index, str) or not re.fullmatch(r"[0-9]+", scope_index):
                raise ValueError(i18n.t("неверная конфигурация DNS"))
            adapters[key]["scope_index"] = scope_index
    doh = {}
    for server, config in data["doh"].items():
        address, = _dns_addresses([server])
        if config is not None and not _valid_doh(config):
            raise ValueError(i18n.t("не удалось прочитать конфигурацию DoH"))
        doh[address] = config
    return {"adapters": adapters, "doh": doh}


def _save_backup(backup: dict) -> None:
    if not gd_state.write_json_atomic(state_path(), {"version": 1, **backup}):
        raise OSError(i18n.t("не удалось сохранить резервную копию DNS"))


def legacy_snapshot_aliases() -> list[str]:
    """Aliases for which the DNS tab can ask the user to verify the adapter."""
    try:
        backup = _load_backup()
    except (OSError, ValueError, TypeError, KeyError):
        return []
    return [entry["iface"] for entry in backup["adapters"].values() if not entry["guid"]]


def _restore_families(index: str, families: dict[str, dict]) -> tuple[dict, list[str]]:
    pending = {}
    errors = []
    for version, state in families.items():
        try:
            _write_family(index, int(version), state)
        except OSError as exc:
            pending[version] = state
            errors.append(str(exc))
    return pending, errors


def apply_provider(servers: list[str], *, template: str | None = None,
                   mode: str = "static", iface: str | None = None) -> dict:
    """Apply one DNS change under the helper's operation lock."""
    try:
        requested = _requested_families(mode, servers)
        doh_config = {"template": template, "upgrade": True, "fallback": False}
        if template is not None and (mode != "static" or not _valid_doh(doh_config)):
            raise ValueError(i18n.t("для этого резолвера нет шаблона DoH"))
        if iface is not None and (not isinstance(iface, str) or not iface or "\x00" in iface):
            raise ValueError(i18n.t("неверная конфигурация DNS"))
        iface = iface or active_interface(force=True)
        adapter_id = _adapter_id(iface) if iface else None
        index = _adapter_index(adapter_id) if adapter_id else None
        if not index:
            raise OSError(i18n.t("сетевой адаптер не найден"))
        before = {}
        for version in requested:
            state = _read_family(index, int(version))
            if state is None:
                raise OSError(i18n.t("не удалось прочитать конфигурацию DNS для IPv{v0}", v0=version))
            before[version] = state
            if "register" in state:
                requested[version]["register"] = state["register"]
        changes = ({server: doh_config for state in requested.values() for server in state["servers"]}
                   if template is not None else {})
        doh_table = _read_doh() if changes else {}
        old_doh = {server: doh_table.get(server) for server in changes}
        previous_backup = _load_backup()
        backup = {"adapters": previous_backup["adapters"].copy(), "doh": {**old_doh, **previous_backup["doh"]}}
        if any(not entry["guid"] and entry["iface"].casefold() == iface.casefold()
               for entry in backup["adapters"].values()):
            raise ValueError(i18n.t("Проверьте адаптер «{v0}» и восстановите старый снимок на вкладке DNS.", v0=iface))
        existing = backup["adapters"].get(adapter_id) or {"families": {}}
        scope_index = existing.get("scope_index", index)
        original_families = existing["families"].copy()
        if "6" in original_families and scope_index != index:
            original_families["6"] = _bind_ipv6_scope(original_families["6"], scope_index, index)
        backup["adapters"][adapter_id] = {"iface": iface, "guid": adapter_id,
                                          "scope_index": index, "families": {**before, **original_families}}
        _save_backup(backup)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}

    started = {}
    doh_started = False
    try:
        if changes:
            doh_started = True
            try:
                _write_doh(changes, doh_table)
            except (OSError, ValueError) as exc:
                raise OSError(f"DoH: {exc}") from exc
        for version, state in requested.items():
            started[version] = before[version]
            _write_family(index, int(version), state)
        ok, message = flush_native()
        if not ok:
            raise OSError(message)
    except (OSError, ValueError) as exc:
        pending_families, errors = _restore_families(index, started)
        pending_doh, doh_error = _restore_doh(old_doh) if doh_started else ({}, "")
        if pending_doh:
            errors.append(f"DoH: {doh_error}")
        flush_native()
        partial = bool(errors)
        retry = {"adapters": previous_backup["adapters"].copy(), "doh": {**pending_doh, **previous_backup["doh"]}}
        if pending_families:
            retry["adapters"][adapter_id] = {"iface": iface, "guid": adapter_id,
                                             "scope_index": index, "families": {**pending_families, **original_families}}
        if retry != backup:
            try:
                _save_backup(retry)
            except OSError as backup_error:
                errors.append(str(backup_error))
        return {"ok": False, "partial": partial, "error": "; ".join([str(exc), *errors])}

    return {"ok": True, "data": i18n.t("DNS обновлён"), "remembered": True,
            "before": {"iface": iface, "servers": [server for state in before.values() for server in state["servers"]]},
            "doh": i18n.t("DoH включён") if changes else ""}


def restore_backups(confirmed_aliases: list[str] | None = None) -> dict:
    """Restore prior DNS and DoH, retaining unconfirmed entries for a retry."""
    try:
        backup = _load_backup()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    pending = {"adapters": {}, "doh": {}}
    restored = []
    errors = []
    attempted = bool(backup["doh"])
    for key, entry in backup["adapters"].items():
        name = entry["iface"]
        if not entry["guid"] and name not in (confirmed_aliases or []):
            pending["adapters"][key] = entry
            errors.append(i18n.t("Проверьте адаптер «{v0}» и восстановите старый снимок на вкладке DNS.", v0=name))
            continue
        adapter_id = entry["guid"] or _adapter_id(name)
        index = _adapter_index(adapter_id) if adapter_id else None
        if not index:
            pending["adapters"][key] = entry
            errors.append(f"{name}: {i18n.t('сетевой адаптер не найден')}")
            continue
        families = entry["families"].copy()
        scope_index = entry.get("scope_index", index)
        if "6" in families and scope_index != index:
            families["6"] = _bind_ipv6_scope(families["6"], scope_index, index)
        attempted |= bool(families)
        pending_families, failures = _restore_families(index, families)
        errors.extend(f"{name}: {message}" for message in failures)
        if pending_families:
            pending["adapters"][key] = {**entry, "scope_index": index, "families": pending_families}
        else:
            restored.append(name)
    if backup["doh"]:
        pending["doh"], failure = _restore_doh(backup["doh"])
        if pending["doh"]:
            errors.append(f"DoH: {failure}")
    changed = pending != backup
    if attempted:
        ok, message = flush_native()
        if not ok:
            errors.append(message)
    if changed:
        try:
            _save_backup(pending)
        except OSError as exc:
            errors.append(str(exc))
    return {"ok": not errors, "changed": changed, "restored": restored, "error": "; ".join(errors) or None}


def flush_native() -> tuple[bool, str]:
    """Сбросить кэш DNS."""
    import ctypes

    try:
        dnsapi = ctypes.WinDLL("dnsapi")
        dnsapi.DnsFlushResolverCache.argtypes = []
        dnsapi.DnsFlushResolverCache.restype = ctypes.c_int
        if dnsapi.DnsFlushResolverCache():
            return True, i18n.t("кэш DNS сброшен")
    except Exception:  # noqa: BLE001
        pass
    code, out = _run(["ipconfig", "/flushdns"])
    ok = code == 0
    return ok, (i18n.t("кэш DNS сброшен") if ok else out.strip())
