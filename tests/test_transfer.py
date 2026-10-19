# -*- coding: utf-8 -*-
"""Перенос настроек: экспорт/импорт не должен ни молчать, ни ломать конфиг.

Проверки герметичные: системные чтения и файлы подменены, поэтому тест не
трогает ни ProgramData, ни живые настройки автора.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_profiles  # noqa: E402 — путь проекта добавлен выше


def _patch_reads(monkeypatch):
    """Подменить системные чтения экспорта: тест не должен зависеть от машины."""
    import gd_dns
    import gd_rules

    monkeypatch.setattr(gd_rules, "load_rules", lambda: [])
    monkeypatch.setattr(gd_dns, "current_state", lambda: {})
    monkeypatch.setattr(gd_profiles, "load_quiet", lambda: {})
    monkeypatch.setattr(gd_profiles, "_read_autoclean", lambda: {})


def test_export_without_settings_keeps_old_shape(tmp_path, monkeypatch):
    """Старый вызов (без конфига) не меняет файл: раздела settings нет."""
    _patch_reads(monkeypatch)

    out = tmp_path / "settings.json"
    ok, _ = gd_profiles.export_all(out)
    data = json.loads(out.read_text(encoding="utf-8"))

    assert ok and data["app"] == "Gammadrilon"
    assert data["format"] == gd_profiles.FORMAT_VERSION
    assert "settings" not in data, "без конфига раздел настроек не должен появляться"


def test_export_carries_settings(tmp_path, monkeypatch):
    """С конфигом рядом с профилями ложатся настройки приложения."""
    _patch_reads(monkeypatch)

    out = tmp_path / "settings.json"
    config = {"settings": {"lang": "en"}, "presets": [{"name": "Ночь"}]}
    ok, msg = gd_profiles.export_all(out, app_settings=config)
    data = json.loads(out.read_text(encoding="utf-8"))

    assert ok and data["settings"] == config
    assert "внутри" in msg, "подпись не говорит, что настройки в файле есть"


def test_import_applies_settings_via_callback(tmp_path, monkeypatch):
    """Раздел settings отдаётся окну (у него конфиг), а его ответ виден в итоге."""
    import gd_rules

    monkeypatch.setattr(gd_rules, "save_rules", lambda rules: True)
    monkeypatch.setattr(gd_profiles, "save_quiet", lambda data: True)
    monkeypatch.setattr(gd_profiles, "_write_autoclean", lambda data: True)

    got: dict = {}

    def apply_settings(settings):
        got.update(settings)
        return True, "после перезапуска"

    path = tmp_path / "in.json"
    path.write_text(json.dumps(
        {"app": "Gammadrilon", "rules": [], "settings": {"settings": {"lang": "en"}}},
        ensure_ascii=False), encoding="utf-8")

    ok, msg = gd_profiles.import_all(path, apply_settings=apply_settings)

    assert ok, msg
    assert got == {"settings": {"lang": "en"}}
    assert "настройки приложения" in msg
    assert "после перезапуска" in msg, "оговорка окна потерялась"


def test_import_without_window_says_so(tmp_path):
    """Настройки в файле есть, а применить их некому — это не молчание."""
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"app": "Gammadrilon", "settings": {"settings": {}}}),
                    encoding="utf-8")

    ok, msg = gd_profiles.import_all(path)

    assert not ok
    assert "без окна" in msg, f"импорт не объяснил, почему настройки пропущены: {msg}"


def test_import_refuses_garbage(tmp_path):
    """Мусор и чужой файл распознаются до записи, с человеческим текстом."""
    broken = tmp_path / "broken.json"
    broken.write_text("{не json", encoding="utf-8")
    ok, msg = gd_profiles.import_all(broken)
    assert not ok and "JSON" in msg

    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"app": "Other"}), encoding="utf-8")
    ok, msg = gd_profiles.import_all(foreign)
    assert not ok and "Gammadrilon" in msg


def test_merge_known_into_drops_unknown_and_wrong_types():
    """Чужой конфиг не должен подменять значения чужим типом."""
    import main as gd_main

    defaults = {"a": {"b": 1}, "c": "text", "d": True, "e": None, "f": [1]}
    skipped: list[str] = []

    gd_main.merge_known_into(
        defaults,
        {"a": {"b": 2, "zz": 3}, "c": 5, "d": 1, "e": 7, "f": "x", "nope": 1},
        skipped,
    )

    assert defaults == {"a": {"b": 2}, "c": "text", "d": True, "e": 7, "f": [1]}
    assert sorted(skipped) == ["a.zz", "c", "d", "f", "nope"]


def test_sanitize_presets_clamps_and_drops_garbage():
    """Пресеты гаммы: понятные чиним границами, мусор считаем."""
    import main as gd_main

    clean, dropped = gd_main.MainWindow._sanitize_presets([
        {"name": "Ночь", "brightness": 9, "contrast": "x", "gamma": 1.4,
         "temperature": 2000, "hotkey": " Ctrl+1 "},
        "мусор",
        {"name": "   "},
    ])

    assert dropped == 2
    assert len(clean) == 1
    item = clean[0]
    assert item["name"] == "Ночь"
    assert item["brightness"] == 3.0        # зажато верхней границей
    assert item["contrast"] == 1.0          # нечисло → умолчание, а не падение
    assert item["gamma"] == 1.4
    assert item["temperature"] == 3000      # зажато нижней границей
    assert item["hotkey"] == "Ctrl+1"


def test_import_keeps_keys_absent_from_file(tmp_path, monkeypatch):
    """Импорт не сбрасывает то, чего в файле нет, и не тянет чужое состояние.

    Язык — настройка, его переносим (он живёт вне DEFAULT_CONFIG, и без схемы
    рантайм-настроек молча пропал бы). «Первый запуск пройден» — состояние этой
    машины: чужое значение не должно вернуть пользователю экран знакомства.
    """
    import gd_journal
    import main as gd_main

    monkeypatch.setattr(gd_journal, "record", lambda *a, **kw: {})   # журнал машины не трогаем

    written: dict = {}
    monkeypatch.setattr(gd_main, "save_config", lambda data: written.update(data) or True)
    monkeypatch.setattr(gd_main, "config_path", lambda: tmp_path / "presets.json")
    (tmp_path / "presets.json").write_text("{}", encoding="utf-8")

    class Host:
        # перенос — метод окна, а статик у него общий с настоящим MainWindow
        _sanitize_presets = staticmethod(gd_main.MainWindow._sanitize_presets)

        def __init__(self):
            self.config = {"settings": {"language": "ru", "first_run_done": True,
                                        "theme": "dark"},
                           "presets": []}

    host = Host()
    ok, note = gd_main.MainWindow.import_app_settings(
        host, {"settings": {"language": "en", "theme": 42, "first_run_done": False}})

    assert ok, note
    settings = host.config["settings"]
    assert written, "конфиг не записан"
    assert settings["language"] == "en", "язык интерфейса — настройка, его переносят"
    assert settings["theme"] == "dark", "чужой тип (число вместо строки) не должен проходить"
    assert settings["first_run_done"] is True, "чужое состояние первого запуска не переносим"
    assert (tmp_path / "presets.before-import.json").exists(), "копия прежнего конфига не сделана"
