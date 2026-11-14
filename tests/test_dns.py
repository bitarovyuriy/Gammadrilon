# -*- coding: utf-8 -*-
"""Регрессии разбора DNS: netsh ``show config`` врёт про DHCP."""
from __future__ import annotations

import base64
import sys
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_dns  # noqa: E402


def test_static_dns_is_not_reported_as_dhcp(monkeypatch):
    """Windows 11 пишет «через DHCP» и для статических адресов.

    Именно на этом приложение сбрасывало DNS на серверы роутера при
    «восстановлении»: считало статический режим DHCP-шным.
    """
    output = (
        'Настройка интерфейса "Ethernet"\n'
        "    Статически настроенные DNS-серверы:    1.1.1.1\n"
        "                                          77.88.8.1\n"
        "    Зарегистрировать с суффиксом:           Только основной\n"
    )

    def fake_run(args):
        assert args[-1] == "name=Ethernet"
        return 0, output

    monkeypatch.setattr(gd_dns, "_run", fake_run)
    state = gd_dns.current_state("Ethernet")
    assert state == {"iface": "Ethernet", "mode": "static",
                     "servers": ["1.1.1.1", "77.88.8.1"]}


def test_dhcp_dns_is_reported_as_dhcp(monkeypatch):
    output = (
        'Configuration for interface "Ethernet"\n'
        "    DNS servers configured through DHCP:  192.168.0.1\n"
        "    Register with which suffix:           Primary only\n"
    )
    monkeypatch.setattr(gd_dns, "_run", lambda args: (0, output))
    state = gd_dns.current_state("Ethernet")
    assert state == {"iface": "Ethernet", "mode": "dhcp",
                     "servers": ["192.168.0.1"]}


def test_no_dns_returns_empty_not_gateway(monkeypatch):
    output = ('Настройка интерфейса "Ethernet"\n'
              '    DNS-серверы со статической настройкой:  Нет\n')
    monkeypatch.setattr(gd_dns, "_run", lambda args: (0, output))
    state = gd_dns.current_state("Ethernet")
    assert state == {"iface": "Ethernet", "mode": "static", "servers": []}


def test_failed_or_unrecognized_dns_read_does_not_infer_dhcp(monkeypatch):
    monkeypatch.setattr(gd_dns, "_run", lambda args: (0, ""))
    assert gd_dns.current_state("Ethernet") == {"iface": "Ethernet", "mode": None, "servers": []}


def test_ipv6_query_parses_scoped_addresses_and_suffix_registration():
    out = ('Настройка интерфейса "VPN DNS: 172.16.0.1"\n'
           '    DNS-серверы со статической настройкой: fec0:0:0:ffff::1%16\n'
           '                                          2001:db8::1\n'
           '    Зарегистрировать с суффиксом:        Только основной\n')
    assert gd_dns._parse_family(out, 6) == {
        "mode": "static", "servers": ["fec0:0:0:ffff::1%16", "2001:db8::1"], "register": "primary",
    }


@pytest.fixture
def network(monkeypatch, tmp_path):
    adapters = {"Ethernet": "b4673e5c-8429-46d3-85f3-ef013fbac191", "Wi-Fi": "b4673e5c-8429-46d3-85f3-ef013fbac192"}
    indices = dict(zip(adapters.values(), ("11", "12")))
    primary = {"mode": "dhcp", "servers": ["192.168.1.1"], "register": "primary"}
    static_ipv6 = {"mode": "static", "servers": ["fd00::1"], "register": "none"}
    states = {("11", 4): primary, ("11", 6): static_ipv6, ("12", 4): primary.copy(), ("12", 6): static_ipv6.copy()}
    doh = {"1.1.1.1": {"template": "https://original.example/dns-query", "upgrade": False, "fallback": True}}
    entry = dict(families=states, doh=doh, indices=indices, adapters=adapters)
    monkeypatch.setattr(gd_dns, "state_path", lambda: tmp_path / "dns.json")
    monkeypatch.setattr(gd_dns, "active_interface", lambda force=False: "Ethernet")
    monkeypatch.setattr(gd_dns, "_adapter_id", adapters.get)
    monkeypatch.setattr(gd_dns, "_adapter_index", indices.get)
    monkeypatch.setattr(gd_dns, "_read_family", lambda index, version: deepcopy(states.get((index, version))))
    monkeypatch.setattr(gd_dns, "_read_doh", lambda: deepcopy(doh))
    monkeypatch.setattr(gd_dns, "flush_native", lambda: (True, "flushed"))

    def write_family(index, version, state):
        assert gd_dns.state_path().exists()
        states[index, version] = deepcopy(state)

    def write_doh(changes, _current):
        assert gd_dns.state_path().exists()
        for server, config in changes.items():
            if config is None:
                doh.pop(server, None)
            else:
                doh[server] = deepcopy(config)

    monkeypatch.setattr(gd_dns, "_write_family", write_family)
    monkeypatch.setattr(gd_dns, "_write_doh", write_doh)
    return entry


