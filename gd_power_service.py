# -*- coding: utf-8 -*-
"""Процесс-компаньон питания: pipe-сервер + watchdog отката.

Запускается UI-приложением (`gd_proc.popen` — без окна консоли). Через него идут все
операции с профилями питания; при смерти UI (краш) он откатывает схему — откат
«живёт после падения приложения».

Протокол: multiprocessing.connection по named pipe (AF_PIPE),
JSON-совместимые словари: {'cmd': ..., ...} -> {'ok': bool, 'data'|'error': ...}.

Команды: ping | status | apply {profile, custom} | restore | shutdown {restore}

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
import time

from multiprocessing.connection import Listener

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gd_i18n as i18n  # noqa: E402
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


def handle(cmd: dict) -> dict:
    name = cmd.get("cmd")
    if name == "ping":
        return {"ok": True, "data": "pong"}
    if name == "status":
        try:
            return {"ok": True, "data": gp.state_snapshot()}
        except OSError as e:
            return {"ok": False, "error": str(e)}
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

    # -- watchdog ----------------------------------------------------------
    def _watchdog(self):
        while not self.stop.wait(WATCHDOG_INTERVAL):
            if self.ui_pid and not is_process_alive(self.ui_pid):
                # UI умер, не попрощавшись: если висит применённый профиль — откат
                if gp.load_state():
                    _rollback(i18n.t("профиль остался после смерти окна"))
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
                            _rollback(i18n.t("окно закрывается — возвращаю схему"))
                    time.sleep(0.05)  # дать ответу уйти в pipe
                    os._exit(0)
        finally:
            conn.close()

    def run(self) -> int:
        # при старте: если остался «грязный» state от прошлой сессии — откат
        if gp.load_state():
            _rollback(i18n.t("профиль остался от прошлой сессии"))

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
    # i18n: skip (справка CLI — видна только разработчику)
    p.add_argument("--ui-pid", type=int, default=0, help="PID UI-процесса для watchdog")
    p.add_argument("--pipe", default=PIPE_NAME)
    args = p.parse_args()
    return PowerService(args.ui_pid, args.pipe).run()


if __name__ == "__main__":
    sys.exit(main())
