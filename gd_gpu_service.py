# -*- coding: utf-8 -*-
"""Elevated-компаньон управления GPU (NVIDIA).

Установка лимита мощности и частот требует прав администратора (драйвер
отвечает «Insufficient Permissions» обычному пользователю). Поэтому запись
выполняет этот процесс: он запускается один раз через UAC
(`Start-Process -Verb RunAs`) и далее обслуживает окно по именованному каналу.

Держит исходные значения и откатывает их:
  * при штатном выходе (команда shutdown),
  * при падении окна-владельца (watchdog по PID — как у CPU-компаньона).

Команды (версия протокола 1):
  {"v":1,"cmd":"ping"}
  {"v":1,"cmd":"status"}
  {"v":1,"cmd":"apply","pl":300,"lock_clocks":[900,1800]|null}
  {"v":1,"cmd":"restore"}
  {"v":1,"cmd":"shutdown","restore":true}
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import threading
import time

from multiprocessing.connection import Listener

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gd_gpu  # noqa: E402

_OP_LOCK = threading.Lock()
_original: dict | None = None
_applied: dict | None = None


_LOG_FILE: str | None = None


def _log(msg: str) -> None:
    line = f"[gpu-service] {msg}"
    print(line, flush=True)
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def _run_smi(args: list[str], timeout: float = 20.0) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [gd_gpu.SMI, *args],
            capture_output=True, text=True, timeout=timeout,
            creationflags=0x08000000,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    low = text.lower()
    if proc.returncode != 0 or any(w in low for w in (
        "insufficient permissions", "not supported", "terminating early",
        "does not have permission", "failed to set",
    )):
        return False, text or f"код {proc.returncode}"
    return True, text


# -- операции с GPU ---------------------------------------------------------


def _apply(pl: float | None, lock_clocks: list | None) -> tuple[bool, str]:
    global _original, _applied
    state = gd_gpu.read_state()
    if state is None:
        return False, "nvidia-smi недоступен"
    before = {"pl": state.get("pl"), "clocks_locked": False}

    what = []
    if pl is not None:
        ok, out = _run_smi(["-pl", str(int(pl))])
        if not ok:
            return False, f"лимит мощности: {out}"
        what.append(f"PL {int(pl)} Вт")
    if lock_clocks:
        lo, hi = int(lock_clocks[0]), int(lock_clocks[1])
        ok, out = _run_smi(["-lgc", f"{lo},{hi}"])
        if not ok:
            return False, f"частоты: {out}"
        what.append(f"клоки {lo}–{hi} МГц")

    # исходное состояние запоминаем только когда изменение действительно прошло
    if _original is None:
        _original = before
    _applied = {"pl": pl, "lock_clocks": lock_clocks}
    return True, "применено: " + ", ".join(what) if what else "нечего применять"


def _restore() -> tuple[bool, str]:
    """Вернуть исходный лимит мощности и снять блокировку частот."""
    global _original, _applied
    if _original is None:
        return True, "нечего откатывать"
    errors = []
    ok, out = _run_smi(["-rgc"])          # снять блокировку частот
    if not ok and "permission" not in out.lower():
        errors.append(f"сброс частот: {out}")
    orig_pl = _original.get("pl")
    if orig_pl is not None:
        ok, out = _run_smi(["-pl", str(int(orig_pl))])
        if not ok:
            errors.append(f"лимит мощности: {out}")
    _original = None
    _applied = None
    if errors:
        return False, "; ".join(errors)
    return True, "исходные значения возвращены"


def _status() -> dict:
    return {
        "state": gd_gpu.read_state(),
        "original": _original,
        "applied": _applied,
    }


# -- обработка команды ------------------------------------------------------


def handle(obj: dict) -> dict:
    cmd = obj.get("cmd")
    if cmd == "ping":
        return {"ok": True, "data": "pong"}
    if cmd == "status":
        return {"ok": True, "data": _status()}
    if cmd == "apply":
        with _OP_LOCK:
            ok, msg = _apply(obj.get("pl"), obj.get("lock_clocks"))
        return {"ok": ok, "data": _status(), "error": None if ok else msg}
    if cmd == "restore":
        with _OP_LOCK:
            ok, msg = _restore()
        return {"ok": ok, "data": _status(), "error": None if ok else msg}
    if cmd == "shutdown":
        with _OP_LOCK:
            if obj.get("restore", True):
                _restore()
        return {"ok": True, "data": "bye"}
    # -- утилиты (ТЗ 6.1/6.2): требуют прав администратора, поэтому живут в хелпере --
    if cmd == "purge_standby":
        with _OP_LOCK:
            import gd_memory

            sensors = gd_memory.MemorySensors()
            before = sensors.sample()
            ok, msg = gd_memory.purge_standby_native()
            after = sensors.sample()
            sensors.close()
        return {"ok": ok, "data": {"before": before, "after": after},
                "error": None if ok else msg}
    if cmd == "empty_working_sets":
        with _OP_LOCK:
            import gd_memory

            sensors = gd_memory.MemorySensors()
            before = sensors.sample()
            ok, msg, count = gd_memory.empty_working_sets_native(obj.get("pids") or [])
            after = sensors.sample()
            sensors.close()
        return {"ok": ok, "data": {"before": before, "after": after, "count": count},
                "error": None if ok else msg}
    if cmd == "dns_apply":
        with _OP_LOCK:
            import gd_dns

            ok, msg = gd_dns.apply_native(
                obj.get("mode") or "dhcp",
                obj.get("servers") or [],
                obj.get("iface") or gd_dns.active_interface(),
            )
        return {"ok": ok, "data": msg, "error": None if ok else msg}
    if cmd == "doh":
        with _OP_LOCK:
            import gd_dns

            ok, msg = gd_dns.doh_native(
                obj.get("servers") or [], obj.get("template"), bool(obj.get("enable"))
            )
        return {"ok": ok, "data": msg, "error": None if ok else msg}
    if cmd == "dns_flush":
        with _OP_LOCK:
            import gd_dns

            ok, msg = gd_dns.flush_native()
        return {"ok": ok, "data": msg, "error": None if ok else msg}
    return {"ok": False, "error": f"неизвестная команда: {cmd}"}


# -- сервер -----------------------------------------------------------------


def serve_client(conn) -> None:
    try:
        while True:
            if not conn.poll(1.0):
                continue
            try:
                obj = conn.recv()
            except EOFError:
                break
            if not isinstance(obj, dict):
                conn.send({"ok": False, "error": "ожидался объект"})
                continue
            resp = handle(obj)
            conn.send(resp)
            if obj.get("cmd") == "shutdown":
                os._exit(0)              # откат уже сделан внутри handle()
    except (OSError, EOFError):
        pass


def watchdog(ui_pid: int) -> None:
    """Если окно-владелец умерло — вернуть исходные значения и выйти."""
    SYNCHRONIZE = 0x00100000
    k32 = ctypes.windll.kernel32
    handle_ = k32.OpenProcess(SYNCHRONIZE, False, ui_pid)
    if not handle_:
        return
    while True:
        if k32.WaitForSingleObject(handle_, 2000) == 0:   # процесс завершился
            _log(f"окно (pid {ui_pid}) завершилось — откат")
            with _OP_LOCK:
                _restore()
            os._exit(0)


def main() -> int:
    global _LOG_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=gd_gpu.TCP_HOST)
    ap.add_argument("--port", type=int, default=gd_gpu.TCP_PORT)
    ap.add_argument("--token", default="")
    ap.add_argument("--ui-pid", type=int, default=0)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()
    _LOG_FILE = args.log

    if args.ui_pid:
        threading.Thread(target=watchdog, args=(args.ui_pid,), daemon=True).start()

    authkey = bytes.fromhex(args.token) if args.token else None
    listener = Listener(address=(args.host, args.port), family="AF_INET", authkey=authkey)
    _log(f"слушаю {args.host}:{args.port} (ui-pid {args.ui_pid}, токен {'да' if authkey else 'нет'})")

    while True:                          # выход — через os._exit (shutdown/watchdog)
        try:
            conn = listener.accept()
        except OSError:
            time.sleep(0.2)
            continue
        _log("клиент подключился")
        threading.Thread(target=serve_client, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
