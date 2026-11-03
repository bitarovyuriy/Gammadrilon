# -*- coding: utf-8 -*-
"""«Чемоданчик»: папка с presets.json и маркером для переноса на другой ПК."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_profiles  # noqa: E402


def test_suitcase_has_settings_and_marker(tmp_path):
    settings = {"presets": [{"name": "Игра"}], "settings": {"theme": "dark"}}

    ok, message = gd_profiles.make_suitcase(tmp_path, settings)

    assert ok is True, message
    folder = tmp_path / "Gammadrilon-portable"
    assert (folder / "portable.txt").is_file()
    saved = json.loads((folder / "presets.json").read_text(encoding="utf-8"))
    assert saved["presets"][0]["name"] == "Игра"


def test_suitcase_without_settings_still_has_marker(tmp_path):
    ok, _message = gd_profiles.make_suitcase(tmp_path, None)

    assert ok is True
    folder = tmp_path / "Gammadrilon-portable"
    assert (folder / "portable.txt").is_file()
    assert not (folder / "presets.json").exists()


def test_suitcase_reports_bad_destination():
    ok, message = gd_profiles.make_suitcase("\0плохой путь", {})

    assert ok is False
    assert message
