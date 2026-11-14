# -*- coding: utf-8 -*-
"""Правило «применить пресет гаммы»: расписание и запуск процесса.

Действие живёт в окне (`_run_rule_action`), потому что пресеты знает только
конфиг приложения. Проверяем выбор пресета и отказ на несуществующем номере.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_rules  # noqa: E402
import main as gd_main  # noqa: E402


class FakeWindow:
    def __init__(self, presets: list[dict]) -> None:
        self.config = {"presets": presets}
        self.applied: list[tuple[int, bool]] = []
        self.failures = []
        self.statuses = []

    def apply_preset_by_index(self, index: int, auto: bool = False):
        self.applied.append((index, auto))

    def _report_failure(self, status: str, detail: str | None = None):
        self.failures.append((status, detail))

    def set_status(self, text: str, ok: bool = True):
        self.statuses.append(text)


PRESETS = [{"name": "Работа"}, {"name": "Ночь"}]


def run_action(window, action: dict):
    rule = {"name": "Вечер", "action": action}
    return gd_main.MainWindow._run_rule_action(window, rule)


def test_action_applies_preset():
    window = FakeWindow(PRESETS)

    result = run_action(window, {"kind": "gamma_preset", "preset": 1})

    assert window.applied == [(1, True)], "пресет применяется как автоматический"
    assert "Ночь" in result


def test_missing_preset_is_reported_honestly():
    window = FakeWindow(PRESETS)

    result = run_action(window, {"kind": "gamma_preset", "preset": 5})

    assert window.applied == []
    assert "не найден" in result
    assert window.failures, "отказ обязан попасть в статус и тост"


def test_broken_preset_field_does_not_crash():
    window = FakeWindow(PRESETS)

    result = run_action(window, {"kind": "gamma_preset", "preset": "abc"})

    assert window.applied == []
    assert result


def test_describe_mentions_preset_number():
    _trigger, _condition, action = gd_rules.describe(
        {"trigger": {"type": "schedule", "at": "22:00"},
         "action": {"kind": "gamma_preset", "preset": 1}})

    assert "#2" in action, action


def test_describe_handles_missing_preset():
    _trigger, _condition, action = gd_rules.describe(
        {"trigger": {"type": "schedule", "at": "22:00"},
         "action": {"kind": "gamma_preset"}})

    assert "не выбран" in action


def test_process_and_battery_events_are_shared_by_all_matching_rules(monkeypatch):
    engine = gd_rules.RuleEngine()
    current = {"already.exe"}
    on_ac = True
    monkeypatch.setattr(engine, "_running_processes", lambda: current.copy())
    monkeypatch.setattr(gd_rules, "battery_on_ac", lambda: on_ac)
    monkeypatch.setattr(engine, "_ac_state", lambda: on_ac)
    engine.prime()

    triggers = [{"type": "process_start", "process": name} for name in
                ("already.exe", "started.exe", "started.exe", "other.exe")]
    triggers += [{"type": "on_battery"}, {"type": "on_battery"}]
    rules = [_new_rule(trigger) for trigger in triggers]
    current.update(("started.exe", "other.exe"))
    on_ac = False

    assert [rule for rule, _ in engine.evaluate(rules)] == rules[1:]
    assert engine.evaluate(rules) == []
    current.clear()
    assert engine.evaluate(rules) == []
    current.add("started.exe")
    assert [rule for rule, _ in engine.evaluate(rules)] == rules[1:3]


def _new_rule(trigger):
    return {**gd_rules.new_rule(), "trigger": trigger}


def test_rules_created_together_fire_at_the_same_scheduled_time(monkeypatch):
    monkeypatch.setattr(gd_rules.time, "time", lambda: 1700000000)
    engine = gd_rules.RuleEngine()
    monkeypatch.setattr(engine, "_running_processes", set)
    engine.prime()
    engine._last_check = datetime(2026, 1, 1, 21, 59, 59)
    rules = [_new_rule({"type": "schedule", "at": "22:00"}) for _ in range(2)]

    assert [rule for rule, _ in engine.evaluate(rules, datetime(2026, 1, 1, 22))] == rules
