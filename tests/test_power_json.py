# -*- coding: utf-8 -*-
"""Канал питания говорит на JSON, а не на pickle.

`multiprocessing.connection.send/recv` десериализует pickle: процесс, занявший
имя канала раньше приложения, мог вернуть исполняемый объект. Обмен переведён
на ограниченный JSON — тот же протокол, что у GPU-помощника.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))

import gd_helper  # noqa: E402


def test_message_roundtrip_keeps_data():
    payload = {"ok": True, "data": {"pl": 300, "name": "RTX", "f": 3.5}}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    assert json.loads(encoded.decode("utf-8")) == payload


def test_too_large_message_is_refused():
    class FakeConn:
        def send_bytes(self, data):
            raise AssertionError("не должно дойти до отправки")

    with pytest.raises(ValueError):
        gd_helper.send_message(FakeConn(), {"x": "a" * (gd_helper.MAX_MESSAGE_BYTES + 10)})


def test_non_object_message_is_refused():
    class FakeConn:
        def recv_bytes(self, limit):
            return b'[1, 2, 3]'

    with pytest.raises(ValueError):
        gd_helper.recv_message(FakeConn())


@pytest.mark.parametrize("name", ["gd_power_ui.py", "gd_power_service.py"])
def test_channel_does_not_use_pickle_send_recv(name):
    source = (ROOT / name).read_text(encoding="utf-8")
    # ищем именно вызовы Connection.send/recv без _bytes
    assert not re.search(r"\bconn\.send\(", source), name
    assert not re.search(r"\bconn\.recv\(", source), name
    assert "send_message" in source and "recv_message" in source


def test_live_pipe_exchange_uses_json():
    """Живой обмен по named pipe: клиент и сервер понимают друг друга."""
    from multiprocessing.connection import Client, Listener

    address = rf"\\.\pipe\gd_json_test_{uuid.uuid4().hex[:10]}"
    ready = threading.Event()

    def server():
        with Listener(address) as listener:
            ready.set()
            conn = listener.accept()
            try:
                msg = gd_helper.recv_message(conn)
                gd_helper.send_message(conn, {"ok": True, "echo": msg.get("cmd")})
            finally:
                conn.close()

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    assert ready.wait(5)

    conn = Client(address)
    try:
        gd_helper.send_message(conn, {"cmd": "status"})
        assert conn.poll(5)
        assert gd_helper.recv_message(conn) == {"ok": True, "echo": "status"}
    finally:
        conn.close()
    thread.join(5)
