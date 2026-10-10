# -*- coding: utf-8 -*-
"""Процесс-компаньон питания: pipe-сервер + watchdog отката.

Запускается UI-приложением (subprocess.Popen). Через него идут все операции
с профилями питания; при смерти UI (краш) он откатывает схему — откат
«живёт после падения приложения».

Протокол: multiprocessing.connection по named pipe (AF_PIPE),
JSON-совместимые словари: {'cmd': ..., ...} -> {'ok': bool, 'data'|'error': ...}.

Команды: ping | status | apply {profile, custom} | restore | shutdown {restore}
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time

from multiprocessing.connection import Listener

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gd_power as gp  # noqa: E402

PIPE_NAME = r"\\.\pipe\GammadrilonPower"
WATCHDOG_INTERVAL = 2.0

# сериализация мутирующих операций (несколько клиентов одновременно)
_OP_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# watchdog: жив ли UI-процесс
# --------------------------------------------------------------------------

SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x00000102


def is_process_alive(pid: int) -> bool:
    h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == WAIT_TIMEOUT
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


# --------------------------------------------------------------------------
# обработчики команд
# --------------------------------------------------------------------------


def handle(cmd: dict) -> dict:
    name = cmd.get("cmd")
    if name == "ping":
        return {"ok": True, "data": "pong"}
    if name == "status":
        try:
            active = gp.get_active_scheme()
        except OSError as e:
            return {"ok": False, "error": str(e)}
        state = gp.load_state()
        data = {"active_scheme": active, "profile": None, "clone_scheme": None,
                "values": gp.read_values(active)}
        try:
            data["active_name"] = gp.scheme_name(active)
        except OSError:
            data["active_name"] = ""
        if state:
            data["profile"] = state.get("profile")
            data["clone_scheme"] = state.get("clone_scheme")
            data["original_scheme"] = state.get("original_scheme")
            data["applied_at"] = state.get("applied_at")
            try:
                data["original_values"] = gp.read_values(state["original_scheme"])
            except OSError:
                data["original_values"] = None
        return {"ok": True, "data": data}
    if name == "apply":
        with _OP_LOCK:
            try:
                st = gp.apply_profile(cmd.get("profile", "normal"), cmd.get("custom"))
                return {"ok": True, "data": {k: v for k, v in st.items() if k != "custom"}}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if name == "restore":
        with _OP_LOCK:
            try:
                return {"ok": True, "data": gp.restore()}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if name == "shutdown":
        return {"ok": True, "data": "bye"}
    return {"ok": False, "error": f"неизвестная команда: {name}"}


# --------------------------------------------------------------------------
# сервис
# --------------------------------------------------------------------------


class PowerService:
    def __init__(self, ui_pid: int, pipe_name: str):
        self.ui_pid = ui_pid
        self.pipe_name = pipe_name
        self.stop = threading.Event()

    # -- watchdog ----------------------------------------------------------
    def _watchdog(self):
        while not self.stop.wait(WATCHDOG_INTERVAL):
            if self.ui_pid and not is_process_alive(self.ui_pid):
                # UI умер, не попрощавшись: если висит применённый профиль — откат
                if gp.load_state():
                    try:
                        gp.restore()
                    except Exception:  # noqa: BLE001
                        pass
                self.stop.set()
                os._exit(0)  # не ждём accept — выходим сразу

    # -- pipe --------------------------------------------------------------
    def _serve_conn(self, conn):
        try:
            while True:
                try:
                    cmd = conn.recv()
                except (EOFError, OSError):
                    return
                resp = handle(cmd)
                try:
                    conn.send(resp)
                except OSError:
                    return
                if cmd.get("cmd") == "shutdown":
                    if cmd.get("restore", True) and gp.load_state():
                        with _OP_LOCK:
                            try:
                                gp.restore()
                            except Exception:  # noqa: BLE001
                                pass
                    time.sleep(0.05)  # дать ответу уйти в pipe
                    os._exit(0)
        finally:
            conn.close()

    def run(self) -> int:
        # при старте: если остался «грязный» state от прошлой сессии — откат
        if gp.load_state():
            try:
                gp.restore()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=self._watchdog, daemon=True).start()

        try:
            listener = Listener(address=self.pipe_name, family="AF_PIPE")
        except OSError:
            # pipe занят — другой экземпляр уже работает
            return 2

        try:
            while not self.stop.is_set():
                conn = listener.accept()
                # каждый клиент обслуживается своим потоком: несколько
                # потребителей (окно, тесты) не блокируют друг друга
                threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()
        finally:
            listener.close()
        return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Gammadrilon power companion")
    p.add_argument("--ui-pid", type=int, default=0, help="PID UI-процесса для watchdog")
    p.add_argument("--pipe", default=PIPE_NAME)
    args = p.parse_args()
    return PowerService(args.ui_pid, args.pipe).run()


if __name__ == "__main__":
    sys.exit(main())
