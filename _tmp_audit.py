# -*- coding: utf-8 -*-
"""Аудит: где код молча глотает отказ (except без объяснения).

Ищем блоки `except`, тело которых — только `pass`/`continue`/`return None`, без
строки-объяснения. Это места, где приложение знает о проблеме и молчит: ровно тот
класс дефектов, который в этом проекте закрывали у громкости (объяснение отказа),
журнала и питания CPU.

Запуск: .venv\\Scripts\\python.exe _tmp_audit.py [грубая-подстрока]
"""
from __future__ import annotations

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SILENT = (ast.Pass, ast.Continue)


def body_is_silent(node: ast.AST) -> bool:
    """Тело обработчика — только pass/continue (без объяснения и без return-значения)."""
    body = getattr(node, "body", [])
    return bool(body) and all(
        isinstance(stmt, SILENT)
        or (isinstance(stmt, ast.Return) and stmt.value is None)
        for stmt in body
    )


def returns_success_without_check(fn: ast.FunctionDef) -> bool:
    """Функция объявлена -> bool, но в return нет сравнения/вызова (успех выдуман)."""
    ret = fn.returns
    name = getattr(ret, "id", None) or getattr(ret, "value", None)
    if name != "bool":
        return False
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) \
                and node.value.value is True:
            return True
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) \
                and node.value.value is None:
            return False
    return False


def audit(path: str) -> tuple[list[str], list[str]]:
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"{os.path.basename(path)}: не разобран — {exc}"], []
    lines = src.splitlines()
    silent, fake = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and body_is_silent(node) \
                and not isinstance(node.type, ast.Tuple):
            silent.append(f"{os.path.basename(path)}:{node.lineno}: "
                          f"except {ast.unparse(node.type) if node.type else ''}: "
                          f"{lines[node.lineno - 1].strip()[:70]}")
        if isinstance(node, ast.FunctionDef) and returns_success_without_check(node):
            fake.append(f"{os.path.basename(path)}:{node.lineno}: def {node.name}() -> bool")
    return silent, fake


def main() -> int:
    needle = sys.argv[1] if len(sys.argv) > 1 else ""
    silent, fake = [], []
    files = sorted(f for f in os.listdir(HERE)
                   if (f.endswith(".py") and (f.startswith("gd_") or f == "main.py")))
    for f in files:
        s, k = audit(os.path.join(HERE, f))
        silent += [x for x in s if needle in x]
        fake += [x for x in k if needle in x]
    print(f"файлов проверено: {len(files)}")
    print(f"\nмолчаливых except (тело только pass/continue/return None): {len(silent)}")
    for line in silent:
        print("  " + line)
    print(f"\nфункции -> bool, где True возвращается без проверки: {len(fake)}")
    for line in fake:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
