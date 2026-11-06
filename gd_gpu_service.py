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
import math
from pathlib import Path
import sys
import threading
import time

from multiprocessing import AuthenticationError
from multiprocessing.connection import Listener

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gd_gpu  # noqa: E402
import gd_i18n as i18n  # noqa: E402
import gd_paths  # noqa: E402
import gd_proc  # noqa: E402
from gd_helper import recv_message, send_message  # noqa: E402

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
_boost = None


def _stop_boost():
    if _boost is not None:
        return _boost.stop()
    return True, ""


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

        data = json.loads(gd_paths.config_path().read_text(encoding="utf-8"))
        return bool((data.get("settings") or {}).get("gpu_keep_on_exit", True))
    except Exception:  # noqa: BLE001
        return True


def _set_keep_on_exit(on) -> None:
    global _keep_on_exit
    _keep_on_exit = bool(on)


def _load_kept_original() -> None:
    """Прочитать исходный лимит, оставленный прошлой сессией (если был)."""
    global _original, _applied, _keep_on_exit
    _set_keep_on_exit(_read_keep_setting())
    try:
        import json

        data = json.loads(_state_path().read_text(encoding="utf-8"))
        original = data.get("original") or {"pl": float(data["original_pl"])}
        pl = original.get("pl")
        if pl is not None and (not isinstance(pl, (int, float)) or not math.isfinite(pl) or not 0 < pl <= 2000):
            return
    except Exception:  # noqa: BLE001 — файла нет, битый JSON или не число
        return
    _original = original
    _applied = data.get("applied")
    # i18n: skip-start — строка лога компаньона (для разработчика)
    _log(f"состояние отката с прошлой сессии: {_original}")
    # i18n: skip-end


def _keep_original() -> None:
    """Запомнить исходный лимит на диск: лимит остался на карте после выхода."""
    if not _original:
        return
    try:
        import gd_state

        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        gd_state.write_json_atomic(path, {"original_pl": _original.get("pl"),
                                          "original": _original, "applied": _applied})
    except OSError:
        pass


def _clear_kept_original() -> None:
    """Лимит вернулся к исходному — помнить больше нечего."""
    try:
        _state_path().unlink(missing_ok=True)
    except OSError:
        pass


def _run_smi(args: list[str], timeout: float = 20.0) -> tuple[bool, str]:
    path = gd_gpu._smi_path()  # noqa: SLF001 — путь ищется в одном месте
    if path is None:
        return False, i18n.t("nvidia-smi не найден")
    try:
        proc = gd_proc.run([path, *args], text=True, timeout=timeout)
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


def _set_power(pl: float) -> tuple[bool, str]:
    """Лимит мощности: сначала NVML (без процесса), затем nvidia-smi."""
    try:
        import gd_nvml

        nv = gd_nvml.Nvml()
        if nv.available():
            ok, message = nv.set_power_limit(pl)
            nv.shutdown()
            if ok:
                return True, message
    except Exception:  # noqa: BLE001 — нет NVML или драйвер старый
        pass
    return _run_smi(["-pl", str(int(pl))])


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
        ok, out = _set_power(pl)
        if not ok:
            return False, i18n.t("лимит мощности: {v0}", v0=out)
        if _original is None:
            _original = before
        if _applied is None:
            _applied = {}
        _applied["pl"] = pl
        _keep_original()
        what.append(i18n.t("PL {v0} Вт", v0=int(pl)))
    if lock_clocks:
        lo, hi = int(lock_clocks[0]), int(lock_clocks[1])
        ok, out = _run_smi(["-lgc", f"{lo},{hi}"])
        if not ok:
            return False, i18n.t("частоты: {v0}", v0=out)
        if _original is None:
            _original = before
        if _applied is None:
            _applied = {}
        _applied["lock_clocks"] = lock_clocks
        what.append(i18n.t("клоки {v0}–{v1} МГц", v0=lo, v1=hi))

    # исходное состояние запоминаем только когда изменение действительно прошло
    if what and _original is None:
        _original = before
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
    if (_applied or {}).get("lock_clocks"):
        ok, out = _run_smi(["-rgc"])
        if not ok:
            errors.append(i18n.t("сброс частот: {v0}", v0=out))
        else:
            _applied["lock_clocks"] = None
    orig_pl = _original.get("pl")
    if orig_pl is not None:
        ok, out = _set_power(float(orig_pl))
        if not ok:
            errors.append(i18n.t("лимит мощности: {v0}", v0=out))
        else:
            _original["pl"] = None
    if errors:
        _keep_original()
        return False, "; ".join(errors)
    _original = None
    _applied = None
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


