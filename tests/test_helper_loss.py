# -*- coding: utf-8 -*-
"""Клиент хелпера: обрыв и таймаут — ошибка в ответ, а не зависшее окно."""
from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest

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

    def close(self):
        pass


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


@pytest.mark.parametrize(("command", "connection"), [
    ("apply", SilentConnection),
    ("restore", DeadConnection),
])
def test_gpu_write_is_unconfirmed_if_helper_does_not_reply(command, connection):
    client = make_client(connection())
    result = client._call_connected({"cmd": command}, 0.1)
    assert result["ok"] is None
    assert "неизвестен" in result["error"]
    assert client._conn is None

def test_helper_launch_is_serialized_only_for_matching_endpoints(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    session = tmp_path / "session.json"
    first = gd_helper.HelperClient(session_file=session)
    second = gd_helper.HelperClient(session_file=session)
    other = gd_helper.HelperClient(port=47319, session_file=tmp_path / "other.json")
    starting = Event()
    release = Event()
    second_call = Event()
    launches = []
    available = {}

    def launch(port, token):
        launches.append(port)
        if port == gd_helper.TCP_PORT and not starting.is_set():
            starting.set()
            assert release.wait(5)
        available[port] = token

    def connect(client, port, token):
        if available.get(port) != token:
            return False
        client._conn = object()
        return True

    for client in (first, second, other):
        monkeypatch.setattr(client, "_launch", launch)
        monkeypatch.setattr(client, "_connect", lambda p, t, c=client: connect(c, p, t))

    def ensure_second():
        second_call.set()
        return second.ensure()

    with ThreadPoolExecutor(max_workers=3) as workers:
        first_result = workers.submit(first.ensure)
        try:
            assert starting.wait(5)
            assert not session.exists()
            second_result = workers.submit(ensure_second)
            assert second_call.wait(5)
            assert workers.submit(other.ensure).result(timeout=2)
            assert launches == [gd_helper.TCP_PORT, other.port]
        finally:
            release.set()
        assert first_result.result(timeout=5)
        assert second_result.result(timeout=5)

    assert launches == [gd_helper.TCP_PORT, other.port]
