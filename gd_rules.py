
# -*- coding: utf-8 -*-
"""Правила-автоматизации (ТЗ 5.4).

Триггер → условие → действие, с таблицей на экране «Правила» и логом срабатываний.

Триггеры: запуск процесса X, переход на батарею, расписание (время суток).
Действия: применить профиль CPU/GPU, вернуть исходное, очистить standby list,
сменить DNS (провайдер выбирается в правиле).

Действия из ADMIN_ACTIONS требуют прав администратора, поэтому выполняются
**только через уже запущенный хелпер** (`gd_helper.HelperClient.call_existing`):
фоновое правило не поднимает UAC — иначе поверх чужой игры всплывало бы окно
запроса прав. Нет сессии — в лог правил идёт честная причина отказа.

Правила: %ProgramData%\\Gammadrilon\\rules.json
Лог:     %ProgramData%\\Gammadrilon\\rules_log.jsonl (последние 100)
"""
from __future__ import annotations

import gd_i18n as i18n

import gd_dns
import gd_state

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

#: восстановление файла правил: "" — всё в порядке; "backup" — файл был повреждён,
#: копия сохранена как rules.json.bak; "no_backup" — копию сделать не удалось.
#: Вкладка «Правила» показывает это пользователю (см. `recovery_note`)
RECOVERY: str = ""

#: окно между проверками расписания. Тик движка — 3 с, поэтому разрыв больше двух
#: минут означает, что приложение спало или было заморожено: время, прошедшее «мимо»,
#: не догоняем (см. `RuleEngine._schedule_fired`)
SCHEDULE_MAX_GAP = 120.0

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
    "set_dns": i18n.t("Сменить DNS"),
}

#: действия, которым нужны права администратора: правило выполняет их только через
#: уже запущенный хелпер — запрос UAC из фона не показываем (см. docstring модуля)
ADMIN_ACTIONS = {"purge_standby", "set_dns"}

#: механизм «зарегистрировано, но пока недоступно»: пуст с момента, когда закрыли
#: «Утилиты» (ТЗ 6.1/6.2), оставлен для будущих разделов
UNAVAILABLE_ACTIONS: set[str] = set()

CPU_PROFILES = (("eco", i18n.t("ЭКО")), ("normal", i18n.t("НОРМАЛ")), ("boost", i18n.t("БУСТ")))
GPU_PROFILES = (("quiet", i18n.t("Тихий")), ("default", i18n.t("Дефолт")),
                ("game", i18n.t("Игровой")), ("max", i18n.t("Макс")))
#: провайдеры DNS для правила «Сменить DNS». «Свой» в списке нет: ему нужны адреса из
#: поля вкладки «DNS», а правило должно быть самодостаточным.
DNS_PROFILES = tuple((r["key"], r["label"]) for r in gd_dns.RESOLVERS if r["key"] != "custom")


def _base_dir() -> Path:
    base = os.environ.get("ProgramData", r"C:\ProgramData")
    return Path(base) / "Gammadrilon"


def rules_path() -> Path:
    return _base_dir() / "rules.json"


def log_path() -> Path:
    return _base_dir() / "rules_log.jsonl"


def load_rules() -> list[dict]:
    """Прочитать правила. Повреждённый файл не затирается: остаётся копия .bak.

    Раньше битый файл молча превращался в пустой список, и первое же сохранение
    затирало его пустотой — правила пропадали насовсем. Теперь копия сохраняется,
    а вкладка «Правила» честно говорит о восстановлении.
    """
    global RECOVERY
    p = rules_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        RECOVERY = "backup" if gd_state.quarantine(p) else "no_backup"
        return []
    if not isinstance(data, list):
        RECOVERY = "backup" if gd_state.quarantine(p) else "no_backup"
        return []
    # мусорная запись (строка, число) раньше роняла таблицу и движок — отбрасываем
    return [r for r in data if isinstance(r, dict)]


def save_rules(rules: list[dict]) -> bool:
    """Сохранить правила атомарно. False — не получилось (вызывающий скажет)."""
    p = rules_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return gd_state.write_json_atomic(p, rules)


