# -*- coding: utf-8 -*-
"""«Что нового» из описания релиза: чистый текст и обрезка по строке."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_update  # noqa: E402


def test_markdown_is_cleaned():
    info = {"body": """## Что нового

- Пресеты гаммы помнят подсветку
- Автопрофиль по игре
* DDC/CI на ноутбуках выключен честно

<!-- служебный комментарий -->
**Full Changelog**: https://github.com/x/y/compare/v1...v2
"""}

    text = gd_update.release_notes(info)

    assert "Пресеты гаммы помнят подсветку" in text
    assert "Автопрофиль по игре" in text
    assert "##" not in text and "- " not in text
    assert "Full Changelog" not in text
    assert "служебный" not in text


def test_long_notes_are_cut_at_line_border():
    line = "Очень длинное описание изменения номер {0}"
    body = "\n".join(line.format(index) for index in range(40))

    text = gd_update.release_notes({"body": body}, limit=200)

    assert len(text) <= 201, len(text)
    assert text.endswith("…")
    assert "номер 0" in text


def test_empty_body_gives_empty_string():
    assert gd_update.release_notes({}) == ""
    assert gd_update.release_notes(None) == ""
    assert gd_update.release_notes({"body": "   "}) == ""


def test_short_notes_are_untouched():
    text = gd_update.release_notes({"body": "Исправлена сборка установщика"})

    assert text == "Исправлена сборка установщика"
