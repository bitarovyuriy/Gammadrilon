# -*- coding: utf-8 -*-
"""Проверки измерения bufferbloat без внешнего трафика."""

import io
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import gd_extra
from gd_extra_ui import ExtraPanel


def test_download_sets_user_agent_signals_start_and_respects_limit(monkeypatch):
    def urlopen(request, *, timeout):
        assert request.full_url == gd_extra.LOAD_URL
        assert request.get_header("User-agent") == "Gammadrilon"
        assert timeout == gd_extra.LOAD_TIMEOUT
        return io.BytesIO(b"x" * 200)

    monkeypatch.setattr(gd_extra.urllib.request, "urlopen", urlopen)
    progress = []

    assert gd_extra._download(gd_extra.LOAD_URL, 123, threading.Event(), progress.append) == ""
    assert progress == [123]


def test_bufferbloat_counts_only_probes_with_transfer_progress(monkeypatch):
    probe_started = threading.Event()
    next_chunk = threading.Event()
    probes = 0

    def download(url, size_limit, stop, on_progress):
        on_progress(100_000)
        assert probe_started.wait(timeout=2)
        on_progress(200_000)
        next_chunk.set()
        assert stop.wait(timeout=2)
        return ""

    def tcp_latency(host, port, timeout):
        nonlocal probes
        probes += 1
        if probes == 6:
            probe_started.set()
            assert next_chunk.wait(timeout=2)
            return 40.0
        return 5.0

    monkeypatch.setattr(gd_extra, "_download", download)
    monkeypatch.setattr(gd_extra, "tcp_latency", tcp_latency)

    result = gd_extra.bufferbloat(samples=3)

    assert probes == 8
    assert result["idle"] == 5.0
    assert result["loaded"] == 40.0
    assert result["increase"] == 35.0
    assert result["grade"] == "B"


@pytest.mark.parametrize("result", [(0, "FileNotFoundError: TLS"), (gd_extra.LOAD_BYTES, "")])
def test_bufferbloat_has_no_loaded_measurement_if_download_already_ended(monkeypatch, result):
    def download(url, size_limit, stop, on_progress):
        if result[0]:
            on_progress(result[0])
        return result[1]

    monkeypatch.setattr(gd_extra, "tcp_latency", lambda *args: 5.0)
    monkeypatch.setattr(gd_extra, "_download", download)

    measured = gd_extra.bufferbloat()

    assert measured["idle"] == 5.0
    assert measured["loaded"] is None
    assert measured["increase"] is None
    assert measured["grade"] == "—"
    assert (measured["load_bytes"], measured["load_error"]) == result


def test_bufferbloat_reports_stalled_download_without_grade(monkeypatch):
    stalled = threading.Event()
    probes_done = threading.Event()
    probes = 0

    class StallingResponse(io.BytesIO):
        def read1(self, size):
            chunk = super().read1(size)
            if chunk:
                return chunk
            stalled.set()
            assert probes_done.wait(timeout=2)
            raise TimeoutError("download stalled")

    def tcp_latency(host, port, timeout):
        nonlocal probes
        probes += 1
        if probes > 5:
            assert stalled.wait(timeout=2)
        if probes == 8:
            probes_done.set()
        return 5.0

    monkeypatch.setattr(
        gd_extra.urllib.request, "urlopen", lambda *args, **kwargs: StallingResponse(b"x" * 131_072),
    )
    monkeypatch.setattr(gd_extra, "tcp_latency", tcp_latency)

    result = gd_extra.bufferbloat(samples=3)

    assert result["load_bytes"] == 131_072
    assert result["load_error"] == "TimeoutError: download stalled"
    assert result["increase"] is None
    assert result["grade"] == "—"

    panel = SimpleNamespace(btn_bb=Mock(), lbl_net=object(), _set_status=Mock())
    ExtraPanel._bb_done(panel, {"bufferbloat": result})
    assert panel._set_status.call_args.kwargs == {"ok": False}
    message = panel._set_status.call_args.args[1]
    assert "128" in message and "TimeoutError: download stalled" in message