def test_backup_retains_originals_per_family_and_adapter_across_route_changes(network):
    originals = deepcopy(network["families"])
    assert gd_dns.apply_provider(["1.1.1.1"], iface="Ethernet")["ok"]
    assert gd_dns.apply_provider(["8.8.8.8"], iface="Wi-Fi")["ok"]
    assert gd_dns.apply_provider(["2001:4860::1"], iface="Ethernet")["ok"]
    assert gd_dns.apply_provider(["1.0.0.1"], iface="Ethernet")["ok"]
    network["adapters"]["Ethernet"] = "reused-alias"
    gd_dns.restore_backups()
    assert network["families"] == originals
    assert gd_dns.restore_backups() == {"ok": True, "changed": False, "restored": [], "error": None}


def test_restore_rebinds_scoped_ipv6_to_same_adapter_at_new_index(network):
    families = network["families"]
    families["11", 6] = {"mode": "static", "servers": ["fe80::1%11", "fe80::2%44", "fec0::1%11"], "register": "none"}
    assert gd_dns.apply_provider(["2001:4860::1"])["ok"]
    network["indices"][network["adapters"]["Ethernet"]] = "27"
    families["27", 6] = families.pop(("11", 6))
    assert gd_dns.restore_backups()["ok"]
    assert families["27", 6]["servers"] == ["fe80::1%27", "fe80::2%44", "fec0::1%11"]


@pytest.mark.parametrize("failure", ["disk", "family read", "doh read", "corrupt backup"])
def test_dns_snapshot_failure_prevents_all_writes(network, monkeypatch, failure):
    original = deepcopy(network)
    if failure == "disk":
        monkeypatch.setattr(gd_dns.gd_state, "write_json_atomic", lambda *_args: False)
    elif failure == "family read":
        monkeypatch.setattr(gd_dns, "_read_family", lambda *_args: None)
    elif failure == "doh read":
        monkeypatch.setattr(gd_dns, "_read_doh", Mock(side_effect=OSError("unreadable")))
    else:
        gd_dns.state_path().write_text("{broken", encoding="utf-8")
    assert not gd_dns.apply_provider(["1.1.1.1", "2001:4860::1"], template="https://test.example/dns-query")["ok"]
    assert network == original
    if failure == "corrupt backup":
        assert gd_dns.state_path().read_text(encoding="utf-8") == "{broken"


@pytest.mark.parametrize("failure", ["IPv6", "DoH", "flush"])
def test_failed_apply_restores_exact_previous_ipv4_ipv6_and_doh(network, monkeypatch, failure):
    original = deepcopy(network)
    write_family, write_doh = gd_dns._write_family, gd_dns._write_doh

    def write_ipv6_then_fail(index, version, state):
        write_family(index, version, state)
        if version == 6 and state["servers"] == ["2001:4860::1"]:
            raise OSError("IPv6 failed")

    def write_doh_then_fail(changes, current):
        write_doh(changes, current)
        if changes["1.1.1.1"]["upgrade"]:
            raise OSError("DoH failed")

    if failure == "IPv6":
        monkeypatch.setattr(gd_dns, "_write_family", write_ipv6_then_fail)
    elif failure == "DoH":
        monkeypatch.setattr(gd_dns, "_write_doh", write_doh_then_fail)
    else:
        monkeypatch.setattr(gd_dns, "flush_native", Mock(side_effect=[(False, "flush failed"), (True, "")]))
    resp = gd_dns.apply_provider(["1.1.1.1", "2001:4860::1"], template="https://test.example/dns-query")
    assert not resp["ok"] and not resp["partial"]
    assert network == original
    assert not gd_dns.restore_backups()["changed"]


