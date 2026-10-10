
# -*- coding: utf-8 -*-
"""Правила-автоматизации (ТЗ 5.4).

Триггер → условие → действие, с таблицей на экране «Правила» и логом срабатываний.

Триггеры: запуск процесса X, переход на батарею, расписание (время суток).
Действия: применить профиль CPU/GPU, вернуть исходное. Действия из этапа «Утилиты»
(очистка standby list, смена DNS) зарегистрированы, но помечены как недоступные —
до их реализации в палитре не предлагаются.

Правила: %ProgramData%\\Gammadrilon\\rules.json
Лог:     %ProgramData%\\Gammadrilon\\rules_log.jsonl (последние 100)
"""
from __future__ import annotations

import gd_i18n as i18n

import json
import os
import time
from datetime import datetime
from pathlib import Path

try:
    import psutil
except Exception:  # noqa: BLE001
    psutil = None

MAX_FIRES = 100

TRIGGER_TYPES = {
    "process_start": i18n.t("Запуск процесса"),
    "on_battery": i18n.t("Переход на батарею"),
    "schedule": i18n.t("По расписанию"),
}

ACTIONS = {
    "cpu_profile": i18n.t("Профиль CPU"),
    "cpu_restore": i18n.t("Вернуть схему CPU"),
    "gpu_profile": i18n.t("Профиль GPU"),
    "gpu_restore": i18n.t("Вернуть лимит GPU"),
    "purge_standby": i18n.t("Очистить standby list"),
    # зарезервировано под этап «Утилиты» (ТЗ 6)
    "set_dns": i18n.t("Сменить DNS (нужны Утилиты)"),
}

UNAVAILABLE_ACTIONS = {"set_dns"}

CPU_PROFILES = (("eco", i18n.t("ЭКО")), ("normal", i18n.t("НОРМАЛ")), ("boost", i18n.t("БУСТ")))
GPU_PROFILES = (("quiet", i18n.t("Тихий")), ("default", i18n.t("Дефолт")),
                ("game", i18n.t("Игровой")), ("max", i18n.t("Макс")))


def _base_dir() -> Path:
    base = os.environ.get("ProgramData", r"C:\ProgramData")
    return Path(base) / "Gammadrilon"


def rules_path() -> Path:
    return _base_dir() / "rules.json"


def log_path() -> Path:
    return _base_dir() / "rules_log.jsonl"


def load_rules() -> list[dict]:
    p = rules_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save_rules(rules: list[dict]) -> None:
    p = rules_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def new_rule() -> dict:
    return {
        "id": f"r{int(time.time())}",
        "enabled": True,
        "name": i18n.t("Новое правило"),
        "trigger": {"type": "process_start", "process": ""},
        "condition": {"load_below": None},
        "action": {"kind": "cpu_profile", "profile": "eco"},
    }


def describe(rule: dict) -> tuple[str, str, str]:
    """(триггер, условие, действие) — человекочитаемо для таблицы."""
    t = rule.get("trigger") or {}
    ttype = t.get("type")
    if ttype == "process_start":
        trig = f"Запуск «{t.get('process') or '—'}»"
    elif ttype == "on_battery":
        trig = i18n.t("Переход на батарею")
    elif ttype == "schedule":
        trig = f"По расписанию {t.get('at') or '—'}"
    else:
        trig = "—"

    c = rule.get("condition") or {}
    lb = c.get("load_below")
    cond = f"CPU-загрузка < {lb}%" if lb is not None else i18n.t("без условий")

    a = rule.get("action") or {}
    kind = a.get("kind")
    if kind == "cpu_profile":
        label = dict(CPU_PROFILES).get(a.get("profile"), a.get("profile"))
        act = f"Профиль CPU: {label}"
    elif kind == "gpu_profile":
        label = dict(GPU_PROFILES).get(a.get("profile"), a.get("profile"))
        act = f"Профиль GPU: {label}"
    elif kind == "cpu_restore":
        act = i18n.t("Вернуть схему CPU")
    elif kind == "gpu_restore":
        act = i18n.t("Вернуть лимит GPU")
    else:
        act = ACTIONS.get(kind, "—")
    if kind in UNAVAILABLE_ACTIONS:
        act += i18n.t(" — недоступно")
    return trig, cond, act


