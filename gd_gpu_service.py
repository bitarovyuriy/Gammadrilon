# -*- coding: utf-8 -*-
"""Elevated-компаньон управления GPU (NVIDIA).

Установка лимита мощности и частот требует прав администратора (драйвер
отвечает «Insufficient Permissions» обычному пользователю). Поэтому запись
выполняет этот процесс: он запускается один раз через UAC
(`Start-Process -Verb RunAs`) и далее обслуживает окно по TCP loopback.

Держит исходные значения и откатывает их:
  * при штатном выходе (команда shutdown),
  * при падении окна-владельца (watchdog по PID — как у CPU-компаньона).

Если включена настройка «не возвращать лимит при выходе» (`keep_on_exit`), откат
не делается ни при выходе, ни при крахе: лимит остаётся в драйвере. Снимок в
gpu_state.json записывается до изменения, чтобы его можно было вернуть.

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
from ctypes import wintypes
import json
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
import gd_state  # noqa: E402
from gd_helper import recv_message, send_message  # noqa: E402

i18n.init_for_process()      # компаньон — отдельный процесс: язык берём из presets.json

_OP_LOCK = threading.Lock()
_stopping = False
_original: dict | None = None
_applied: dict | None = None
_snapshot_problem = False
# Настройка UI «не возвращать лимит при выходе приложения». Приходит с командой
# apply (и отдельной командой keep при переключении галочки) — решает, что делать
# при выходе и при смерти окна-владельца. Хелпер — единственный, кто живёт дольше
# окна. При смерти окна хелпер использует последнее полученное значение.
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


def _read_keep_setting(default: bool = True) -> bool:
    """Настройка «не возвращать лимит при выходе» из presets.json.

    Хелпер читает её сам (как gd_i18n.init_for_process читает язык): его мог
    поднять любой клиент — не только вкладка GPU, — а настройка общая для всего
    приложения. Нет файла или ключа — включена: так решил пользователь
    («по умолчанию — не откатывать»).
    """
    data = gd_state.read_json(gd_paths.config_path())
    settings = data.get("settings") if isinstance(data, dict) else None
    value = settings.get("gpu_keep_on_exit") if isinstance(settings, dict) else None
    return value if type(value) is bool else default


def _set_keep_on_exit(on) -> None:
    global _keep_on_exit
    _keep_on_exit = bool(on)


def _load_kept_original() -> None:
    """Прочитать снимок прошлой сессии (включая старый формат)."""
    global _original, _applied, _snapshot_problem
    _original, _applied, _snapshot_problem = None, None, False
    _set_keep_on_exit(_read_keep_setting())
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid GPU snapshot")
        original = data["original"] if "original" in data else {"pl": float(data["original_pl"])}
        if not isinstance(original, dict):
            raise ValueError("invalid original GPU state")
        pl = original.get("pl")
        uuid = original.get("uuid")
        applied = data.get("applied")
        if applied is None:
            applied = {}
        if (pl is not None and not _valid_power(pl)) or (
                uuid is not None and not gd_gpu.valid_uuid(uuid)) or not isinstance(applied, dict):
            raise ValueError("invalid GPU snapshot")
        if not _valid_clocks(applied.get("lock_clocks")):
            raise ValueError("invalid clock snapshot")
        if pl is None and applied.get("pl") is not None:
            raise ValueError("missing original power limit")
    except FileNotFoundError:
        return
    except (KeyError, OSError, OverflowError, TypeError, ValueError):
        _snapshot_problem = True
        return
    if pl is None and not applied.get("lock_clocks"):
        return
    _original = original
    _applied = applied
    # i18n: skip-start — строка лога компаньона (для разработчика)
    _log(f"состояние отката с прошлой сессии: {_original}")
    # i18n: skip-end


def _save_snapshot(original: dict, applied: dict) -> bool:
    return gd_state.write_json_atomic(_state_path(), {"original_pl": original.get("pl"),
                                                     "original": original, "applied": applied})


def _clear_snapshot() -> bool:
    try:
        _state_path().unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _valid_power(value) -> bool:
    return type(value) in (int, float) and 0 < value <= 2000


def _valid_clocks(value) -> bool:
    return value is None or (isinstance(value, list) and len(value) == 2
                             and all(type(v) is int and 0 < v <= 10000 for v in value)
                             and value[0] <= value[1])


class _GpuWriteNotStarted(Exception):
    """The command was never sent to the driver."""


def _run_smi(gpu_uuid: str, args: list[str], timeout: float = 20.0) -> tuple[bool, str]:
    path = gd_gpu._smi_path()  # noqa: SLF001 — путь ищется в одном месте
    if path is None:
        raise _GpuWriteNotStarted(i18n.t("nvidia-smi не найден"))
    try:
        proc = gd_proc.run([path, "-i", gpu_uuid, *args], text=True, timeout=timeout)
    except OSError as exc:
        return False, str(exc)
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    low = text.lower()
    if proc.returncode != 0 or any(w in low for w in (
        "insufficient permissions", "not supported", "terminating early",
        "does not have permission", "failed to set",
    )):
        return False, text or i18n.t("код {v0}", v0=proc.returncode)
    return True, text


def _set_power(gpu_uuid: str, pl: float) -> tuple[bool, str]:
    """Лимит мощности: сначала NVML (без процесса), затем nvidia-smi."""
    try:
        import gd_nvml

        nv = gd_nvml.Nvml(gpu_uuid)
    except Exception:  # noqa: BLE001 — нет NVML или драйвер старый
        nv = None
    nvml_error = None
    if nv is not None:
        try:
            if nv.available():
                ok, nvml_error = nv.set_power_limit(pl)
                if ok:
                    return True, nvml_error
        finally:
            try:
                nv.shutdown()
            except Exception as exc:  # noqa: BLE001 — cleanup cannot change the write result
                _log(f"NVML shutdown: {exc}")  # i18n: skip (лог)
    try:
        return _run_smi(gpu_uuid, ["-pl", f"{pl:.3f}".rstrip("0").rstrip(".")])
    except _GpuWriteNotStarted:
        if nvml_error is not None:
            return False, nvml_error
        raise


# -- операции с GPU ---------------------------------------------------------


def _apply(pl: float | None, lock_clocks: list | None,
           keep_on_exit: bool | None = None) -> tuple[bool | None, str]:
    if keep_on_exit is not None:
        _set_keep_on_exit(keep_on_exit)
    if pl is None and not lock_clocks:
        return True, i18n.t("нечего применять")
    if problem := _snapshot_error():
        return False, problem
    state = gd_gpu.read_state()
    if state is None:
        return False, i18n.t("nvidia-smi недоступен")
    uuid = state.get("uuid")
    if not gd_gpu.valid_uuid(uuid):
        return False, i18n.t("Не удалось определить видеокарту для изменения.")
    if _original and _original["uuid"].casefold() != uuid.casefold():
        return False, i18n.t("Сначала восстановите настройки видеокарты из сохранённого снимка.")
    if pl is not None and not _valid_power(state.get("pl")):
        return False, i18n.t("Драйвер не сообщил исходный лимит мощности. Изменение отменено.")

    what = []
    if pl is not None:
        original = dict(_original or {}, uuid=uuid)
        if original.get("pl") is None:
            original["pl"] = state["pl"]
        applied = dict(_applied or {}, pl=pl)
        ok, out = _apply_with_snapshot(original, applied, lambda: _set_power(uuid, pl))
        if not ok:
            return ok, i18n.t("лимит мощности: {v0}", v0=out)
        what.append(i18n.t("PL {v0} Вт", v0=int(pl)))
    if lock_clocks:
        lo, hi = lock_clocks
        original = dict(_original or {"pl": None}, uuid=uuid)
        applied = dict(_applied or {}, lock_clocks=lock_clocks)
        ok, out = _apply_with_snapshot(original, applied, lambda: _run_smi(uuid, ["-lgc", f"{lo},{hi}"]))
        if not ok:
            return ok, i18n.t("частоты: {v0}", v0=out)
        what.append(i18n.t("клоки {v0}–{v1} МГц", v0=lo, v1=hi))

    if _keep_on_exit:
        return True, i18n.t("применено: {v0} (лимит останется после выхода)", v0=", ".join(what))
    return True, i18n.t("применено: {v0}", v0=", ".join(what))


def _snapshot_error() -> str:
    if _snapshot_problem or (_original and not _original.get("uuid")):
        return i18n.t("Не удалось связать снимок GPU с конкретной картой. "
                      "Проверьте исходный лимит, переместите файл {v0} в резервную папку "
                      "и перезапустите приложение. Инструкция есть в README.", v0=_state_path())
    return ""


def _record_snapshot(original: dict | None, applied: dict | None) -> bool:
    global _original, _applied
    pending = (original or {}).get("pl") is not None or bool((applied or {}).get("lock_clocks"))
    # Write an empty snapshot before removing it: even when unlink fails, a
    # restarted helper will not retry an already completed rollback.
    if not _save_snapshot(original if pending else {}, applied if pending else {}):
        return False
    _original = original if pending else None
    _applied = applied if pending else None
    if not pending:
        _clear_snapshot()
    return True


def _apply_with_snapshot(original, applied, write_device) -> tuple[bool | None, str]:
    previous = _original, _applied
    if not _record_snapshot(original, applied):
        return False, i18n.t("Не удалось сохранить снимок для отката GPU.")
    try:
        ok, out = write_device()
    except _GpuWriteNotStarted as exc:
        if not _record_snapshot(*previous):
            return False, f"{exc}. {i18n.t('Не удалось сохранить снимок для отката GPU.')}"
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 — the driver may have changed the GPU
        ok, out = False, str(exc) or type(exc).__name__
    if ok:
        return True, out
    # Even a reported error can follow a successful write. Retain the snapshot
    # unless the command is known to have stopped before reaching the driver.
    return None, i18n.t("Результат изменения GPU неизвестен. Снимок для отката сохранён. {v0}", v0=out)


def _restore() -> tuple[bool, str]:
    """Вернуть исходный лимит мощности и снять блокировку частот."""
    global _original, _applied
    if problem := _snapshot_error():
        return False, problem
    if _original is None:
        return True, i18n.t("нечего откатывать")
    uuid = _original["uuid"]
    errors = []
    original, applied = dict(_original), dict(_applied or {})
    if applied.get("lock_clocks"):
        ok, out = _attempt_rollback(lambda: _run_smi(uuid, ["-rgc"]))
        if not ok:
            errors.append(i18n.t("сброс частот: {v0}", v0=out))
        else:
            applied.pop("lock_clocks")
    orig_pl = original.get("pl")
    if orig_pl is not None:
        ok, out = _attempt_rollback(lambda: _set_power(uuid, orig_pl))
        if not ok:
            errors.append(i18n.t("лимит мощности: {v0}", v0=out))
        else:
            original["pl"] = None
            applied.pop("pl", None)
    if not _record_snapshot(original, applied):
        # Preserve progress in this process even if we could not update disk.
        _original, _applied = original, applied
        errors.append(i18n.t("Не удалось сохранить снимок для отката GPU."))
    if errors:
        return False, "; ".join(errors)
    return True, i18n.t("исходные значения возвращены")


def _attempt_rollback(rollback) -> tuple[bool, str]:
    try:
        return rollback()
    except Exception as exc:  # noqa: BLE001 — other rollback steps must run
        return False, str(exc) or type(exc).__name__


def _status() -> dict:
    return {
        "state": gd_gpu.read_state(),
        **_snapshot_status(),
    }


def _snapshot_status() -> dict:
    return {
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


def _zapret_shutdown() -> tuple[bool, str]:
    """При выходе хелпера — обход возвращается к состоянию «до включения».

    Вторая линия — job-объект kill-on-close в gd_zapret: он гасит winws даже
    при убийстве хелпера, когда до этой функции дело не доходит.
    """
    if _zapret_pid:
        return _zapret_stop(_zapret_pid)
    return True, ""


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
                "empty_working_sets", "sensors_start", "sensors_stop", "dns_apply", "dns_restore", "dns_flush",
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
    if not _valid_clocks(obj.get("lock_clocks")):
        raise ValueError("invalid clocks")
    for key in ("keep_on_exit", "on", "restore", "force"):
        if key in obj and type(obj[key]) is not bool:
            raise ValueError(f"invalid {key}")
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
    if obj["cmd"] == "zapret_update" and obj.get("op", "apply") not in {"apply", "rollback", "ipset"}:
        raise ValueError("invalid update operation")


def handle(obj: dict) -> dict:
    """Обработать команду, гарантированно возвращая dict.

    Раньше исключение внутри операции (например, `ctypes` в purge_standby)
    убивало поток serve_client без ответа — UI ловил таймаут. Теперь любая
    ошибка превращается в честный ответ {"ok": False, ...}.
    """
    try:
        _validate_command(obj)
        if obj["cmd"] == "ping":
            return {"ok": True, "data": "pong"}
        if obj["cmd"] == "shutdown":
            return _shutdown(obj) or _stopping_reply()
        with _OP_LOCK:
            return _stopping_reply() if _stopping else _handle(obj)
    except Exception as exc:  # noqa: BLE001
        _log(f"ошибка команды: {type(exc).__name__}: {exc}")  # i18n: skip (лог)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _stopping_reply() -> dict:
    return {"ok": False, "error": i18n.t("компаньон завершает работу")}


def _shutdown(obj: dict) -> dict | None:
    """The first shutdown caller owns cleanup and the subsequent process exit."""
    global _stopping
    with _OP_LOCK:
        if _stopping:
            return None
        _stopping = True
        operations = [_stop_boost, _zapret_shutdown]
        if obj.get("force") or obj.get("restore", not _keep_on_exit):
            operations.append(_restore)
        errors = []
        for operation in operations:
            ok, message = _attempt_rollback(operation)
            if not ok:
                errors.append(message or i18n.t("не удалось выполнить откат"))
        return {"ok": not errors, "data": "bye", "error": "; ".join(errors) or None}


def _handle(obj: dict) -> dict:
    global _boost
    cmd = obj.get("cmd")
    if cmd in {"boost_start", "boost_stop", "boost_restore"}:
        import gd_boost

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

        report = (gd_cs2.revert() if cmd == "cs2_revert"
                  else gd_cs2.apply(obj["items"], obj.get("exe") or ""))
        return {"ok": report["ok"], "data": report,
                "error": "; ".join(report.get("errors") or [])}
    if cmd == "status":
        return {"ok": True, "data": _status()}
    if cmd == "apply":
        ok, msg = _apply(obj.get("pl"), obj.get("lock_clocks"),
                         obj.get("keep_on_exit"))
        return {"ok": ok, "data": _snapshot_status(), "error": None if ok else msg}
    if cmd == "keep":
        # галочка «не возвращать лимит при выходе» переключена: решение об откате
        # принимает хелпер — сообщаем ему сразу, без повторного UAC-цикла
        _set_keep_on_exit(obj.get("on"))
        return {"ok": True, "data": _snapshot_status()}
    if cmd == "restore":
        ok, msg = _restore()
        return {"ok": ok, "data": _snapshot_status(), "error": None if ok else msg}
    # -- утилиты (ТЗ 6.1/6.2): требуют прав администратора, поэтому живут в хелпере --
    if cmd == "purge_standby":
        import gd_memory

        sensors = gd_memory.MemorySensors()
        try:
            before = sensors.sample()
            ok, msg = gd_memory.purge_standby_native()
            after = sensors.sample()
        finally:
            sensors.close()
        return {"ok": ok, "data": {"before": before, "after": after},
                "error": None if ok else msg}
    if cmd == "empty_working_sets":
        import gd_memory

        sensors = gd_memory.MemorySensors()
        try:
            before = sensors.sample()
            ok, msg, count = gd_memory.empty_working_sets_native(obj.get("pids") or [])
            after = sensors.sample()
        finally:
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
    if cmd in ("dns_apply", "dns_restore"):
        import gd_dns

        if cmd == "dns_restore":
            aliases = obj.get("confirmed_aliases", [])
            if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
                raise ValueError("invalid DNS aliases")
            return gd_dns.restore_backups(aliases)
        return gd_dns.apply_provider(obj.get("servers", []), template=obj.get("template"),
                                     mode=obj.get("mode", "static"), iface=obj.get("iface"))
    if cmd == "dns_flush":
        import gd_dns

        ok, msg = gd_dns.flush_native()
        return {"ok": ok, "data": msg, "error": None if ok else msg}
    # -- zapret (ТЗ 8): освоение и управление winws.exe ----------------------
    if cmd == "zapret_recon":
        import gd_zapret

        ok, res = gd_zapret.recon(obj.get("path") or "", obj.get("strategy") or "")
        return {"ok": ok, "data": res if ok else None, "error": None if ok else res}
    if cmd == "zapret_start":
        ok, res = _zapret_start(
            obj.get("path") or "", obj.get("argv") or [],
            obj.get("log") or "", obj.get("note") or "")
        return {"ok": ok, "data": res if ok else None, "error": None if ok else res}
    if cmd == "zapret_stop":
        ok, msg = _zapret_stop(int(obj.get("pid") or 0))
        return {"ok": ok, "data": msg, "error": None if ok else msg}
    if cmd == "zapret_adopt":
        ok, res = _zapret_adopt(int(obj.get("pid") or 0))
        return {"ok": ok, "data": res if ok else None, "error": None if ok else res}
    # -- служба zapret и автообновление (ТЗ 8): без консоли service.bat --------
    if cmd == "zapret_probe":
        # перебор стратегий: каждая проба поднимает winws.exe на секунды.
        # Лок держим на весь перебор: две пробы разом подерутся за WinDivert
        files = obj.get("files") or []
        import gd_zapret

        results = gd_zapret.probe_round(
            obj.get("path") or "", files or None,
            seconds=float(obj.get("seconds") or 3.0),
            limit=int(obj.get("limit") or 0))
        passed = [item for item in results if item.get("ok")]
        return {"ok": True, "data": {"results": results, "passed": passed}}
    if cmd == "zapret_service":
        op = str(obj.get("op") or "status")
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
        import gd_zapret_update as upd

        if op == "apply":
            res = upd.apply_verified_update(obj.get("path") or "", obj.get("zip") or "",
                                            obj.get("version") or "", obj.get("sha256") or "")
        elif op == "rollback":
            res = upd.rollback_update()
        elif op == "ipset":
            res = upd.update_ipset(obj.get("path") or "")
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
    prepared_hosts = None
    shutdown_owner = False
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
                if obj == {"cmd": "zapret_update", "op": "hosts_prepare"}:
                    import gd_zapret_update as upd

                    # No shared operation lock or disk writes during network I/O.
                    # The verified response can only be used on this connection.
                    prepared_hosts = None
                    try:
                        prepared_hosts = upd.fetch_hosts_content()
                        result = {"ok": True}
                    except Exception as exc:  # noqa: BLE001 — always reply to the UI
                        result = {"ok": False, "error": str(exc)}
                elif obj == {"cmd": "zapret_update", "op": "hosts"} and prepared_hosts is not None:
                    content, prepared_hosts = prepared_hosts, None
                    try:
                        with _OP_LOCK:
                            if _stopping:
                                result = _stopping_reply()
                            else:
                                data = upd.apply_hosts(content)
                                result = {"ok": bool(data.get("ok")), "data": data, "error": data.get("error")}
                    except Exception as exc:  # noqa: BLE001 — always reply to the UI
                        result = {"ok": False, "error": str(exc)}
                elif obj.get("cmd") == "shutdown":
                    prepared_hosts = None
                    try:
                        _validate_command(obj)
                    except (TypeError, ValueError) as exc:
                        result = {"ok": False, "error": str(exc)}
                    else:
                        result = _shutdown(obj)
                        shutdown_owner = result is not None
                        if not shutdown_owner:
                            result = _stopping_reply()
                else:
                    prepared_hosts = None
                    result = handle(obj)
                send_message(conn, result)
            except (OSError, EOFError):
                break
            if shutdown_owner:
                break
    except (OSError, EOFError):
        pass
    finally:
        try:
            conn.close()
        finally:
            if shutdown_owner:
                os._exit(0)


def _owner_handle(ui_pid: int):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    handle_ = k32.OpenProcess(0x00100000, False, ui_pid)  # SYNCHRONIZE
    if not handle_:
        raise ctypes.WinError(ctypes.get_last_error())
    return k32, handle_


def watchdog(k32, owner_handle) -> None:
    """Откатить изменения после завершения конкретного процесса-владельца."""
    try:
        wait = k32.WaitForSingleObject(owner_handle, 0xFFFFFFFF)  # INFINITE
    finally:
        k32.CloseHandle(owner_handle)
    # Stop on a wait failure as well. The service cannot safely outlive its
    # owner if the owner's lifetime is no longer observable.
    # The config may have been saved even if the keep notification was lost
    # when the window exited or the connection dropped.
    result = _shutdown({"restore": not _read_keep_setting(default=_keep_on_exit)})
    if result is not None:
        _log(result["error"] or f"owner watch ended ({wait})")  # i18n: skip (лог)
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
    _LOG_FILE = args.log
    authkey = bytes.fromhex(args.token)
    # Bind before loading state or starting a watchdog: a failed second
    # instance must not run cleanup for the GPU owned by the first one.
    try:
        listener = Listener(address=(args.host, args.port), family="AF_INET", authkey=authkey)
    except OSError:
        return 2
    with listener:
        try:
            ui_created = psutil.Process(args.ui_pid).create_time()
            k32, owner_handle = _owner_handle(args.ui_pid)
        except (OSError, psutil.Error):
            return 2
        try:
            if psutil.Process(args.ui_pid).create_time() != ui_created:
                raise psutil.NoSuchProcess(args.ui_pid)
            _load_kept_original()
            threading.Thread(target=watchdog, args=(k32, owner_handle), daemon=True).start()
        except (OSError, psutil.Error):
            k32.CloseHandle(owner_handle)
            return 2
        except BaseException:
            k32.CloseHandle(owner_handle)
            raise
        _serve(listener, args, ui_created)
    return 0


def _serve(listener, args, ui_created: float) -> None:
    # i18n: skip-start — строки журнала компаньона (лог-файл, разработчику)
    _log(f"слушаю {args.host}:{args.port} (ui-pid {args.ui_pid})")

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
