# -*- coding: utf-8 -*-
"""Переводы: плейсхолдеры в русской и английской строке должны совпадать.

Если в русской строке `{v0}`, а в английской `{v1}`, английский интерфейс
упадёт на `KeyError` — и только у того, кто переключил язык. Проверка ловит
это на месте: сравниваем наборы имён в каждой паре.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_i18n  # noqa: E402

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text or ""))


def strings_tables() -> list[dict]:
    """Все словари переводов из gd_i18n (STRINGS ru/en и DIRECT)."""
    tables = []
    for name in ("STRINGS", "DIRECT"):
        table = getattr(gd_i18n, name, None)
        if isinstance(table, dict):
            tables.append({name: table})
    return tables


def test_direct_pairs_have_same_placeholders():
    """В DIRECT ключом может быть и русская строка, и ключ из STRINGS."""
    direct = getattr(gd_i18n, "DIRECT", None)
    if not isinstance(direct, dict):
        return
    russian_table = (getattr(gd_i18n, "STRINGS", {}) or {}).get("ru") or {}

    problems = []
    for key, english in direct.items():
        if not isinstance(english, str):
            continue
        # ключ вида "toast.*" живёт в STRINGS: плейсхолдеры — в русской строке
        source = russian_table.get(key, key)
        if placeholders(source) != placeholders(english):
            problems.append(
                f"{str(source)[:40]!r}: {sorted(placeholders(source))} != {sorted(placeholders(english))}")
    assert not problems, "; ".join(problems[:5])


def test_string_tables_match_between_languages():
    strings = getattr(gd_i18n, "STRINGS", None)
    if not isinstance(strings, dict):
        return
    ru = strings.get("ru") or {}
    en = strings.get("en") or {}
    problems = []
    for key, russian in ru.items():
        english = en.get(key)
        if not isinstance(russian, str) or not isinstance(english, str):
            continue
        if placeholders(russian) != placeholders(english):
            problems.append(f"{key}: {sorted(placeholders(russian))} != {sorted(placeholders(english))}")
    assert not problems, "; ".join(problems[:5])


def test_every_placeholder_in_russian_exists_in_string():
    """Плейсхолдер должен быть в самой строке, а не только в переводе."""
    strings = getattr(gd_i18n, "STRINGS", None)
    if not isinstance(strings, dict):
        return
    ru = strings.get("ru") or {}
    problems = []
    for key, russian in ru.items():
        english = (strings.get("en") or {}).get(key)
        if not isinstance(russian, str) or not isinstance(english, str):
            continue
        extra = placeholders(english) - placeholders(russian)
        if extra:
            problems.append(f"{key}: в переводе лишние {sorted(extra)}")
    assert not problems, "; ".join(problems[:5])