def log_fire(rule: dict, result: str) -> dict:
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "rule": rule.get("name") or rule.get("id"),
        "action": describe(rule)[2],
        "result": result,
    }
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_FIRES:
            p.write_text("\n".join(lines[-MAX_FIRES:]) + "\n", encoding="utf-8")
    except OSError:
        pass
    return entry


def recent_fires(n: int = MAX_FIRES) -> list[dict]:
    p = log_path()
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out


def clear_fires() -> None:
    try:
        p = log_path()
        if p.exists():
            p.unlink()
    except OSError:
        pass


def battery_on_ac():
    """True/False — питание от сети/батареи; None — батареи нет."""
    if psutil is None:
        return None
    try:
        b = psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        return None
    if b is None:
        return None
    return bool(b.power_plugged)


def cpu_load() -> float | None:
    if psutil is None:
        return None
    try:
        return psutil.cpu_percent(interval=None)
    except Exception:  # noqa: BLE001
        return None


class RuleEngine:
    """Без Qt: evaluate() дёргается таймером из main.py."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._on_ac = battery_on_ac()
        self._fired_day: dict[str, str] = {}     # id правила -> дата срабатывания
        self._primed = False

    def prime(self) -> None:
        """Запомнить текущее состояние, чтобы не сработать на «уже запущено»."""
        self._seen = self._running_processes()
        self._on_ac = battery_on_ac()
        self._primed = True

    @staticmethod
    def _running_processes() -> set[str]:
        names = set()
        if psutil is None:
            return names
        try:
            for p in psutil.process_iter(["name"]):
                n = (p.info.get("name") or "").lower()
                if n:
                    names.add(n)
        except Exception:  # noqa: BLE001
            pass
        return names

    def _trigger_fired(self, rule: dict) -> bool:
        t = rule.get("trigger") or {}
        ttype = t.get("type")

        if ttype == "process_start":
            wanted = (t.get("process") or "").strip().lower()
            if not wanted:
                return False
            running = self._running_processes()
            appeared = wanted in running and wanted not in self._seen
            self._seen = running if not appeared else self._seen | {wanted}
            return appeared

        if ttype == "on_battery":
            now = battery_on_ac()
            if now is None:
                return False
            fired = self._on_ac is True and now is False
            self._on_ac = now
            return fired

        if ttype == "schedule":
            at = (t.get("at") or "").strip()
            if len(at) != 5 or ":" not in at:
                return False
            today = datetime.now().strftime("%Y-%m-%d")
            now_hm = datetime.now().strftime("%H:%M")
            key = str(rule.get("id"))
            if now_hm >= at and self._fired_day.get(key) != today:
                self._fired_day[key] = today
                return True
            return False

        return False

    def _condition_ok(self, rule: dict) -> bool:
        c = rule.get("condition") or {}
        lb = c.get("load_below")
        if lb is None:
            return True
        load = cpu_load()
        if load is None:
            return True
        return load < float(lb)

    def evaluate(self, rules: list[dict]) -> list[tuple[dict, str]]:
        """Проверить правила. Возвращает [(правило, причина)] для сработавших."""
        if not self._primed:
            self.prime()
        fired = []
        for rule in rules:
            if not rule.get("enabled"):
                continue
            kind = (rule.get("action") or {}).get("kind")
            if kind in UNAVAILABLE_ACTIONS:
                continue
            if self._trigger_fired(rule) and self._condition_ok(rule):
                fired.append((rule, i18n.t("условие выполнено")))
        return fired
