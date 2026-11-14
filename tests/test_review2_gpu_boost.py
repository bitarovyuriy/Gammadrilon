# -*- coding: utf-8 -*-
"""Остатки ревью 3.6.3: исходный PL после частичного отката и PID в бусте."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_boost  # noqa: E402
import gd_gpu_service  # noqa: E402

GPU_UUID = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

# --- GPU: частичный откат не должен стирать исходный лимит -----------------

def test_apply_remembers_pl_after_partial_restore(monkeypatch):
    monkeypatch.setattr(gd_gpu_service.gd_gpu, "read_state",
                        lambda: {"uuid": GPU_UUID, "pl": 340.0, "pl_min": 100.0, "pl_max": 366.0})
    monkeypatch.setattr(gd_gpu_service, "_set_power", lambda uuid, pl: (True, ""))
    monkeypatch.setattr(gd_gpu_service, "_save_snapshot", lambda original, applied: True)
    monkeypatch.setattr(gd_gpu_service, "_set_keep_on_exit", lambda value: None)
    # состояние после неудачного отката: мощность вернулась, частоты — нет
    monkeypatch.setitem(gd_gpu_service.__dict__, "_original",
                        {"pl": None, "uuid": GPU_UUID})
    monkeypatch.setitem(gd_gpu_service.__dict__, "_applied", {"lock_clocks": [900, 1200]})

    ok, _msg = gd_gpu_service._apply(340.0, None, keep_on_exit=False)

    assert ok is True
    assert gd_gpu_service._original == {"pl": 340.0, "uuid": GPU_UUID}


def test_restore_skips_pl_when_it_was_already_returned(monkeypatch):
    calls = []
    monkeypatch.setitem(gd_gpu_service.__dict__, "_original", {"pl": None, "uuid": GPU_UUID})
    monkeypatch.setitem(gd_gpu_service.__dict__, "_applied", {})
    monkeypatch.setattr(gd_gpu_service, "_set_power", lambda uuid, pl: calls.append(pl) or (True, ""))
    monkeypatch.setattr(gd_gpu_service, "_clear_snapshot", lambda: True)
    monkeypatch.setattr(gd_gpu_service, "_save_snapshot", lambda original, applied: True)

    ok, _msg = gd_gpu_service._restore()

    assert ok is True
    assert calls == []          # мощность не трогаем: она уже исходная


# --- Буст: восстановление только для того же процесса -----------------------

def test_same_process_detects_current_pid():
    current = os.getpid()
    created = gd_boost._process_created(current)

    assert created is not None
    assert gd_boost._same_process(current, created) is True


def test_same_process_rejects_reused_pid():
    created = gd_boost._process_created(os.getpid())

    assert gd_boost._same_process(os.getpid(), (created or 0) + 100) is False


def test_same_process_handles_dead_process():
    # PID, которого заведомо нет: заведомо больше любого живого
    assert gd_boost._same_process(999_999, 1.0) is False


def test_process_created_is_real_time():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        created = gd_boost._process_created(proc.pid)
        assert created is not None and created > 0
    finally:
        proc.kill()
        proc.wait(5)
