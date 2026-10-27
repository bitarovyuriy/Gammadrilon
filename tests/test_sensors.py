# -*- coding: utf-8 -*-
"""Чтение файла замера от хелпера датчиков.

Файл пишет отдельный процесс под администратором, а читает окно обычным
пользователем — значит возможны и половина строки, и удалённый файл, и запись
с ошибкой. Всё это не должно ронять интерфейс: последний удачный замер остаётся.
"""

import json

import pytest

import gd_sensors


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Каждый тест — с чистого листа: и кэш, и выбранный канал VRM."""
    gd_sensors._last_good = {"cpu": None, "cpu_max": None, "vrm": None,
                             "board": [], "ok": False, "error": ""}
    gd_sensors.set_vrm_channel(0)
    yield
    gd_sensors.set_vrm_channel(0)


def _put(tmp_path, monkeypatch, payload) -> None:
    path = tmp_path / "sensors.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                    encoding="utf-8")
    monkeypatch.setattr(gd_sensors, "sample_path", lambda: path)


def test_read_parses_sample(tmp_path, monkeypatch):
    _put(tmp_path, monkeypatch, {"ok": True, "cpu": 61.5, "cpu_max": 70.25,
                                 "vrm": None, "board": ["Temperature #5=80.5"]})
    data = gd_sensors.read()
    assert data["ok"] is True
    assert data["cpu"] == 61.5
    assert data["cpu_max"] == 70.25
    assert gd_sensors.board_channels() == [("Temperature #5", 80.5)]


def test_vrm_from_named_sensor(tmp_path, monkeypatch):
    """Если прошивка платы назвала датчик VRM — берём его, канал не нужен."""
    _put(tmp_path, monkeypatch, {"ok": True, "cpu": 50.0, "vrm": 71.0, "board": []})
    assert gd_sensors._vrm_value(gd_sensors.read()) == 71.0


def test_vrm_from_chosen_channel(tmp_path, monkeypatch):
    _put(tmp_path, monkeypatch, {"ok": True, "cpu": 50.0, "vrm": None,
                                 "board": ["Temperature #3=44.0", "Temperature #5=81.0"]})
    assert gd_sensors._vrm_value(gd_sensors.read()) is None   # по умолчанию выключено
    gd_sensors.set_vrm_channel(5)
    assert gd_sensors._vrm_value(gd_sensors.read()) == 81.0
    gd_sensors.set_vrm_channel(2)                            # такого канала нет
    assert gd_sensors._vrm_value(gd_sensors.read()) is None


def test_broken_file_keeps_last_good(tmp_path, monkeypatch):
    _put(tmp_path, monkeypatch, {"ok": True, "cpu": 62.0, "vrm": None, "board": []})
    assert gd_sensors.read()["cpu"] == 62.0
    _put(tmp_path, monkeypatch, '{"ok": true, "cpu": 63')     # половина строки
    assert gd_sensors.read()["cpu"] == 62.0


def test_missing_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(gd_sensors, "sample_path", lambda: tmp_path / "нет.json")
    data = gd_sensors.read()
    assert data["cpu"] is None
    assert data["ok"] is False


def test_error_sample_reports_but_keeps_values(tmp_path, monkeypatch):
    """Хелпер ответил ошибкой (драйвер отвалился) — цифры прошлого тика не стираем."""
    _put(tmp_path, monkeypatch, {"ok": True, "cpu": 55.0, "vrm": None, "board": []})
    gd_sensors.read()
    _put(tmp_path, monkeypatch, {"ok": False, "error": "драйвер PawnIO не установлен"})
    data = gd_sensors.read()
    assert data["ok"] is False
    assert "PawnIO" in data["error"]
    assert data["cpu"] == 55.0


def test_board_channels_ignores_garbage(tmp_path, monkeypatch):
    _put(tmp_path, monkeypatch, {"ok": True, "board": ["Temperature #1=32", "мусор", "x="]})
    assert gd_sensors.board_channels() == [("Temperature #1", 32.0)]
