# -*- coding: utf-8 -*-
"""Журнал изменений (ТЗ 5.5).

Отвечает на вопрос «что произошло»: было → стало, чем вызвано, можно ли откатить.
Пишется в %ProgramData%\\Gammadrilon\\journal.jsonl (по строке JSON на событие).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import gd_i18n as i18n

MAX_LINES = 800        # хвост, который держим на диске


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
        _trim(p)
    except OSError:
        pass
    return entry


def _trim(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_LINES:
            with path.open("w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_LINES:])
    except OSError:
        pass


def recent(n: int = 60) -> list[dict]:
    p = journal_path()
    if not p.exists():
        return []
    try:
        with p.open("r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()          # свежие сверху
    return out


def clear() -> None:
    try:
        p = journal_path()
        if p.exists():
            p.unlink()
    except OSError:
        pass


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