def recovery_note() -> str:
    """Строка для вкладки «Правила» о восстановлении файла. Пусто — всё в порядке."""
    if RECOVERY == "backup":
        return i18n.t("Файл правил был повреждён — правила не загружены; "
                      "копия сохранена как rules.json.bak")
    if RECOVERY == "no_backup":
        return i18n.t("Файл правил был повреждён — правила не загружены")
    return ""


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
        trig = i18n.t("Запуск «{v0}»", v0=t.get('process') or '—')
    elif ttype == "on_battery":
        trig = i18n.t("Переход на батарею")
    elif ttype == "schedule":
        trig = i18n.t("По расписанию {v0}", v0=t.get('at') or '—')
    else:
        trig = "—"

    c = rule.get("condition") or {}
    lb = c.get("load_below")
    cond = i18n.t("CPU-загрузка < {v0}%", v0=lb) if lb is not None else i18n.t("без условий")

    a = rule.get("action") or {}
    kind = a.get("kind")
    if kind == "cpu_profile":
        label = dict(CPU_PROFILES).get(a.get("profile"), a.get("profile"))
        act = i18n.t("Профиль CPU: {v0}", v0=label)
    elif kind == "gpu_profile":
        label = dict(GPU_PROFILES).get(a.get("profile"), a.get("profile"))
        act = i18n.t("Профиль GPU: {v0}", v0=label)
    elif kind == "set_dns":
        label = dict(DNS_PROFILES).get(a.get("dns"), a.get("dns") or "—")
        act = i18n.t("Сменить DNS: {v0}", v0=label)
    elif kind == "cpu_restore":
        act = i18n.t("Вернуть схему CPU")
    elif kind == "gpu_restore":
        act = i18n.t("Вернуть лимит GPU")
    else:
        act = ACTIONS.get(kind, "—")
    if kind in UNAVAILABLE_ACTIONS:
        act += i18n.t(" — недоступно")
    return trig, cond, act


def dns_servers(key: str | None) -> list[str]:
    """Адреса резолвера по ключу — для выполнения действия «Сменить DNS».

    Живёт здесь, рядом с DNS_PROFILES: правило хранит только ключ, а список адресов
    берётся из одного места (`gd_dns.RESOLVERS`), чтобы он не разъезжался.
    """
    r = next((x for x in gd_dns.RESOLVERS if x["key"] == key), None)
    return list(r.get("servers") or []) if r else []


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
            # обрезка тоже атомарная: обрыв посреди неё не оставит пустой лог
            gd_state.write_text_atomic(p, "\n".join(lines[-MAX_FIRES:]) + "\n")
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


#: окружение правил, снятое фоновым воркером: psutil.process_iter в GUI-потоке
#: каждые 3 секунды — это фризы, поэтому снимок делается заранее и только в фоне
_env: dict = {"procs": set(), "on_ac": None, "load": None, "ts": 0.0}
_ENV_TTL = 10.0        # сколько держим снимок, если фон почему-то встал


def scan_processes() -> set[str]:
    """Имена запущенных процессов в нижнем регистре. Дорогой вызов — только в фоне."""
    names: set[str] = set()
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


def collect_env(need: set[str] | None = None) -> dict:
    """Снять окружение для правил: процессы, питание, загрузка CPU (в фоне).

    ``need`` — что именно нужно (`env_need`). Снимать всё «на всякий случай» нельзя:
    `psutil.process_iter` стоит десятки миллисекунд, и правило «тихий час»
    (расписание + загрузка) платило бы за список процессов, который ему не нужен.
    """
    if need is None:
        need = {"procs", "on_ac", "load"}
    env: dict = {}
    if "procs" in need:
        env["procs"] = scan_processes()
    if "on_ac" in need:
        env["on_ac"] = battery_on_ac()
    if "load" in need:
        env["load"] = cpu_load()
    return env


def update_env(env: dict) -> None:
    """Сохранить снятое в фоне окружение (приходит в GUI-поток по сигналу).

    Ключ, которого нет в снимке, означает «больше не следим» — старые данные
    стираются, иначе правило, добавленное позже, читало бы чужой устаревший набор.
    """
    if not isinstance(env, dict):
        return
    if "procs" in env:
        _env["procs"] = set(env.get("procs") or ())
    else:
        _env["procs"] = set()
    _env["on_ac"] = env.get("on_ac")
    _env["load"] = env.get("load")
    _env["ts"] = time.monotonic()


def env_need(rules: list[dict] | None = None) -> set[str]:
    """Какие части окружения нужны включённым правилам: ``procs`` / ``on_ac`` / ``load``."""
    if rules is None:
        rules = load_rules()
    need: set[str] = set()
    for rule in rules:
        if not rule.get("enabled"):
            continue
        if (rule.get("action") or {}).get("kind") in UNAVAILABLE_ACTIONS:
            continue
        ttype = (rule.get("trigger") or {}).get("type")
        if ttype == "process_start":
            need.add("procs")
        elif ttype == "on_battery":
            need.add("on_ac")
        if (rule.get("condition") or {}).get("load_below") is not None:
            need.add("load")
    return need


