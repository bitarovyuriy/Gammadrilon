# -*- coding: utf-8 -*-
"""Регрессии разбора DNS: netsh ``show config`` врёт про DHCP."""
from __future__ import annotations

import sys
from pathlib import Path

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
    output = 'Настройка интерфейса "Ethernet"\n'
    monkeypatch.setattr(gd_dns, "_run", lambda args: (0, output))
    state = gd_dns.current_state("Ethernet")
    assert state == {"iface": "Ethernet", "mode": "dhcp", "servers": []}