def _validate_command(obj: dict) -> None:
    commands = {"ping", "status", "apply", "keep", "restore", "shutdown", "purge_standby",
                "empty_working_sets", "sensors_start", "sensors_stop", "dns_apply", "doh", "dns_flush",
                "zapret_recon", "zapret_start", "zapret_stop", "zapret_adopt", "zapret_probe",
                "zapret_service", "zapret_update", "cs2_apply", "cs2_revert",
                "boost_start", "boost_stop", "boost_restore"}
    if not isinstance(obj, dict) or obj.get("cmd") not in commands:
        raise ValueError("unknown command")
    if obj["cmd"] in {"zapret_start", "zapret_recon", "zapret_probe"} and not obj.get("path"):
        raise ValueError("missing installation path")
    for key in ("path", "zip", "log"):
        if key in obj and (not isinstance(obj[key], str) or not Path(obj[key]).is_absolute()
                           or "\x00" in obj[key]):
            raise ValueError(f"invalid {key}")
    for name in [obj.get("strategy", ""), *(obj.get("files") or [])]:
        if not isinstance(name, str) or "/" in name or "\\" in name or ":" in name or name == "..":
            raise ValueError("invalid strategy")
    for key in ("pid", "parent", "limit"):
        if key in obj and (type(obj[key]) is not int or not 0 < obj[key] <= 2**32 - 1):
            raise ValueError(f"invalid {key}")
    for key in ("pl", "seconds"):
        value = obj.get(key)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)
                                  or not 0 < value <= (600 if key == "seconds" else 2000)):
            raise ValueError(f"invalid {key}")
    clocks = obj.get("lock_clocks")
    if clocks is not None and (not isinstance(clocks, list) or len(clocks) != 2
                               or any(type(v) is not int or not 0 < v <= 10000 for v in clocks)
                               or clocks[0] > clocks[1]):
        raise ValueError("invalid clocks")
    if obj["cmd"] == "cs2_apply":
        import gd_cs2

        if not isinstance(obj.get("items"), list) or any(i not in {s["id"] for s in gd_cs2.TWEAKS}
                                                        for i in obj["items"]):
            raise ValueError("invalid CS2 items")
        if obj.get("exe") and (not Path(obj["exe"]).is_absolute()
                                or Path(obj["exe"]).name.lower() != "cs2.exe"):
            raise ValueError("invalid cs2.exe path")
    for key in ("argv", "servers", "pids", "files"):
        if key in obj and (not isinstance(obj[key], list) or len(obj[key]) > 4096):
            raise ValueError(f"invalid {key}")
    if "argv" in obj and any(not isinstance(a, str) or "\x00" in a for a in obj["argv"]):
        raise ValueError("invalid argv")
    if obj["cmd"] == "zapret_service" and obj.get("op", "status") not in {
            "status", "install", "remove", "start", "stop", "restart"}:
        raise ValueError("invalid service operation")
    if obj["cmd"] == "zapret_update" and obj.get("op", "apply") not in {"apply", "rollback", "ipset", "hosts"}:
        raise ValueError("invalid update operation")