def needs_env(rules: list[dict] | None = None) -> bool:
    """Нужен ли фоновый снимок окружения вообще (подробности — в `env_need`).

    ``psutil.process_iter`` раз в 3 с стоит заметных миллисекунд, а у нового
    пользователя включённых правил обычно нет вовсе — тогда снимать нечего.
    """
    return bool(env_need(rules))


def _env_fresh() -> bool:
    return _env["ts"] > 0 and (time.monotonic() - _env["ts"]) < _ENV_TTL


class RuleEngine:
    """Без Qt: evaluate() дёргается таймером из main.py."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._on_ac = battery_on_ac()
        self._fired_day: dict[str, str] = {}     # id правила -> дата срабатывания
        self._last_check: datetime | None = None  # время прошлой проверки (расписание)
        self._primed = False

    def prime(self) -> None:
        """Запомнить текущее состояние, чтобы не сработать на «уже запущено»."""
        self._seen = self._running_processes()
        self._on_ac = battery_on_ac()
        self._last_check = datetime.now()
        self._primed = True

    @staticmethod
    def _running_processes() -> set[str]:
        """Процессы из фонового снимка, если он свежий (иначе — скан в текущем потоке)."""
        if _env_fresh() and _env["procs"]:
            return set(_env["procs"])
        return scan_processes()

    @staticmethod
    def _ac_state():
        return _env["on_ac"] if _env_fresh() else battery_on_ac()

    @staticmethod
    def _load_state():
        return _env["load"] if _env_fresh() else cpu_load()

    def _trigger_fired(self, rule: dict, now: datetime) -> bool:
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
            now_ac = self._ac_state()
            if now_ac is None:
                return False
            fired = self._on_ac is True and now_ac is False
            self._on_ac = now_ac
            return fired

        if ttype == "schedule":
            return self._schedule_fired(rule, now)

        return False

    def _schedule_fired(self, rule: dict, now: datetime) -> bool:
        """Сработало ли расписание: указанное время **наступило** между проверками.

        Правило «23:30» срабатывает в 23:30 у работающего приложения, а не «в любой
        момент после 23:30»: иначе запуск в 10 утра применял бы ночной профиль
        задним числом, хотя пользователь его не просил. Пропущенное время не
        догоняется — разрыв между проверками больше окна значит, что приложение
        спало или было закрыто, и тихо править систему «потом» нельзя.
        """
        t = rule.get("trigger") or {}
        at = (t.get("at") or "").strip()
        if len(at) != 5 or ":" not in at:
            return False
        try:
            hh, mm = (int(x) for x in at.split(":", 1))
        except ValueError:
            return False
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return False

        prev = self._last_check
        if prev is None:
            return False
        if (now - prev).total_seconds() > SCHEDULE_MAX_GAP:
            return False                        # пауза: приложение спало/не работало
        key = str(rule.get("id"))
        today = now.strftime("%Y-%m-%d")
        if self._fired_day.get(key) == today:
            return False                        # один раз в сутки
        at_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if not (prev < at_dt <= now):
            return False
        self._fired_day[key] = today
        return True

    def _condition_ok(self, rule: dict) -> bool:
        c = rule.get("condition") or {}
        lb = c.get("load_below")
        if lb is None:
            return True
        load = self._load_state()
        if load is None:
            return True
        return load < float(lb)

    def evaluate(self, rules: list[dict],
                 now: datetime | None = None) -> list[tuple[dict, str]]:
        """Проверить правила. Возвращает [(правило, причина)] для сработавших.

        ``now`` можно передать вручную (тесты расписания); в приложении время берётся
        из часов. Оно же становится точкой отсчёта для следующей проверки.
        """
        if not self._primed:
            self.prime()
        now = now or datetime.now()
        fired = []
        for rule in rules:
            if not rule.get("enabled"):
                continue
            kind = (rule.get("action") or {}).get("kind")
            if kind in UNAVAILABLE_ACTIONS:
                continue
            if self._trigger_fired(rule, now) and self._condition_ok(rule):
                fired.append((rule, i18n.t("условие выполнено")))
        self._last_check = now
        return fired