def test_doh_originals_remain_after_apply_and_retry_on_restore_failure(network, monkeypatch):
    original = deepcopy(network)
    for server in ["1.1.1.1", "1.0.0.1"]:
        assert gd_dns.apply_provider([server], template="https://test.example/dns-query")["ok"]
    write_doh = gd_dns._write_doh
    monkeypatch.setattr(gd_dns, "_write_doh", Mock(side_effect=OSError("DoH denied")))
    result = gd_dns.restore_backups()
    assert not result["ok"] and "DoH denied" in result["error"]
    assert network["families"] == original["families"]
    assert gd_dns._load_backup()["adapters"] == {}
    monkeypatch.setattr(gd_dns, "_write_doh", write_doh)
    assert gd_dns.restore_backups()["ok"]
    assert network == original


@pytest.mark.parametrize("flush_ok", [True, False])
def test_original_backup_survives_failed_apply_and_failed_compensation(network, monkeypatch, flush_ok):
    originals = deepcopy(network)
    assert gd_dns.apply_provider(["1.1.1.1"], template="https://first.example/dns-query")["ok"]
    write_family = gd_dns._write_family

    def unavailable_ipv6(index, version, state):
        if version == 6:
            raise OSError("adapter temporarily unavailable")
        write_family(index, version, state)

    monkeypatch.setattr(gd_dns, "_write_family", unavailable_ipv6)
    result = gd_dns.apply_provider(["8.8.8.8", "2001:4860::1"], template="https://second.example/dns-query")
    assert not result["ok"] and result["partial"]
    monkeypatch.setattr(gd_dns, "flush_native", Mock(side_effect=[(flush_ok, "flush failed"), (True, "")]))
    result = gd_dns.restore_backups()
    assert not result["ok"]
    assert ("flush failed" in result["error"]) is not flush_ok
    backup = gd_dns._load_backup()["adapters"]
    assert list(backup[network["adapters"]["Ethernet"]]["families"]) == ["6"]
    manual_ipv4 = {"mode": "static", "servers": ["8.8.8.8"], "register": "primary"}
    network["families"]["11", 4] = manual_ipv4
    monkeypatch.setattr(gd_dns, "_write_family", write_family)
    assert gd_dns.restore_backups()["ok"]
    originals["families"]["11", 4] = manual_ipv4
    assert network == originals


def test_failed_first_apply_keeps_only_uncompensated_settings(network, monkeypatch):
    write_family = gd_dns._write_family

    def fail_ipv6(index, version, state):
        if version == 6:
            raise OSError("IPv6 denied")
        write_family(index, version, state)

    monkeypatch.setattr(gd_dns, "_write_family", fail_ipv6)
    result = gd_dns.apply_provider(["1.1.1.1", "2001:4860::1"], template="https://resolver.example/dns-query")
    assert not result["ok"] and result["partial"]
    backup = gd_dns._load_backup()
    assert backup["doh"] == {}
    assert list(backup["adapters"][network["adapters"]["Ethernet"]]["families"]) == ["6"]
    manual = {"mode": "static", "servers": ["8.8.8.8"], "register": "primary"}
    network["families"]["11", 4] = manual
    monkeypatch.setattr(gd_dns, "_write_family", write_family)
    assert gd_dns.restore_backups()["ok"]
    assert network["families"]["11", 4] == manual


def test_doh_restore_retries_only_unconfirmed_servers(network, monkeypatch):
    template = "https://application.example/dns-query"
    assert gd_dns.apply_provider(["1.1.1.1", "1.0.0.1"], template=template)["ok"]
    write_doh = gd_dns._write_doh

    def partially_restore(changes, current):
        write_doh({"1.1.1.1": changes["1.1.1.1"]}, current)
        raise OSError("secondary failed")

    monkeypatch.setattr(gd_dns, "_write_doh", partially_restore)
    assert not gd_dns.restore_backups()["ok"]
    assert gd_dns._load_backup()["doh"] == {"1.0.0.1": None}
    manual_doh = {"template": "https://manual.example/dns-query", "upgrade": True, "fallback": True}
    network["doh"]["1.1.1.1"] = manual_doh
    monkeypatch.setattr(gd_dns, "_write_doh", write_doh)
    assert gd_dns.restore_backups()["ok"]
    assert network["doh"] == {"1.1.1.1": manual_doh}