def handle(obj: dict) -> dict:
    """Обработать команду, гарантированно возвращая dict.

    Раньше исключение внутри операции (например, `ctypes` в purge_standby)
    убивало поток serve_client без ответа — UI ловил таймаут. Теперь любая
    ошибка превращается в честный ответ {"ok": False, ...}.
    """
    try:
        _validate_command(obj)
        return _handle(obj)
    except Exception as exc:  # noqa: BLE001
        _log(f"ошибка команды: {type(exc).__name__}: {exc}")  # i18n: skip (лог)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _handle(obj: dict) -> dict:
    global _boost
    cmd = obj.get("cmd")
    if cmd in {"boost_start", "boost_stop", "boost_restore"}:
        import gd_boost

        with _OP_LOCK:
            if _boost is None:
                _boost = gd_boost.GameBoost()
            if cmd == "boost_restore":
                _boost.restore_from_disk()
                ok, message = not _boost.active(), ""
            else:
                ok, message = _boost.start() if cmd == "boost_start" else _boost.stop()
        return {"ok": ok, "message": message, "data": _boost.status(), "error": None if ok else message}
    if cmd in {"cs2_apply", "cs2_revert"}:
        import gd_cs2

        with _OP_LOCK:
            report = (gd_cs2.revert() if cmd == "cs2_revert"
                      else gd_cs2.apply(obj["items"], obj.get("exe") or ""))
        return {"ok": report["ok"], "data": report,
                "error": "; ".join(report.get("errors") or [])}
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
        ok, message = True, "bye"
        with _OP_LOCK:
            boost_ok, boost_message = _stop_boost()
            _zapret_shutdown()               # обход DPI — к состоянию «до включения»
            # решение об откате принимает хелпер: клиентов несколько (вкладка GPU,
            # вкладка zapret, выход приложения), и они не должны перебивать друг
            # друга. force остаётся в протоколе, но окно его больше не выставляет:
            # «неподтверждённых» изменений нет с 12.10.2026 (таймер подтверждения
            # убран), каждое применение сразу становится состоянием карты.
            if obj.get("force") or (obj.get("restore", True) and not _keep_on_exit):
                ok, message = _restore()
            else:
                _keep_original()             # лимит остаётся: исходный — на диск
        return {"ok": ok and boost_ok, "data": "bye",
                "error": None if ok and boost_ok else "; ".join(filter(None, [message if not ok else "", boost_message]))}
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
    # -- датчики: запускаются из этого же привилегированного процесса --------
    # Отдельный runas в gd_sensors — второе окно UAC. Здесь прав уже достаточно,
    # поэтому датчики стартуют молча и умирают вместе с окном (--parent).
    if cmd == "sensors_start":
        import gd_sensors

        parent = int(obj.get("parent") or 0)
        if not parent:
            return {"ok": False, "error": i18n.t("не передан pid окна")}
        ok = gd_sensors.launch_elevated(parent)
        return {"ok": ok, "data": {"path": str(gd_sensors.sample_path())}}
    if cmd == "sensors_stop":
        import psutil

        stopped = 0
        for proc in psutil.process_iter(["name"]):
            try:
                if (proc.info.get("name") or "").lower() == "gammadrilonsensors.exe":
                    proc.terminate()
                    stopped += 1
            except psutil.Error:
                continue
        return {"ok": True, "data": stopped}
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
    # -- служба zapret и автообновление (ТЗ 8): без консоли service.bat --------
    if cmd == "zapret_probe":
        # перебор стратегий: каждая проба поднимает winws.exe на секунды.
        # Лок держим на весь перебор: две пробы разом подерутся за WinDivert
        files = obj.get("files") or []
        with _OP_LOCK:
            import gd_zapret

            results = gd_zapret.probe_round(
                obj.get("path") or "", files or None,
                seconds=float(obj.get("seconds") or 3.0),
                limit=int(obj.get("limit") or 0))
        passed = [item for item in results if item.get("ok")]
        return {"ok": True, "data": {"results": results, "passed": passed}}
    if cmd == "zapret_service":
        op = str(obj.get("op") or "status")
        with _OP_LOCK:
            import gd_zapret

            if op == "status":
                return {"ok": True, "data": gd_zapret.service_status()}
            if op == "install":
                ok, res = gd_zapret.service_install(
                    obj.get("path") or "", obj.get("strategy") or "",
                    obj.get("argv") or [], bool(obj.get("start", True)))
            elif op == "remove":
                ok, res = gd_zapret.service_remove()
            elif op == "start":
                ok, res = gd_zapret.service_start()
            elif op == "stop":
                ok, res = gd_zapret.service_stop()
            elif op == "restart":
                ok, res = gd_zapret.service_restart()
            else:
                return {"ok": False, "error": i18n.t("неизвестная операция службы: {v0}", v0=op)}
        return {"ok": ok, "data": res if ok else None, "error": None if ok else res}
    if cmd == "zapret_update":
        op = str(obj.get("op") or "apply")
        with _OP_LOCK:
            import gd_zapret_update as upd

            if op == "apply":
                res = upd.apply_verified_update(obj.get("path") or "", obj.get("zip") or "",
                                                obj.get("version") or "", obj.get("sha256") or "")
            elif op == "rollback":
                res = upd.rollback_update()
            elif op == "ipset":
                res = upd.update_ipset(obj.get("path") or "")
            elif op == "hosts":
                ok, tmp = upd.fetch_hosts()
                res = upd.apply_hosts(tmp) if ok else {"ok": False, "error": tmp}
            else:
                return {"ok": False, "error": i18n.t("неизвестная операция обновления: {v0}", v0=op)}
        return {"ok": bool(res.get("ok")), "data": res if res.get("ok") else None,
                "error": None if res.get("ok") else res.get("error")}
    return {"ok": False, "error": i18n.t("неизвестная команда: {v0}", v0=cmd)}


# -- сервер -----------------------------------------------------------------


def _client_is_owner(peer, port: int, ui_pid: int, created: float) -> bool:
    """PID берётся из таблицы TCP ОС, а не из доступного клиенту JSON."""
    import psutil

    try:
        if psutil.Process(ui_pid).create_time() != created:
            return False
        return any(c.pid == ui_pid and c.laddr == peer and c.raddr == ("127.0.0.1", port)
                   for c in psutil.net_connections(kind="tcp"))
    except (psutil.Error, OSError):
        return False


def serve_client(conn) -> None:
    try:
        while True:
            if not conn.poll(1.0):
                continue
            try:
                obj = recv_message(conn)
            except EOFError:
                break
            except (ValueError, UnicodeError):
                send_message(conn, {"ok": False, "error": i18n.t("ожидался объект")})
                continue
            try:
                send_message(conn, handle(obj))
            except (OSError, EOFError):
                break
            if obj.get("cmd") == "shutdown":
                os._exit(0)              # откат уже сделан внутри handle()
    except (OSError, EOFError):
        pass
    finally:
        conn.close()


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
                _stop_boost()
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
    if args.host != "127.0.0.1" or not args.ui_pid or not args.token:
        ap.error("loopback, token and UI PID are required")
    import psutil
    ui_created = psutil.Process(args.ui_pid).create_time()
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
        except (OSError, AuthenticationError):
            time.sleep(0.2)
            continue
        _log("клиент подключился")  # i18n: skip-end
        if not _client_is_owner(listener.last_accepted, args.port, args.ui_pid, ui_created):
            conn.close()
            continue
        threading.Thread(target=serve_client, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
