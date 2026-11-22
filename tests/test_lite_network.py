from pathlib import Path
import sys

import pytest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_dns_status_shows_active_interface_and_addresses(monkeypatch):
    import gd_lite_network as network

    monkeypatch.setattr(network.gd_dns, "active_interface", lambda force=False: "Ethernet")
    monkeypatch.setattr(network.gd_dns, "current_state", lambda iface: {
        "iface": iface, "mode": "static", "servers": ["1.1.1.1", "1.0.0.1"]
    })
    text = network.dns_status()
    assert "Ethernet" in text and "1.1.1.1" in text


@pytest.mark.parametrize("provider,servers", [
    ("cloudflare", ["1.1.1.1", "1.0.0.1"]),
    ("google", ["8.8.8.8", "8.8.4.4"]),
    ("quad9", ["9.9.9.9", "149.112.112.112"]),
    ("adguard", ["94.140.14.14", "94.140.15.15"]),
    ("yandex", ["77.88.8.8", "77.88.8.1"]),
])
def test_dns_apply_only_known_static_provider_without_doh(monkeypatch, provider, servers):
    import gd_lite_network as network
    apply = Mock(return_value={"ok": True, "data": "DNS обновлён"})
    monkeypatch.setattr(network.gd_dns, "apply_provider", apply)
    assert "DNS" in network.dns_apply(provider)
    apply.assert_called_once_with(servers)


def test_dns_apply_failure_mentions_partial_rollback(monkeypatch):
    import gd_lite_network as network
    monkeypatch.setattr(network.gd_dns, "apply_provider", lambda _: {
        "ok": False, "partial": True, "error": "отказ"
    })
    with pytest.raises(OSError, match="отказ") as error:
        network.dns_apply("google")
    assert "частич" in str(error.value).lower()


def test_dns_restore_reports_partial_failure(monkeypatch):
    import gd_lite_network as network
    monkeypatch.setattr(network.gd_dns, "restore_backups", lambda: {
        "ok": False, "changed": True, "restored": ["Ethernet"], "error": "Wi-Fi недоступен"
    })
    with pytest.raises(OSError, match="Wi-Fi недоступен") as error:
        network.dns_apply("restore")
    assert "частич" in str(error.value).lower()


def test_dns_restore_empty_backup_does_not_claim_restored(monkeypatch):
    import gd_lite_network as network
    monkeypatch.setattr(network.gd_dns, "restore_backups", lambda: {
        "ok": True, "changed": False, "restored": [], "error": None
    })
    assert "нет" in network.dns_apply("restore").lower()


@pytest.mark.parametrize("bad", ["custom", "GOOGLE", "", "google;cmd", None])
def test_dns_rejects_untrusted_provider_before_any_action(monkeypatch, bad):
    import gd_lite_network as network
    monkeypatch.setattr(network.gd_dns, "apply_provider", Mock(side_effect=AssertionError))
    monkeypatch.setattr(network.gd_dns, "restore_backups", Mock(side_effect=AssertionError))
    with pytest.raises(ValueError):
        network.dns_apply(bad)


class FakeService:
    def __init__(self, states, missing=False):
        self.states = iter(states)
        self.last = None
        self.missing = missing
        self.actions = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def state(self):
        if self.missing:
            return None
        self.last = next(self.states, self.last)
        return self.last

    def start(self):
        self.actions.append("start")

    def stop(self):
        self.actions.append("stop")


def fake_service(monkeypatch, states, missing=False):
    import gd_lite_network as network
    service = FakeService(states, missing)
    monkeypatch.setattr(network, "_Service", lambda access: service)
    return service


@pytest.mark.parametrize("state,word", [(1, "остановлена"), (4, "работает"), (2, "запуск"), (3, "останов")])
def test_zapret_status_reads_service_only(monkeypatch, state, word):
    import gd_lite_network as network
    service = fake_service(monkeypatch, [state])
    assert word in network.zapret_status().lower()
    assert service.actions == [] and service.closed


def test_zapret_not_installed_status_and_action(monkeypatch):
    import gd_lite_network as network
    fake_service(monkeypatch, [], missing=True)
    assert "не установлена" in network.zapret_status().lower()
    with pytest.raises(OSError, match="не установлена"):
        network.zapret_apply("on")


@pytest.mark.parametrize("mode,states,expected", [("on", [1, 2, 4], "start"), ("off", [4, 3, 1], "stop")])
def test_zapret_apply_waits_for_verified_state(monkeypatch, mode, states, expected):
    import gd_lite_network as network
    service = fake_service(monkeypatch, states)
    monkeypatch.setattr(network.time, "sleep", lambda _: None)
    assert "zapret" in network.zapret_apply(mode).lower()
    assert service.actions == [expected] and service.closed


