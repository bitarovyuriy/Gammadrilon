# -*- coding: utf-8 -*-
"""Клиент хелпера: обрыв и таймаут — ошибка в ответ, а не зависшее окно."""
from __future__ import annotations

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_helper  # noqa: E402


class DeadConnection:
    """Соединение, которое рвётся на отправке (компаньон упал)."""

    def send_bytes(self, _obj) -> None:
        raise OSError("соединение сброшено")

    def poll(self, _timeout=None) -> bool:  # noqa: D401
        return False

    def recv_bytes(self, maxlength):
        return b"{}"


class SilentConnection:
    """Соединение живое, но ответа нет — должен сработать таймаут."""

    def send_bytes(self, _obj) -> None:
        pass

    def poll(self, _timeout=None) -> bool:
        return False

    def recv_bytes(self, maxlength):
        return b"{}"

    def close(self):
        pass


class AnsweringConnection:
    def __init__(self, answer: dict) -> None:
        self.answer = answer

    def send_bytes(self, _obj) -> None:
        pass

    def poll(self, _timeout=None) -> bool:
        return True

    def recv_bytes(self, maxlength) -> bytes:
        return json.dumps(self.answer).encode("utf-8")


def make_client(conn):
    client = gd_helper.HelperClient()
    client._conn = conn
    return client


def test_lost_connection_is_reported():
    client = make_client(DeadConnection())

    result = client._call_connected({"cmd": "status"}, 5.0)

    assert result["ok"] is False
    assert "потеряна" in result["error"]
    assert client._conn is None, "мёртвое соединение сбрасывается"


def test_timeout_is_reported():
    client = make_client(SilentConnection())

    result = client._call_connected({"cmd": "status"}, 0.1)

    assert result["ok"] is False
    assert "таймаут" in result["error"]
    assert client._conn is None


def test_answer_is_returned_as_is():
    client = make_client(AnsweringConnection({"ok": True, "data": 42}))

    result = client._call_connected({"cmd": "status"}, 5.0)

    assert result == {"ok": True, "data": 42}
