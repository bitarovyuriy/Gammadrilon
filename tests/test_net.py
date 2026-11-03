# -*- coding: utf-8 -*-
"""Сетевой слой (gd_net): перебор маршрутов и запоминание рабочего.

Проверяем на настоящем локальном HTTP-сервере: подменять urlopen бессмысленно —
именно цепочка opener'ов и есть то, что нужно проверить.
"""
from __future__ import annotations

import http.server
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_net  # noqa: E402 — путь проекта добавлен выше


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — имя задаёт http.server
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D102 — не засорять вывод тестов
        pass


def _server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_open_url_goes_direct_and_remembers_route():
    gd_net.reset()
    srv = _server()
    try:
        url = f"http://127.0.0.1:{srv.server_port}/x"
        with gd_net.open_url(urllib.request.Request(url), timeout=5) as response:
            assert response.read() == b"ok"
        assert gd_net.working_route() == "", "прямое соединение должно быть первым"
    finally:
        srv.shutdown()


def test_dead_route_falls_back_to_direct():
    """Если запомненный маршрут умер, запрос не должен падать."""
    srv = _server()
    try:
        gd_net._working = "http://127.0.0.1:9"  # порт закрыт: соединение отвергнуто
        url = f"http://127.0.0.1:{srv.server_port}/x"
        with gd_net.open_url(urllib.request.Request(url), timeout=5) as response:
            assert response.read() == b"ok"
        assert gd_net.working_route() == "", "после отказа маршрут должен смениться"
    finally:
        gd_net.reset()
        srv.shutdown()