def test_zapret_noop_does_not_issue_control(monkeypatch):
    import gd_lite_network as network
    service = fake_service(monkeypatch, [4])
    assert "работает" in network.zapret_apply("on").lower()
    assert service.actions == []


def test_zapret_timeout_is_failure(monkeypatch):
    import gd_lite_network as network
    service = fake_service(monkeypatch, [1, 2])
    monkeypatch.setattr(network.time, "sleep", lambda _: None)
    ticks = iter([0, 0, 100])
    monkeypatch.setattr(network.time, "monotonic", lambda: next(ticks, 100))
    with pytest.raises(TimeoutError):
        network.zapret_apply("on")
    assert service.actions == ["start"] and service.closed


def test_zapret_native_error_propagates(monkeypatch):
    import gd_lite_network as network
    service = fake_service(monkeypatch, [1])
    service.start = Mock(side_effect=PermissionError("отказано"))
    with pytest.raises(PermissionError, match="отказано"):
        network.zapret_apply("on")
    assert service.closed


@pytest.mark.parametrize("bad", ["install", "restart", "ON", "", "on & cmd", None])
def test_zapret_rejects_invalid_modes_without_scm(monkeypatch, bad):
    import gd_lite_network as network
    monkeypatch.setattr(network, "_Service", Mock(side_effect=AssertionError))
    with pytest.raises(ValueError):
        network.zapret_apply(bad)


@pytest.mark.parametrize("iface,state", [(None, {}), ("Wi-Fi", {"mode": None})])
def test_dns_unreadable_is_error(monkeypatch, iface, state):
    import gd_lite_network as network
    monkeypatch.setattr(network.gd_dns, "active_interface", lambda force=False: iface)
    monkeypatch.setattr(network.gd_dns, "current_state", lambda _: state)
    with pytest.raises(OSError):
        network.dns_status()


def test_zapret_waits_for_previous_transition_before_start(monkeypatch):
    import gd_lite_network as network
    service = fake_service(monkeypatch, [3, 1, 2, 4])
    monkeypatch.setattr(network.time, "sleep", lambda _: None)
    assert "работает" in network.zapret_apply("on").lower()
    assert service.actions == ["start"]


class FakeApi:
    def __init__(self):
        self.OpenSCManagerW = Mock(return_value=100)
        self.OpenServiceW = Mock(return_value=200)
        self.CloseServiceHandle = Mock(return_value=1)
        self.QueryServiceStatus = Mock(side_effect=self.query)
        self.StartServiceW = Mock(return_value=1)
        self.ControlService = Mock(return_value=1)

    @staticmethod
    def query(_handle, ptr):
        ptr._obj.current_state = 4
        return 1


def test_native_service_uses_fixed_name_minimal_rights_and_closes_handles(monkeypatch):
    import gd_lite_network as network
    api = FakeApi()
    monkeypatch.setattr(network.ctypes, "WinDLL", lambda *_a, **_k: api)
    assert "работает" in network.zapret_status().lower()
    api.OpenServiceW.assert_called_once_with(100, "zapret", network._QUERY)
    assert api.CloseServiceHandle.call_args_list == [((200,),), ((100,),)]
    api.StartServiceW.assert_not_called()
    api.ControlService.assert_not_called()


@pytest.mark.parametrize("code,missing", [(1060, True), (5, False)])
def test_native_service_distinguishes_missing_from_access_failure(monkeypatch, code, missing):
    import gd_lite_network as network
    api = FakeApi()
    api.OpenServiceW.return_value = 0
    monkeypatch.setattr(network.ctypes, "WinDLL", lambda *_a, **_k: api)
    monkeypatch.setattr(network.ctypes, "get_last_error", lambda: code)
    if missing:
        assert "не установлена" in network.zapret_status().lower()
    else:
        with pytest.raises(OSError):
            network.zapret_status()
    api.CloseServiceHandle.assert_called_once_with(100)


@pytest.mark.parametrize("mode,initial,desired,access", [
    ("on", 1, 4, 0x0014), ("off", 4, 1, 0x0024),
])
def test_native_control_only_starts_or_stops_fixed_service(monkeypatch, mode, initial, desired, access):
    import gd_lite_network as network
    api = FakeApi()
    states = iter([initial, desired])

    def query(_handle, ptr):
        ptr._obj.current_state = next(states)
        return 1

    api.QueryServiceStatus.side_effect = query
    monkeypatch.setattr(network.ctypes, "WinDLL", lambda *_a, **_k: api)
    network.zapret_apply(mode)
    api.OpenServiceW.assert_called_once_with(100, "zapret", access)
    if mode == "on":
        api.StartServiceW.assert_called_once_with(200, 0, None)
        api.ControlService.assert_not_called()
    else:
        api.StartServiceW.assert_not_called()
        assert api.ControlService.call_args.args[:2] == (200, 1)
    assert api.QueryServiceStatus.call_count == 2
    assert api.CloseServiceHandle.call_count == 2
