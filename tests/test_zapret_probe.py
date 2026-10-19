# -*- coding: utf-8 -*-
"""Проверка доступности целей: разбор targets.txt и сводка (без сети)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_zapret_probe as probe  # noqa: E402


def test_parse_targets_reads_file_and_skips_ping(tmp_path):
    utils = tmp_path / "utils"
    utils.mkdir()
    (utils / "targets.txt").write_text(
        '# comment\nDiscordMain = "https://discord.com"\n'
        'GoogleDNS = "PING:8.8.8.8"\n'
        'YouTube = "https://www.youtube.com"\n',
        encoding="utf-8")
    targets = probe.parse_targets(str(tmp_path))
    assert ("DiscordMain", "https://discord.com") in targets
    assert ("YouTube", "https://www.youtube.com") in targets
    assert all(not value.lower().startswith("ping:") for _, value in targets)


def test_parse_targets_falls_back_to_defaults(tmp_path):
    assert probe.parse_targets(str(tmp_path)) == list(probe.DEFAULT_TARGETS)
    assert probe.parse_targets(None) == list(probe.DEFAULT_TARGETS)


def test_probe_all_counts_and_averages(monkeypatch):
    def fake_probe(url, timeout=6.0):
        if "youtube" in url:
            return {"ok": False, "ms": 10.0, "error": "timeout"}
        return {"ok": True, "ms": 100.0, "error": None}

    monkeypatch.setattr(probe, "probe_url", fake_probe)
    result = probe.probe_all(None, max_workers=2)
    assert result["total"] == len(probe.DEFAULT_TARGETS)
    assert result["failed"] == 1
    assert result["passed"] == result["total"] - 1
    assert result["avg_ms"] == 100.0
    assert not result["ok"]


def test_describe_reports_failures():
    result = {
        "total": 2, "passed": 1, "failed": 1, "avg_ms": 50.0,
        "results": [{"name": "Discord", "ok": True}, {"name": "YouTube", "ok": False}],
    }
    text = probe.describe(result)
    assert "1" in text and "2" in text and "YouTube" in text
