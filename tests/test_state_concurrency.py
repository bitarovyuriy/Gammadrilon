"""Concurrent writers each commit their own complete state file."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import gd_state


def test_simultaneous_writes_each_commit(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    both_ready = Barrier(2)
    sync = gd_state.os.fsync

    def concurrent_sync(fd):
        sync(fd)
        both_ready.wait(timeout=5)

    monkeypatch.setattr(gd_state.os, "fsync", concurrent_sync)
    with ThreadPoolExecutor(2) as workers:
        results = list(workers.map(lambda value: gd_state.write_json_atomic(path, value), [1, 2]))

    assert results == [True, True]
    assert gd_state.read_json(path) in (1, 2)
    assert list(tmp_path.iterdir()) == [path]


def test_recovery_never_moves_a_concurrent_valid_write(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{bad", encoding="utf-8")
    parsing, pause, written = Event(), Event(), Event()
    parse = gd_state.json.loads

    def delayed_parse(text):
        if text == "{bad":
            parsing.set()
            assert pause.wait(5)
        return parse(text)

    def write():
        result = gd_state.write_json_atomic(path, {"fresh": True})
        written.set()
        return result

    monkeypatch.setattr(gd_state.json, "loads", delayed_parse)
    with ThreadPoolExecutor(2) as workers:
        reading = workers.submit(gd_state.read_json_with_recovery, path)
        try:
            assert parsing.wait(5)
            writing = workers.submit(write)
            assert not written.wait(0.1)
        finally:
            pause.set()
        assert reading.result(timeout=5) == (None, "backup")
        assert writing.result(timeout=5)

    assert gd_state.read_json(path) == {"fresh": True}
    assert path.with_suffix(".json.bak").read_text(encoding="utf-8") == "{bad"
