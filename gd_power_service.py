# -*- coding: utf-8 -*-
"""Процесс-компаньон питания: pipe-сервер + watchdog отката.

Запускается UI-приложением (`gd_proc.popen` — без окна консоли). Через него идут все
операции с профилями питания; при смерти UI (краш) он откатывает схему — откат
«живёт после падения приложения».

Протокол: multiprocessing.connection по named pipe (AF_PIPE),
JSON-совместимые словари: {'cmd': ..., ...} -> {'ok': bool, 'data'|'error': ...}.

Команды: ping | status | apply {profile, custom} | restore | clean_clones | shutdown {restore}

`status` — чистое чтение powercfg, и то же самое умеет `gd_power.state_snapshot()`
локально, без процесса. Поэтому UI читает состояние и без компаньона, а поднимает
его только на запись (`apply`/`restore`) — ради отката, если UI умрёт.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
from ctypes import wintypes
from multiprocessing.connection import Listener

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gd_i18n as i18n  # noqa: E402
import gd_helper  # noqa: E402
import gd_power as gp  # noqa: E402

i18n.init_for_process()      # компаньон — отдельный процесс: язык берём из presets.json

PIPE_NAME = r"\\.\pipe\GammadrilonPower"
WATCHDOG_INTERVAL = 2.0

# сериализация мутирующих операций (несколько клиентов одновременно)
_OP_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# watchdog: жив ли UI-процесс
# --------------------------------------------------------------------------

SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
ERROR_INVALID_PARAMETER = 87
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def is_process_alive(pid: int) -> bool:
    h = _kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return ctypes.get_last_error() != ERROR_INVALID_PARAMETER
    try:
        return _kernel32.WaitForSingleObject(h, 0) != WAIT_OBJECT_0
    finally:
        _kernel32.CloseHandle(h)


# --------------------------------------------------------------------------
# обработчики команд
# --------------------------------------------------------------------------


def _journal(why: str, note: str = "") -> None:
    """Записать провал отката в журнал изменений.

    У компаньона нет окна и тоста: если он не смог откатить схему после смерти UI,
    честно сказать об этом можно только в журнал (его читает пользователь) и
    оставленным файлом состояния — по нему откат повторится в следующей сессии.
    """
    try:
        import gd_journal

        gd_journal.record("cpu_profile", i18n.t("откат профиля питания"), None, None,
                          why=why if not note else f"{why}: {note}",
                          source=i18n.t("компаньон"), reversible=False)
    except Exception:  # noqa: BLE001
        pass


def _rollback(why: str) -> dict:
    """Откатить остаток профиля и назвать провал (см. ``gp.restore_report``)."""
    rep = gp.restore_report()
    if not rep["ok"]:
        _journal(why, rep["note"])
    return rep


def handle(cmd: dict, *, stopping: threading.Event | None = None) -> dict:
    name = cmd.get("cmd")
    if name == "ping":
        return {"ok": True, "data": "pong"}
    if name == "status":
        try:
            with _OP_LOCK:
                return {"ok": True, "data": gp.state_snapshot()}
        except OSError as e:
            return {"ok": False, "error": str(e)}
    if name in ("apply", "restore", "clean_clones"):
        with _OP_LOCK:
            if stopping is not None and stopping.is_set():
                return {"ok": False, "error": i18n.t("компаньон завершает работу")}
            try:
                if name == "apply":
                    st = gp.apply_profile(cmd.get("profile", "normal"), cmd.get("custom"))
                    return {"ok": True, "data": {k: v for k, v in st.items() if k != "custom"}}
                if name == "clean_clones":
                    return {"ok": True, "data": gp.cleanup_clones()}
                rep = _rollback(i18n.t("возврат исходной схемы по запросу окна"))
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
            if not rep["ok"]:
                return {"ok": False, "data": rep, "error": rep["note"]}
            return {"ok": True, "data": rep}
    if name == "shutdown":
        return {"ok": True, "data": "bye"}
    return {"ok": False, "error": i18n.t("неизвестная команда: {v0}", v0=name)}


# --------------------------------------------------------------------------
# сервис
# --------------------------------------------------------------------------


class PowerService:
    def __init__(self, ui_pid: int, pipe_name: str):
        self.ui_pid = ui_pid
        self.pipe_name = pipe_name
        self.stop = threading.Event()
        self._finish_lock = threading.Lock()

    # -- watchdog ----------------------------------------------------------
    def _watchdog(self):
        while not self.stop.wait(WATCHDOG_INTERVAL):
            if self.ui_pid and not is_process_alive(self.ui_pid):
                # UI умер, не попрощавшись: если висит применённый профиль — откат
                if self._finish(True, i18n.t("профиль остался после смерти окна")) is not None:
                    os._exit(0)  # не ждём accept — выходим сразу

    def _finish(self, restore: bool, reason: str) -> dict | None:
        with self._finish_lock:
            if self.stop.is_set():
                return None
            self.stop.set()
        with _OP_LOCK:
            if not restore:
                return {"ok": True, "data": "bye"}
            try:
                rep = _rollback(reason)
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                _journal(reason, error)
                return {"ok": False, "error": error}
            if not rep["ok"]:
                return {"ok": False, "data": rep, "error": rep["note"]}
            return {"ok": True, "data": rep}

    # -- pipe --------------------------------------------------------------
    def _serve_conn(self, conn):
        try:
            while True:
                try:
                    cmd = gd_helper.recv_message(conn)
                except (EOFError, OSError, ValueError):
                    # битое или слишком большое сообщение: соединению больше не верим
                    return
                if cmd.get("cmd") == "shutdown":
                    resp = self._finish(cmd.get("restore", True),
                                        i18n.t("окно закрывается — возвращаю схему"))
                    if resp is None:
                        return
                    try:
                        gd_helper.send_message(conn, resp)
                    except OSError:
                        pass
                    finally:
                        try:
                            conn.close()
                        finally:
                            os._exit(0)
                    return
                try:
                    gd_helper.send_message(conn, handle(cmd, stopping=self.stop))
                except OSError:
                    return
        finally:
            conn.close()

    def run(self) -> int:
        try:
            listener = Listener(address=self.pipe_name, family="AF_PIPE")
        except OSError:
            # pipe занят — другой экземпляр уже работает
            return 2

        try:
            # Откат выполняет только процесс, которому принадлежит pipe.
            state, problem = gp.read_state_report()
            if state or problem:
                reason = i18n.t("профиль остался от прошлой сессии")
                try:
                    _rollback(reason)
                except Exception as exc:  # noqa: BLE001 — remain available for recovery
                    _journal(reason, f"{type(exc).__name__}: {exc}")
            threading.Thread(target=self._watchdog, daemon=True).start()
            # Поток завершения отправит ответ и остановит процесс.
            while True:
                conn = listener.accept()
                # каждый клиент обслуживается своим потоком: несколько
                # потребителей (окно, тесты) не блокируют друг друга
                threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()
        finally:
            listener.close()
        return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Gammadrilon power companion")
    # i18n: skip (справка CLI — видна только разработчику)
    p.add_argument("--ui-pid", type=int, default=0, help="PID UI-процесса для watchdog")
    p.add_argument("--pipe", default=PIPE_NAME)
    args = p.parse_args()
    return PowerService(args.ui_pid, args.pipe).run()


if __name__ == "__main__":
    sys.exit(main())
