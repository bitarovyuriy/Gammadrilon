# -*- coding: utf-8 -*-
"""Аудит локализации: русские литералы в коде, которых нет в словаре EN.

Идея: в RU-режиме строку можно передать как есть (t("русский текст") или даже без t,
если подпись ставится напрямую), но в EN-режиме такая строка останется русской.
Проверяем каждый строковый литерал с кириллицей: есть ли он ключом в STRINGS["en"].

Исключения (честные): комментарии/docstring не считаются — они не показываются
пользователю; данные аудита/журнала пишутся в языке момента записи и это осознанно.
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gd_i18n as i18n  # noqa: E402

CYR = re.compile(r"[А-Яа-яЁё]")
SKIP_FILES = {"gd_i18n.py", "gd_agent.py"}      # словарь и тексты-заголовки API агента
SKIP_DIRS = {".venv", "build", "dist", "design", "__pycache__", ".git"}


def literals(path: str):
    """Строковые литералы файла с номерами строк (без docstring и комментариев)."""
    src = io.open(path, encoding="utf-8").read()
    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        print(f"!! не разобрать {path}: {exc}")
        return
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docs.add(doc)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in docs:
                continue
            yield node.lineno, getattr(node, "end_lineno", node.lineno), node.value


def skip_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """Диапазоны строк между `# i18n: skip-start` и `# i18n: skip-end`."""
    ranges, start = [], None
    for no, line in enumerate(lines, 1):
        if "# i18n: skip-start" in line:
            start = no
        elif "# i18n: skip-end" in line and start is not None:
            ranges.append((start, no))
            start = None
    return ranges


def main() -> int:
    en = i18n.STRINGS["en"]
    direct = i18n.DIRECT
    missing: list[tuple[str, int, str]] = []
    files = []
    for name in sorted(os.listdir(".")):
        if name.endswith(".py") and name not in SKIP_FILES and not name.startswith("_tmp_"):
            files.append(name)

    for name in files:
        src_lines = io.open(name, encoding="utf-8").read().splitlines()
        blocks = skip_ranges(src_lines)
        for lineno, end_lineno, text in literals(name):
            if not CYR.search(text):
                continue
            # явные исключения: `# i18n: skip` на строке литерала (или строкой выше)
            # либо блок skip-start/end
            first = max(1, lineno - 1)
            if any("# i18n: skip" in src_lines[n - 1]
                   for n in range(first, min(end_lineno, len(src_lines)) + 1)):
                continue
            if any(lo <= lineno <= hi for lo, hi in blocks):
                continue
            # QSS/CSS-комментарии внутри стилей пользователю не видны
            if "/*" in text:
                text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
                if not CYR.search(text):
                    continue
            if text in en or text in direct:
                continue
            missing.append((name, lineno, text))

    out = []
    out.append(f"файлов: {len(files)}; непереведённых строк: {len(missing)}")
    ru = i18n.STRINGS["ru"]
    gaps = sorted(k for k in ru if k not in en and k not in direct)
    out.append(f"ключей в ru без перевода в en: {len(gaps)}")
    for k in gaps:
        out.append(f"   ключ | {k}")

    # мёртвые ключи: в DIRECT есть, а в коде такой строки нет (ТЗ 10: без мёртвого кода).
    # Сравниваем со множеством AST-литералов всех модулей приложения (gd_agent.py тоже):
    # склейка строк через перенос не мешает — парсер отдаёт один литерал.
    app_files = [n for n in sorted(os.listdir("."))
                 if n.endswith(".py") and n != "gd_i18n.py" and not n.startswith("_tmp_")]
    used = set()
    for name in app_files:
        for _ln, _end, text in literals(name) or ():
            used.add(text)
    dead = [k for k in direct if k not in used]
    out.append(f"ключей DIRECT без использования в коде: {len(dead)}")
    for k in dead:
        out.append(f"   мёртвый | {k}")

    # дубликаты ключей в словарях (в dict-литерале побеждает последний — это мёртвый код)
    tree = ast.parse(io.open("gd_i18n.py", encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        value = node.value
        if not names or not isinstance(value, ast.Dict):
            continue
        if names[0] == "DIRECT":
            keys = [k.value for k in value.keys if isinstance(k, ast.Constant)]
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            out.append(f"дубликатов в DIRECT: {len(dupes)}")
            for k in dupes:
                out.append(f"   дубль | {k}")
        elif names[0] == "STRINGS":
            for lang, d in zip(value.keys, value.values):
                if not isinstance(d, ast.Dict):
                    continue
                keys = [k.value for k in d.keys if isinstance(k, ast.Constant)]
                dupes = sorted({k for k in keys if keys.count(k) > 1})
                out.append(f"дубликатов в STRINGS[{lang.value}]: {len(dupes)}")
                for k in dupes:
                    out.append(f"   дубль | {k}")
    by_file: dict[str, list] = {}
    for name, lineno, text in missing:
        by_file.setdefault(name, []).append((lineno, text))
    for name, items in sorted(by_file.items(), key=lambda kv: -len(kv[1])):
        out.append(f"\n== {name} ({len(items)})")
        for lineno, text in items:
            short = text if len(text) <= 120 else text[:117] + "…"
            out.append(f"   {lineno:5} | {short}")
    report = "\n".join(out)
    print(report)
    with io.open(os.path.join("build", "shots", "l10n.txt"), "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
