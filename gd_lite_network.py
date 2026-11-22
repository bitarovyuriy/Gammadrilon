"""On-demand DNS and zapret service controls for Lite (no Qt)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import time

import gd_dns


def dns_status() -> str:
    iface = gd_dns.active_interface(force=True)
    if not iface:
        raise OSError("Сетевой адаптер не найден")
    state = gd_dns.current_state(iface)
    if state.get("mode") not in ("static", "dhcp"):
        raise OSError("Не удалось прочитать настройки DNS")
    mode = "автоматически (DHCP)" if state["mode"] == "dhcp" else "вручную"
    addresses = ", ".join(state.get("servers", [])) or "нет адресов"
    return f"DNS — {iface}: {mode}; {addresses}"


def dns_apply(provider: str) -> str:
    """Run only as the elevated worker after an explicit user request."""
    allowed = {"cloudflare", "google", "quad9", "adguard", "yandex"}
    if not isinstance(provider, str) or provider not in allowed | {"restore"}:
        raise ValueError("Неизвестный DNS-провайдер")
    if provider == "restore":
        result = gd_dns.restore_backups()
        if not result.get("ok"):
            detail = result.get("error") or "неизвестная ошибка"
            prefix = "DNS восстановлен частично" if result.get("changed") or result.get("restored") else "Не удалось восстановить DNS"
            raise OSError(f"{prefix}: {detail}")
        if not result.get("changed") and not result.get("restored"):
            return "Нет сохранённых настроек DNS для восстановления"
        return "Сохранённые настройки DNS восстановлены"

    resolver = next(r for r in gd_dns.RESOLVERS if r["key"] == provider)
    result = gd_dns.apply_provider(list(resolver["servers"]))
    if not result.get("ok"):
        detail = result.get("error") or "неизвестная ошибка"
        prefix = "Откат DNS выполнен частично" if result.get("partial") else "Не удалось изменить DNS"
        raise OSError(f"{prefix}: {detail}")
    return f"DNS обновлён: {resolver['label']}"


# Only these SCM rights and controls are used; the service name is fixed.
_QUERY = 0x0004
_START = 0x0010
_STOP = 0x0020
_STOPPED, _START_PENDING, _STOP_PENDING, _RUNNING = 1, 2, 3, 4
_WAIT_SECONDS = 15.0


class _ServiceStatus(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "service_type", "current_state", "controls_accepted", "win32_exit_code",
        "service_exit_code", "check_point", "wait_hint")]


class _Service:
    def __init__(self, access: int):
        self.access = access
        self.scm = None
        self.handle = None

    def __enter__(self):
        api = self.api = ctypes.WinDLL("advapi32", use_last_error=True)
        handle = wintypes.HANDLE
        api.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        api.OpenSCManagerW.restype = handle
        api.OpenServiceW.argtypes = [handle, wintypes.LPCWSTR, wintypes.DWORD]
        api.OpenServiceW.restype = handle
        api.CloseServiceHandle.argtypes = [handle]
        api.CloseServiceHandle.restype = wintypes.BOOL
        api.QueryServiceStatus.argtypes = [handle, ctypes.POINTER(_ServiceStatus)]
        api.QueryServiceStatus.restype = wintypes.BOOL
        api.StartServiceW.argtypes = [handle, wintypes.DWORD, ctypes.POINTER(wintypes.LPCWSTR)]
        api.StartServiceW.restype = wintypes.BOOL
        api.ControlService.argtypes = [handle, wintypes.DWORD, ctypes.POINTER(_ServiceStatus)]
        api.ControlService.restype = wintypes.BOOL
        self.scm = api.OpenSCManagerW(None, None, 0x0001)
        if not self.scm:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = api.OpenServiceW(self.scm, "zapret", self.access)
        if not self.handle:
            error = ctypes.get_last_error()
            if error != 1060:
                self.__exit__(None, None, None)
                raise ctypes.WinError(error)
        return self

    def __exit__(self, *_):
        if self.handle:
            self.api.CloseServiceHandle(self.handle)
            self.handle = None
        if self.scm:
            self.api.CloseServiceHandle(self.scm)
            self.scm = None

    def state(self) -> int | None:
        if not self.handle:
            return None
        status = _ServiceStatus()
        if not self.api.QueryServiceStatus(self.handle, ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())
        return status.current_state

    def start(self):
        if not self.api.StartServiceW(self.handle, 0, None):
            raise ctypes.WinError(ctypes.get_last_error())

    def stop(self):
        status = _ServiceStatus()
        if not self.api.ControlService(self.handle, 1, ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())


def _state_text(state: int | None) -> str:
    return {
        None: "Служба zapret не установлена",
        _STOPPED: "Служба zapret остановлена",
        _RUNNING: "Служба zapret работает",
        _START_PENDING: "Служба zapret: запуск",
        _STOP_PENDING: "Служба zapret: остановка",
        5: "Служба zapret: продолжение",
        6: "Служба zapret: приостановка",
        7: "Служба zapret приостановлена",
    }.get(state, f"Служба zapret: неизвестное состояние ({state})")


def zapret_status() -> str:
    with _Service(_QUERY) as service:
        return _state_text(service.state())


def zapret_apply(mode: str) -> str:
    """Start/stop the pre-existing service; do not install or configure it."""
    if mode not in ("on", "off"):
        raise ValueError("Неизвестное действие для zapret")
    target = _RUNNING if mode == "on" else _STOPPED
    with _Service(_QUERY | (_START if mode == "on" else _STOP)) as service:
        state = service.state()
        if state is None:
            raise OSError("Служба zapret не установлена")
        deadline = time.monotonic() + _WAIT_SECONDS
        # A prior request might still be in flight; wait until it settles first.
        while state in (_START_PENDING, _STOP_PENDING, 5, 6):
            if time.monotonic() >= deadline:
                raise TimeoutError("Не дождались завершения операции службы zapret")
            time.sleep(0.2)
            state = service.state()
        if state == target:
            return _state_text(state)
        if state not in (_RUNNING, _STOPPED):
            raise OSError(_state_text(state))
        if mode == "on":
            service.start()
        else:
            service.stop()
        while True:
            state = service.state()
            if state == target:
                return _state_text(state)
            if time.monotonic() >= deadline:
                raise TimeoutError("Windows не подтвердила изменение состояния службы zapret")
            time.sleep(0.2)
