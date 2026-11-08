# -*- coding: utf-8 -*-
"""Обновление списков обхода: адреса raw разрешены только своему репозиторию.

Ограничение «только releases/download» сломало скачивание `ipset-service.txt`
и `hosts`: обе загрузки отклонялись до сетевого запроса.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_zapret_update as upd  # noqa: E402


def test_own_raw_lists_are_trusted():
    assert upd.trusted_list_url(upd.IPSET_URL)
    assert upd.trusted_list_url(upd.HOSTS_URL)
    assert upd.trusted_list_url(upd.VERSION_URL)


def test_foreign_raw_addresses_are_rejected():
    for url in (
        "https://raw.githubusercontent.com/evil/zapret/refs/heads/main/hosts",
        "https://raw.githubusercontent.com/Flowseal/other/refs/heads/main/x.txt",
        "http://raw.githubusercontent.com/Flowseal/zapret-discord-youtube/refs/heads/main/x",
        "https://raw.githubusercontent.com.evil.example/x",
    ):
        assert not upd.trusted_list_url(url), url


def test_release_addresses_still_work():
    url = "https://github.com/Flowseal/zapret-discord-youtube/releases/download/v1/x.zip"
    assert upd.trusted_release_url(url)
    assert not upd.trusted_list_url(url)


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._payload)
        chunk, self._payload = self._payload[:size], self._payload[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.opened = []

    def open(self, req, timeout=None):
        self.opened.append(req.full_url)
        return _FakeResponse(self._payload)

    def add_handler(self, handler):    # вызывает build_opener
        pass


def test_download_accepts_list_url(monkeypatch, tmp_path):
    opener = _FakeOpener(b"1.2.3.4/32\n")
    monkeypatch.setattr(upd.urllib.request, "build_opener", lambda *a, **k: opener)
    target = tmp_path / "ipset-all.txt"

    ok, msg = upd.download(upd.IPSET_URL, target)

    assert ok, msg
    assert target.read_bytes() == b"1.2.3.4/32\n"
    assert opener.opened == [upd.IPSET_URL]


def test_download_rejects_foreign_list(monkeypatch, tmp_path):
    opener = _FakeOpener(b"data")
    monkeypatch.setattr(upd.urllib.request, "build_opener", lambda *a, **k: opener)
    target = tmp_path / "x.txt"

    ok, msg = upd.download("https://raw.githubusercontent.com/evil/hosts", target)

    assert not ok
    assert opener.opened == []          # до сети дело не дошло
    assert not target.exists()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://example.com/x.zip"])
def test_download_rejects_other_schemes(monkeypatch, tmp_path, url):
    ok, _ = upd.download(url, tmp_path / "y.txt")
    assert not ok