def test_doh_commands_update_register_and_remove_as_needed(monkeypatch):
    previous = {"template": "https://before.example/dns-query", "upgrade": False, "fallback": True}
    desired = {"template": "https://after.example/dns-query", "upgrade": True, "fallback": False}
    old_table = {"1.1.1.1": previous, "8.8.8.8": previous}
    new_table = {"1.1.1.1": desired, "1.0.0.1": desired}
    changes = {**new_table, "8.8.8.8": None}
    run = Mock(return_value=(0, ""))
    monkeypatch.setattr(gd_dns, "_run", run)
    reader = Mock(return_value=new_table)
    monkeypatch.setattr(gd_dns, "_read_doh", reader)
    gd_dns._write_doh(changes, old_table)
    reader.assert_called_once_with()
    run.assert_called_once()
    args, = run.call_args.args
    assert args[-2] == "-EncodedCommand"
    script = base64.b64decode(args[-1]).decode("utf-16-le")
    assert "$ErrorActionPreference = 'Stop'" in script
    assert "Set-DnsClientDohServerAddress -ServerAddress '1.1.1.1'" in script
    assert "Add-DnsClientDohServerAddress -ServerAddress '1.0.0.1'" in script
    assert "Remove-DnsClientDohServerAddress -ServerAddress '8.8.8.8'" in script
    assert "-AutoUpgrade $true -AllowFallbackToUdp $false" in script


def test_ipv4_query_verifies_writes_even_if_netsh_exit_code_is_zero(monkeypatch):
    monkeypatch.setattr(gd_dns, "_run", lambda *_args: (0, ""))
    monkeypatch.setattr(gd_dns, "_read_family", lambda *_args: {"mode": "static", "servers": ["8.8.8.8"]})
    with pytest.raises(OSError):
        gd_dns._write_family("11", 4, {"mode": "static", "servers": ["1.1.1.1"]})


def test_legacy_ipv4_snapshot_does_not_reset_ipv6(network):
    old = {"iface": "Ethernet", "mode": "dhcp", "servers": ["192.168.1.111"]}
    gd_dns.gd_state.write_json_atomic(gd_dns.state_path(), old)
    previous_ipv6 = network["families"]["11", 6]
    assert not gd_dns.restore_backups()["ok"]
    assert gd_dns.gd_state.read_json(gd_dns.state_path()) == old
    assert not gd_dns.apply_provider(["1.1.1.1"])["ok"]
    assert gd_dns.legacy_snapshot_aliases() == ["Ethernet"]
    assert gd_dns.restore_backups(["Ethernet"])["ok"]
    assert network["families"]["11", 4] == {"mode": "dhcp", "servers": old["servers"]}
    assert network["families"]["11", 6] == previous_ipv6


def test_helper_shutdown_waits_for_dns_mutation(network, monkeypatch):
    import gd_gpu_service as service

    entered, release, closing = Event(), Event(), Event()
    write_family = gd_dns._write_family

    def write(*args):
        entered.set()
        assert release.wait(5)
        write_family(*args)

    def close():
        closing.set()
        return service.handle({"cmd": "shutdown", "restore": False})

    monkeypatch.setattr(service, "_stopping", False)
    monkeypatch.setattr(service, "_stop_boost", lambda: (True, ""))
    monkeypatch.setattr(service, "_zapret_shutdown", lambda: (True, ""))
    monkeypatch.setattr(gd_dns, "_write_family", write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        mutation = pool.submit(service.handle, {"cmd": "dns_apply", "servers": ["1.1.1.1"]})
        try:
            assert entered.wait(2)
            shutdown = pool.submit(close)
            assert closing.wait(2)
            assert not shutdown.done()
        finally:
            release.set()
        assert mutation.result(timeout=2)["ok"]
        assert shutdown.result(timeout=2)["data"] == "bye"
    assert network["families"]["11", 4]["servers"] == ["1.1.1.1"]
    assert gd_dns._load_backup()["adapters"]
    assert not service.handle({"cmd": "dns_restore"})["ok"]
