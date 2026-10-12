# -*- coding: utf-8 -*-
"""Журнал изменений (ТЗ 5.5).

Отвечает на вопрос «что произошло»: было → стало, чем вызвано, можно ли откатить.
Пишется в %ProgramData%\\Gammadrilon\\journal.jsonl (по строке JSON на событие).

Журнал — обещание «история не теряется», поэтому отказ записи здесь не глушится:
`record` запоминает причину (`write_error`), один раз за сессию говорит о мёртвом
журнале тостом, а окно «Что произошло» показывает состояние файла, его путь и
пропущенные повреждённые строки (`read_report`).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

import gd_i18n as i18n
import gd_state

MAX_LINES = 800        # хвост, который держим на диске

_last_error = ""       # причина последнего отказа записи ("" — запись прошла)
_warned = False        # тост о мёртвом журнале — один раз за сессию


def write_error() -> str:
    """Причина последнего отказа записи. Пусто — журнал писался успешно."""
    return _last_error


def journal_path() -> Path:
    base = os.environ.get("ProgramData", r"C:\ProgramData")
    return Path(base) / "Gammadrilon" / "journal.jsonl"


def record(
    kind: str,
    target: str,
    before=None,
    after=None,
    why: str = "",
    source: str = "UI",
    reversible: bool = True,
) -> dict:
    """Добавить событие. kind: cpu_profile | gpu_pl | gpu_clocks | restore."""
    global _last_error
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "target": target,
        "before": before,
        "after": after,
        "why": why,
        "source": source,
        "reversible": bool(reversible),
    }
    try:
        p = journal_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _last_error = ""
        _trim(p)
    except OSError as exc:
        _last_error = f"{type(exc).__name__}: {exc}"
        _warn_dead_journal()
    return entry


def _warn_dead_journal() -> None:
    """Сказать о мёртвом журнале один раз за сессию — аудит не должен умирать молча."""
    global _warned
    if _warned or threading.current_thread() is not threading.main_thread():
        return
    _warned = True
    try:
        import gd_toast

        gd_toast.show_toast(
            i18n.t("Журнал изменений не пишется: {v0} — история изменений не сохранится.",
                   v0=_last_error),
            kind="warn")
    except Exception:  # noqa: BLE001
        pass


def probe() -> tuple[bool, str]:
    """Можно ли писать журнал прямо сейчас: (можно?, причина отказа).

    Окно «Что произошло» показывает это состояние при открытии, чтобы «пусто»
    отличалось от «файл недоступен для записи».
    """
    global _last_error
    try:
        p = journal_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8"):
            pass
    except OSError as exc:
        _last_error = f"{type(exc).__name__}: {exc}"
        return False, _last_error
    _last_error = ""
    return True, ""


def _trim(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_LINES:
            # обрезка атомарная: обрыв посреди неё не оставит пустой журнал
            gd_state.write_text_atomic(path, "".join(lines[-MAX_LINES:]))
    except OSError:
        pass


def read_report(n: int = 60) -> dict:
    """Хвост журнала и честная сводка: {'items', 'broken', 'total'}.

    `broken` — сколько строк хвоста не разобралось: журнал — ответ на вопрос
    «что произошло», и молча пропущенная запись здесь так же плоха, как пустой
    файл. Окно журнала называет это число.
    """
    p = journal_path()
    if not p.exists():
        return {"items": [], "broken": 0, "total": 0}
    try:
        with p.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return {"items": [], "broken": 0, "total": 0}
    out, broken, total = [], 0, 0
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        total += 1
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            broken += 1
    out.reverse()          # свежие сверху
    return {"items": out, "broken": broken, "total": total}


def recent(n: int = 60) -> list[dict]:
    return read_report(n)["items"]


def state_note() -> str:
    """Честная приписка о самом журнале (для API агентов): пусто — пишется и цел."""
    ok, err = probe()
    if not ok:
        return i18n.t("журнал недоступен для записи: {v0} — история может быть неполной.",
                      v0=err)
    broken = read_report(MAX_LINES)["broken"]
    if broken:
        return i18n.t("повреждено строк: {v0} — они пропущены.", v0=broken)
    return ""


def clear() -> bool:
    """Очистить журнал. False — удалить не удалось (файл остался как был)."""
    global _last_error
    try:
        p = journal_path()
        if p.exists():
            p.unlink()
    except OSError as exc:
        _last_error = f"{type(exc).__name__}: {exc}"
        return False
    _last_error = ""
    return True


def human(entry: dict) -> str:
    """Строка для списка: «12:04  ЭКО  100% → 50%  (трей)»."""
    try:
        t = datetime.fromisoformat(entry.get("ts", "")).strftime("%H:%M")
    except ValueError:
        t = "--:--"
    names = {
        "cpu_profile": i18n.t("Питание CPU"),
        "gpu_pl": i18n.t("Лимит GPU"),
        "gpu_clocks": i18n.t("Частоты GPU"),
        "zapret": i18n.t("Обход DPI"),
        "restore": i18n.t("Откат"),
    }
    kind = names.get(entry.get("kind"), entry.get("kind", "?"))
    target = entry.get("target") or ""
    if entry.get("kind") == "restore":
        body = f"{target}" if target else i18n.t("исходное состояние возвращено")
    else:
        before, after = entry.get("before"), entry.get("after")
        if before is None or after is None:
            body = f"{target}"
        else:
            body = f"{target}: {before} → {after}"
    why = entry.get("why") or ""
    src = entry.get("source") or ""
    tail = "  ".join(x for x in (why, f"({src})" if src else "") if x)
    return f"{t}  {kind} · {body}" + (f"  — {tail}" if tail else "")
