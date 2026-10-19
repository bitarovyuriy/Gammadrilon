# -*- coding: utf-8 -*-
"""Elevated-компаньон управления GPU (NVIDIA).

Установка лимита мощности и частот требует прав администратора (драйвер
отвечает «Insufficient Permissions» обычному пользователю). Поэтому запись
выполняет этот процесс: он запускается один раз через UAC
(`Start-Process -Verb RunAs`) и далее обслуживает окно по именованному каналу.

Держит исходные значения и откатывает их:
  * при штатном выходе (команда shutdown),
  * при падении окна-владельца (watchdog по PID — как у CPU-компаньона).

Если включена настройка «не возвращать лимит при выходе» (`keep_on_exit`), откат
не делается ни при выходе, ни при крахе: лимит остаётся в драйвере, а исходное
значение уходит в gpu_state.json — следующая сессия вернёт именно его.

Команды (версия протокола 1):
  {"v":1,"cmd":"ping"}
  {"v":1,"cmd":"status"}
  {"v":1,"cmd":"apply","pl":300,"lock_clocks":[900,1800]|null,"keep_on_exit":true}
  {"v":1,"cmd":"keep","on":true}
  {"v":1,"cmd":"restore"}
  {"v":1,"cmd":"shutdown","restore":true}
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time

from multiprocessing.connection import Listener

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gd_gpu  # noqa: E402
import gd_i18n as i18n  # noqa: E402
import gd_paths  # noqa: E402
import gd_proc  # noqa: E402

i18n.init_for_process()      # компаньон — отдельный процесс: язык берём из presets.json

_OP_LOCK = threading.Lock()
_original: dict | None = None
_applied: dict | None = None
# Настройка UI «не возвращать лимит при выходе приложения». Приходит с командой
# apply (и отдельной командой keep при переключении галочки) — решает, что делать
# при выходе и при смерти окна-владельца. Хелпер — единственный, кто живёт дольше
# окна, поэтому решение об откате принимает он, а не тот, кто успел крикнуть первым:
# вкладка zapret тоже шлёт shutdown(restore=True), и раньше она могла вернуть лимит
# вопреки настройке.
_keep_on_exit = False


_LOG_FILE: str | None = None
_LOG_MAX_BYTES = 512 * 1024      # лог хелпера не растёт бесконечно (см. _trim_log)


def _trim_log(path: str) -> None:
    """Оставить последние ~256 КБ лога.

    Файл открывается на дозапись и переживает сессии, а чистить его больше
    некому: у активного пользователя он копится месяцами. Проверка размера —
    один stat на строку лога (микросекунды), обрезка случается раз в ~4000 строк.
    """
    try:
        if os.path.getsize(path) <= _LOG_MAX_BYTES:
            return
        with open(path, "rb") as fh:
            fh.seek(-_LOG_MAX_BYTES // 2, os.SEEK_END)
            tail = fh.read()
        with open(path, "wb") as fh:
            fh.write(tail)
    except OSError:
        pass


def _log(msg: str) -> None:
    line = f"[gpu-service] {msg}"
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001
        pass          # в windowed-сборке stdout может отсутствовать
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            _trim_log(_LOG_FILE)
        except OSError:
            pass


def _state_path():
    """Файл «лимит оставлен»: исходное значение для кнопки «Вернуть как было».

    Лимит мощности живёт в драйвере и после выхода приложения, но исходное
    значение знал только этот процесс. Файл переживает перезапуск — следующая
    сессия всё ещё может вернуть исходный лимит, а не «текущий».
    """
    return gd_paths.data_dir() / "gpu_state.json"


def _read_keep_setting() -> bool:
    """Настройка «не возвращать лимит при выходе» из presets.json.

    Хелпер читает её сам (как gd_i18n.init_for_process читает язык): его мог
    поднять любой клиент — не только вкладка GPU, — а настройка общая для всего
    приложения. Нет файла или ключа — включена: так решил пользователь
    («по умолчанию — не откатывать»).
    """
    try:
        import json

        data = json.loads((gd_paths.app_dir() / "presets.json").read_text(encoding="utf-8"))
        return bool((data.get("settings") or {}).get("gpu_keep_on_exit", True))
    except Exception:  # noqa: BLE001
        return True


def _set_keep_on_exit(on) -> None:
    global _keep_on_exit
    _keep_on_exit = bool(on)


def _load_kept_original() -> None:
    """Прочитать исходный лимит, оставленный прошлой сессией (если был)."""
    global _original, _keep_on_exit
    _set_keep_on_exit(_read_keep_setting())
    try:
        import json

        data = json.loads(_state_path().read_text(encoding="utf-8"))
        pl = float(data["original_pl"])
    except Exception:  # noqa: BLE001 — файла нет, битый JSON или не число
        return
    _original = {"pl": pl, "clocks_locked": False}
    # i18n: skip-start — строка лога компаньона (для разработчика)
    _log(f"лимит с прошлой сессии: исходный {pl:.0f} Вт")
    # i18n: skip-end


def _keep_original() -> None:
    """Запомнить исходный лимит на диск: лимит остался на карте после выхода."""
    if not _original or _original.get("pl") is None:
        return
    try:
        import json

        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"original_pl": _original["pl"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _clear_kept_original() -> None:
    """Лимит вернулся к исходному — помнить больше нечего."""
    try:
        _state_path().unlink(missing_ok=True)
    except OSError:
        pass


def _run_smi(args: list[str], timeout: float = 20.0) -> tuple[bool, str]:
    try:
        proc = gd_proc.run([gd_gpu.SMI, *args], text=True, timeout=timeout)
    except (OSError, gd_proc.TimeoutExpired) as exc:
        return False, str(exc)
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    low = text.lower()
    if proc.returncode != 0 or any(w in low for w in (
        "insufficient permissions", "not supported", "terminating early",
        "does not have permission", "failed to set",
    )):
        return False, text or i18n.t("код {v0}", v0=proc.returncode)
    return True, text


# -- операции с GPU ---------------------------------------------------------


def _apply(pl: float | None, lock_clocks: list | None,
           keep_on_exit: bool | None = None) -> tuple[bool, str]:
    global _original, _applied
    if keep_on_exit is not None:
        _set_keep_on_exit(keep_on_exit)
    state = gd_gpu.read_state()
    if state is None:
        return False, i18n.t("nvidia-smi недоступен")
    before = {"pl": state.get("pl"), "clocks_locked": False}

    what = []
    if pl is not None:
        ok, out = _run_smi(["-pl", str(int(pl))])
        if not ok:
            return False, i18n.t("лимит мощности: {v0}", v0=out)
        what.append(i18n.t("PL {v0} Вт", v0=int(pl)))
    if lock_clocks:
        lo, hi = int(lock_clocks[0]), int(lock_clocks[1])
        ok, out = _run_smi(["-lgc", f"{lo},{hi}"])
        if not ok:
            return False, i18n.t("частоты: {v0}", v0=out)
        what.append(i18n.t("клоки {v0}–{v1} МГц", v0=lo, v1=hi))

    # исходное состояние запоминаем только когда изменение действительно прошло
    if _original is None:
        _original = before
    _applied = {"pl": pl, "lock_clocks": lock_clocks}
    if what and _keep_on_exit:
        # исходное значение — на диск сразу: лимит может пережить и крах окна,
        # и аварийное завершение хелпера, а следующая сессия всё равно должна
        # знать, к чему возвращаться по кнопке «Вернуть как было»
        _keep_original()
    if what:
        if _keep_on_exit:
            return True, i18n.t("применено: {v0} (лимит останется после выхода)",
                                v0=", ".join(what))
        return True, i18n.t("применено: {v0}", v0=", ".join(what))
    return True, i18n.t("нечего применять")


def _restore() -> tuple[bool, str]:
    """Вернуть исходный лимит мощности и снять блокировку частот."""
    global _original, _applied
    if _original is None:
        return True, i18n.t("нечего откатывать")
    errors = []
    ok, out = _run_smi(["-rgc"])          # снять блокировку частот
    if not ok and "permission" not in out.lower():
        errors.append(i18n.t("сброс частот: {v0}", v0=out))
    orig_pl = _original.get("pl")
    if orig_pl is not None:
        ok, out = _run_smi(["-pl", str(int(orig_pl))])
        if not ok:
            errors.append(i18n.t("лимит мощности: {v0}", v0=out))
    _original = None
    _applied = None
    if errors:
        return False, "; ".join(errors)
    _clear_kept_original()          # лимит вернулся — «исходный» больше не нужен
    return True, i18n.t("исходные значения возвращены")


def _status() -> dict:
    return {
        "state": gd_gpu.read_state(),
        "original": _original,
        "applied": _applied,
    }


# -- zapret: обход DPI (ТЗ 8) ------------------------------------------------
# Всё привилегированное: батники-стратегии и winws.exe грузят kernel-драйвер
# WinDivert, обычному пользователю это недоступно. Хелпер запоминает PID
# запущенного им winws, чтобы вернуть состояние при выходе (ТЗ 8, DoD).

_zapret_pid: int | None = None


def _zapret_start(path: str, argv: list, log: str, note: str):
    global _zapret_pid
    import gd_zapret

    ok, res = gd_zapret.start_winws(path, argv, log, note)
    if ok:
        _zapret_pid = int(res["pid"])
    return ok, res


def _zapret_stop(pid: int):
    global _zapret_pid
    import gd_zapret

    ok, msg = gd_zapret.stop_winws(pid)
    if ok and pid == _zapret_pid:
        _zapret_pid = None
    return ok, msg


def _zapret_shutdown() -> None:
    """При выходе хелпера — обход возвращается к состоянию «до включения».

    Вторая линия — job-объект kill-on-close в gd_zapret: он гасит winws даже
    при убийстве хелпера, когда до этой функции дело не доходит.
    """
    if _zapret_pid:
        _zapret_stop(_zapret_pid)


def _zapret_adopt(pid: int):
    """Прочитать argv чужого winws.exe (elevated: у пользователя нет прав на PEB).

    Убивать чужой процесс молча запрещено (ТЗ 8.4): это только чтение, чтобы
    UI мог предложить «взять под управление» или «оставить как есть».
    """
    import psutil

    try:
        proc = psutil.Process(int(pid))
        name = proc.name()
        if (name or "").lower() != "winws.exe":
            return False, i18n.t("PID {v0} — это {v1}, а не winws.exe", v0=pid, v1=name or '?')
        argv = proc.cmdline()
    except (psutil.Error, ValueError) as exc:
        return False, i18n.t("не удалось прочитать командную строку: {v0}", v0=exc)
    if argv and argv[0].lower().endswith("winws.exe"):
        argv = argv[1:]
    return True, {"argv": argv}


# -- обработка команды ------------------------------------------------------


def handle(obj: dict) -> dict:
    """Обработать команду, гарантированно возвращая dict.

    Раньше исключение внутри операции (например, `ctypes` в purge_standby)
    убивало поток serve_client без ответа — UI ловил таймаут. Теперь любая
    ошибка превращается в честный ответ {"ok": False, ...}.
    """
    try:
        return _handle(obj)
    except Exception as exc:  # noqa: BLE001
        _log(f"ошибка команды {obj.get('cmd')!r}: {type(exc).__name__}: {exc}")  # i18n: skip (лог)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _handle(obj: dict) -> dict:
    cmd = obj.get("cmd")
    if cmd == "ping":
        return {"ok": True, "data": "pong"}
    if cmd == "status":
        return {"ok": True, "data": _status()}
    if cmd == "apply":
        with _OP_LOCK:
            ok, msg = _apply(obj.get("pl"), obj.get("lock_clocks"),
                             obj.get("keep_on_exit"))
        return {"ok": ok, "data": _status(), "error": None if ok else msg}
    if cmd == "keep":
        # галочка «не возвращать лимит при выходе» переключена: решение об откате
        # принимает хелпер — сообщаем ему сразу, без повторного UAC-цикла
        _set_keep_on_exit(obj.get("on"))
        return {"ok": True, "data": _status()}
    if cmd == "restore":
        with _OP_LOCK:
            ok, msg = _restore()
        return {"ok": ok, "data": _status(), "error": None if ok else msg}
    if cmd == "shutdown":
        with _OP_LOCK:
            _zapret_shutdown()               # обход DPI — к состоянию «до включения»
            # решение об откате принимает хелпер: клиентов несколько (вкладка GPU,
            # вкладка zapret, выход приложения), и они не должны перебивать друг
            # друга. force остаётся в протоколе, но окно его больше не выставляет:
            # «неподтверждённых» изменений нет с 12.10.2026 (таймер подтверждения
            # убран), каждое применение сразу становится состоянием карты.
            if obj.get("force") or (obj.get("restore", True) and not _keep_on_exit):
                _restore()
            else:
                _keep_original()             # лимит остаётся: исходный — на диск
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
    # -- zapret (ТЗ 8): освоение и управление winws.exe ----------------------
    if cmd == "zapret_recon":
        with _OP_LOCK:
            import gd_zapret

            ok, res = gd_zapret.recon(obj.get("path") or "", obj.get("strategy") or "")
        return {"ok": ok, "data": res if ok else None, "error": None if ok else res}
    if cmd == "zapret_start":
        with _OP_LOCK:
            ok, res = _zapret_start(
                obj.get("path") or "", obj.get("argv") or [],
                obj.get("log") or "", obj.get("note") or "")
        return {"ok": ok, "data": res if ok else None, "error": None if ok else res}
    if cmd == "zapret_stop":
        with _OP_LOCK:
            ok, msg = _zapret_stop(int(obj.get("pid") or 0))
        return {"ok": ok, "data": msg, "error": None if ok else msg}
    if cmd == "zapret_adopt":
        with _OP_LOCK:
            ok, res = _zapret_adopt(int(obj.get("pid") or 0))
        return {"ok": ok, "data": res if ok else None, "error": None if ok else res}
    return {"ok": False, "error": i18n.t("неизвестная команда: {v0}", v0=cmd)}


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
                conn.send({"ok": False, "error": i18n.t("ожидался объект")})
                continue
            try:
                conn.send(handle(obj))
            except (OSError, EOFError):
                break
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
            with _OP_LOCK:
                _zapret_shutdown()           # обход DPI — к состоянию «до включения»
                if _keep_on_exit:
                    _keep_original()         # лимит остаётся: исходный — на диск
                else:
                    _restore()
            # i18n: skip-start — строка лога компаньона (для разработчика)
            _log(f"окно (pid {ui_pid}) завершилось — "
                 f"{'лимит оставлен' if _keep_on_exit else 'откат'}")
            # i18n: skip-end
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

    # лимит, оставленный прошлой сессией: помним исходное значение для отката
    _load_kept_original()

    if args.ui_pid:
        threading.Thread(target=watchdog, args=(args.ui_pid,), daemon=True).start()

    authkey = bytes.fromhex(args.token) if args.token else None
    listener = Listener(address=(args.host, args.port), family="AF_INET", authkey=authkey)
    # i18n: skip-start — строки журнала компаньона (лог-файл, разработчику)
    _log(f"слушаю {args.host}:{args.port} (ui-pid {args.ui_pid}, токен {'да' if authkey else 'нет'})")

    while True:                          # выход — через os._exit (shutdown/watchdog)
        try:
            conn = listener.accept()
        except OSError:
            time.sleep(0.2)
            continue
        _log("клиент подключился")  # i18n: skip-end
        threading.Thread(target=serve_client, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
