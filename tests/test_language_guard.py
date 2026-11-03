# -*- coding: utf-8 -*-
"""Язык: чужой код не ломает интерфейс, определение всегда даёт ru или en."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_i18n  # noqa: E402


def test_detect_returns_supported_language():
    assert gd_i18n.detect() in gd_i18n.LANGS


def test_unknown_language_keeps_previous():
    gd_i18n.set_language("ru")

    gd_i18n.set_language("de")      # такого языка нет

    assert gd_i18n.current() == "ru", "неизвестный код не должен менять язык"


def test_language_is_saved_to_config():
    config = {"settings": {}}

    gd_i18n.set_language("en", config)

    assert config["settings"]["language"] == "en"
    gd_i18n.set_language("ru")
