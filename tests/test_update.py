# -*- coding: utf-8 -*-
"""Проверка обновлений: сравнение версий и честный отказ без сети.

Сеть подменена: тест не выходит наружу и не зависит от состояния GitHub.
"""
from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_update  # noqa: E402 — путь проекта добавлен выше


class _Response:
    """Минимальный ответ urlopen: контекстный менеджер и read()."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def test_parse_version_orders_numbers_not_text():
    """3.10 новее 3.9 — сравнение строк дало бы обратное."""
    assert gd_update.parse_version("v3.10") == (3, 10)
    assert gd_update.parse_version("3.1.0-beta") == (3, 1, 0)
    assert gd_update.parse_version("nightly") == ()


def test_is_newer():
    assert gd_update.is_newer("v3.10", "v3.9")
    assert gd_update.is_newer("v3.2", "")
    assert not gd_update.is_newer("v3.1", "v3.1")
    assert not gd_update.is_newer("nightly", "v3.1"), "непонятный тег не должен пугать"


def test_fetch_latest_parses_release(monkeypatch):
    body = json.dumps({
        "tag_name": "v9.9",
        "name": "v9.9",
        "html_url": "https://example.invalid/rel",
        "published_at": "2026-02-03T04:05:06Z",
    }).encode("utf-8")
    monkeypatch.setattr(gd_update.urllib.request, "urlopen", lambda *a, **kw: _Response(body))

    res = gd_update.fetch_latest(timeout=0.01)

    assert res["ok"] and res["tag"] == "v9.9"
    assert res["published"] == "2026-02-03"
    assert res["url"] == "https://example.invalid/rel"


def test_fetch_latest_speaks_human_without_network(monkeypatch):
    """Отказ сети — это dict с текстом для человека, а не исключение."""

    def boom(*a, **kw):
        raise urllib.error.URLError("нет маршрута")

    monkeypatch.setattr(gd_update.urllib.request, "urlopen", boom)
    res = gd_update.fetch_latest(timeout=0.01)

    assert not res["ok"] and "нет связи" in res["error"]


def test_fetch_latest_reports_missing_tag(monkeypatch):
    monkeypatch.setattr(gd_update.urllib.request, "urlopen",
                        lambda *a, **kw: _Response(b'{"name": "release"}'))

    res = gd_update.fetch_latest(timeout=0.01)

    assert not res["ok"] and "тег" in res["error"]


def test_due_respects_daily_interval():
    """Тихая проверка — раз в сутки: свежая метка молчит, старая просит сеть."""
    now = 1_000_000.0
    assert gd_update.due(None, now=now), "без метки проверять надо"
    assert gd_update.due(0, now=now)
    assert gd_update.due("вчера", now=now), "битая метка — как отсутствие проверки"
    assert not gd_update.due(now, now=now), "только что проверяли — рано"
    assert not gd_update.due(now - gd_update.CHECK_INTERVAL + 60, now=now)
    assert gd_update.due(now - gd_update.CHECK_INTERVAL, now=now), "ровно сутки — пора"
    assert gd_update.due(now - gd_update.CHECK_INTERVAL * 3, now=now)


def test_stamp_is_float_seconds():
    value = gd_update.stamp(now=1234.5)
    assert isinstance(value, float) and value == 1234.5
    assert gd_update.stamp() > 1_600_000_000, "метка — это секунды Unix, а не миллисекунды"


def test_silent_check_is_throttled_by_stamp(monkeypatch):
    """Второй запуск в тот же день не должен стучаться в сеть — ручной может."""
    import main as gd_main

    calls: list = []
    monkeypatch.setattr(gd_update, "fetch_latest", lambda *a, **kw: calls.append(1))

    class Host:
        # метод окна зовёт gd_bg.submit — подменяем его на «просто позвать»
        def __init__(self, stamp):
            self.config = {"settings": {"check_updates": True, "updates_last_check": stamp}}

        def _set_update_status(self, *args, **kwargs):
            pass

    class Bg:
        @staticmethod
        def is_running(worker):
            return False

        @staticmethod
        def submit(fn, done=None, parent=None):
            calls.append(1)
            return object()

    import gd_bg
    monkeypatch.setattr(gd_bg, "is_running", Bg.is_running)
    monkeypatch.setattr(gd_bg, "submit", Bg.submit)

    fresh = Host(gd_update.stamp())
    gd_main.MainWindow.check_updates(fresh, silent=True)
    assert not calls, "тихая проверка со свежей меткой не должна идти в сеть"

    stale = Host(gd_update.stamp() - gd_update.CHECK_INTERVAL - 1)
    gd_main.MainWindow.check_updates(stale, silent=True)
    assert calls, "со старой меткой тихая проверка обязана состояться"

    calls.clear()
    gd_main.MainWindow.check_updates(fresh, silent=False)
    assert calls, "ручная проверка не тормозится меткой"
