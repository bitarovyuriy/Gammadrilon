# -*- coding: utf-8 -*-
"""Вызовы `i18n.t(...)` согласованы со строкой: лишний или недостающий аргумент.

Два класса ошибок, которые видит только пользователь:
- в строке есть `{v0}`, а в вызове аргумента нет → `KeyError` в момент показа;
- аргумент передан, а в строке его нет → опечатка, значение молча теряется.
Разбираем исходники через AST: так видно и вызовы внутри f-строк.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_i18n  # noqa: E402

PLACEHOLDER = re.compile(r"\{(\w+)(?::[^}]*)?\}")
SKIP_FILES = {"gd_i18n.py"}


def russian_strings() -> dict[str, str]:
    table = {}
    direct = getattr(gd_i18n, "DIRECT", {}) or {}
    strings = (getattr(gd_i18n, "STRINGS", {}) or {}).get("ru") or {}
    table.update(strings)
    table.update(direct)
    return table


def t_calls(path: Path) -> list[tuple[str, set[str], int]]:
    """Вызовы i18n.t("строка", v0=...) → (строка, имена аргументов, номер строки)."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name != "t" or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        keywords = {item.arg for item in node.keywords if item.arg}
        found.append((first.value, keywords, node.lineno))
    return found


def test_arguments_match_placeholders():
    table = russian_strings()
    problems = []
    for path in sorted(ROOT.glob("*.py")):
        if path.name in SKIP_FILES:
            continue
        for text, keywords, line in t_calls(path):
            # вызов может получать ключ словаря ("w.gamma.current") — тогда
            # плейсхолдеры живут не в тексте вызова, а в переводе
            source = table.get(text, text)
            needed = set(PLACEHOLDER.findall(source))
            if not needed and not keywords:
                continue
            missing = needed - keywords
            extra = keywords - needed
            if missing:
                problems.append(f"{path.name}:{line} нет аргументов {sorted(missing)}")
            if extra:
                problems.append(f"{path.name}:{line} лишние аргументы {sorted(extra)}")
    assert not problems, "; ".join(problems[:8])
