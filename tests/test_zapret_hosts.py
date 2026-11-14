"""An interrupted hosts download cannot become a privileged write."""
import ctypes
import os
import sys
import threading
from io import BytesIO
from multiprocessing import Pipe
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gd_gpu_service as service  # noqa: E402
import gd_helper  # noqa: E402
import gd_zapret_update as upd  # noqa: E402


@pytest.mark.parametrize("raw,length,valid", [
    (b"1.2.3.4 example.com", "19", True),
    (b"short", "10", False),
    (b"<html>error", None, False),
    (b"x" * (upd.HOSTS_MAX_BYTES + 1), None, False),
], ids=["official", "truncated", "html", "oversize"])
def test_hosts_preparation_uses_only_fixed_source(monkeypatch, raw, length, valid):
    class Response(BytesIO):
        headers = {"Content-Length": length}

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == upd.HOSTS_URL
            assert timeout == upd.NET_TIMEOUT
            return Response(raw)

    def build_opener(handler):
        with pytest.raises(ValueError, match="unexpected hosts redirect"):
            handler.redirect_request(None, None, 302, "", {}, "https://example.net/hosts")
        return Opener()

    monkeypatch.setattr(upd.urllib.request, "build_opener", build_opener)
    if valid:
        assert upd.fetch_hosts_content() == raw.decode()
    else:
        with pytest.raises(ValueError):
            upd.fetch_hosts_content()


@pytest.fixture
def connect():
    sessions = []

    def new():
        client_end, service_end = Pipe()
        thread = threading.Thread(target=service.serve_client, args=(service_end,), daemon=True)
        sessions.append((client_end, thread))
        thread.start()
        return client_end

    yield new

    for end, thread in sessions:
        end.close()
        thread.join(2)
        assert not thread.is_alive()


@pytest.mark.parametrize("cancel", ["shutdown", "timeout"])
def test_hosts_prepare_does_not_hold_operation_lock_or_apply_on_close(monkeypatch, connect, cancel):
    started, release, closing = threading.Event(), threading.Event(), threading.Event()
    applied = []

    def fetch():
        started.set()
        assert release.wait(3)
        return "1.2.3.4 example.com"

    def check_shutdown():
        if closing.is_set():
            raise InterruptedError("application is closing")

    monkeypatch.setattr(upd, "fetch_hosts_content", fetch)
    monkeypatch.setattr(upd, "apply_hosts", applied.append)
    client = gd_helper.HelperClient()
    original = client._conn = connect()
    client.token = "00"

    def open_session(session, port, token):
        session._conn = connect()
        return True

    monkeypatch.setattr(gd_helper.HelperClient, "_connect", open_session)
    if cancel == "timeout":
        original_call = gd_helper.HelperClient._call_connected_locked

        def short_prepare(session, obj, timeout, **kwargs):
            return original_call(session, obj, 0.2 if obj.get("op") == "hosts_prepare" else timeout, **kwargs)

        monkeypatch.setattr(gd_helper.HelperClient, "_call_connected_locked", short_prepare)
    result = []
    worker = threading.Thread(target=lambda: result.append(client.apply_zapret_hosts(check_shutdown)), daemon=True)
    worker.start()
    try:
        assert started.wait(2)
        assert service._OP_LOCK.acquire(blocking=False)
        service._OP_LOCK.release()
        assert client.call({"cmd": "ping"}, timeout=1)["ok"]
        if cancel == "shutdown":
            closing.set()
        worker.join(1)
        assert not worker.is_alive()
        expected_error = ("InterruptedError: application is closing" if cancel == "shutdown"
                          else gd_helper.i18n.t("таймаут ответа хелпера"))
        assert result == [{"ok": False, "error": expected_error}]
        assert client._conn is original
        assert client.call({"cmd": "ping"}, timeout=1)["ok"]
    finally:
        release.set()
        worker.join(2)
    assert not applied


def test_hosts_apply_requires_preparation_on_same_connection(monkeypatch, connect, tmp_path):
    content = "1.2.3.4 example.com"
    monkeypatch.setattr(upd, "fetch_hosts_content", lambda: content)
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    hosts = tmp_path / "System32/drivers/etc/hosts"
    hosts.parent.mkdir(parents=True)
    hosts.write_text("# local entry\n", encoding="utf-8")
    monkeypatch.setattr(upd, "HOSTS_BAK_DIR", tmp_path / "backup")

    def call(conn, op, **extra):
        gd_helper.send_message(conn, {"cmd": "zapret_update", "op": op, **extra})
        assert conn.poll(2)
        return gd_helper.recv_message(conn)

    conn, other = connect(), connect()
    assert call(conn, "hosts_prepare")["ok"]
    assert not call(other, "hosts")["ok"]
    assert not call(conn, "hosts", content="6.6.6.6 example.net")["ok"]
    assert hosts.read_text(encoding="utf-8") == "# local entry\n"

    assert call(conn, "hosts_prepare")["ok"]
    result = call(conn, "hosts")
    assert result["ok"]
    backup = Path(result["data"]["backup"])
    assert backup.read_text(encoding="utf-8") == "# local entry\n"
    assert hosts.read_text(encoding="utf-8").splitlines() == [
        "# local entry", "", upd.HOSTS_BEGIN, content, upd.HOSTS_END,
    ]
    assert not call(conn, "hosts")["ok"]

    updated = "5.6.7.8 example.com"
    monkeypatch.setattr(upd, "fetch_hosts_content", lambda: updated)
    assert call(other, "hosts_prepare")["ok"]
    second = call(other, "hosts")
    assert second["ok"]
    assert second["data"]["backup"] != str(backup)
    assert backup.read_text(encoding="utf-8") == "# local entry\n"
    assert hosts.read_text(encoding="utf-8").splitlines() == [
        "# local entry", "", upd.HOSTS_BEGIN, updated, upd.HOSTS_END,
    ]


@pytest.fixture
def hosts_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    hosts = tmp_path / "System32/drivers/etc/hosts"
    hosts.parent.mkdir(parents=True)
    monkeypatch.setattr(upd, "HOSTS_BAK_DIR", tmp_path / "backup")
    return hosts


@pytest.mark.parametrize("current", [None, b""], ids=["missing", "empty"])
def test_hosts_creation_preserves_absence_or_empty_backup(hosts_file, current):
    if current is not None:
        hosts_file.write_bytes(current)
    result = upd.apply_hosts("1.2.3.4 example.com")
    assert result["ok"]
    assert b"1.2.3.4 example.com" in hosts_file.read_bytes()
    if current is None:
        assert result["backup"] is None
    else:
        assert Path(result["backup"]).read_bytes() == current


@pytest.mark.parametrize("markers", [
    [upd.HOSTS_BEGIN], [upd.HOSTS_END, upd.HOSTS_BEGIN],
    [upd.HOSTS_BEGIN, upd.HOSTS_BEGIN, upd.HOSTS_END],
])
def test_hosts_rejects_unpaired_or_duplicate_marker_lines(hosts_file, markers):
    original = "\n".join(markers).encode()
    hosts_file.write_bytes(original)
    assert not upd.apply_hosts("1.2.3.4 example.com")["ok"]
    assert hosts_file.read_bytes() == original


def test_hosts_replace_preserves_untouched_bytes_and_backup(hosts_file):
    prefix = f"# mentions {upd.HOSTS_BEGIN}\r\n".encode()
    start, end = upd.HOSTS_BEGIN.encode(), upd.HOSTS_END.encode()
    original = prefix + b"\r\n".join((start, b"old", end)) + b"\r\n# non-UTF8: \x96\n"
    hosts_file.write_bytes(original)
    result = upd.apply_hosts("1.2.3.4 example.com")
    assert result["ok"]
    assert hosts_file.read_bytes() == original.replace(b"old", b"1.2.3.4 example.com")
    assert Path(result["backup"]).read_bytes() == original


def test_hosts_replace_failure_keeps_original_and_retains_backup(monkeypatch, hosts_file):
    original = b"# local\r\n"
    hosts_file.write_bytes(original)

    def fail_replace(target, staged, existed):
        assert existed and target == hosts_file and target.read_bytes() == original
        assert b"1.2.3.4 example.com" in staged.read_bytes()
        raise PermissionError("replacement blocked")

    monkeypatch.setattr(upd, "_install_hosts", fail_replace)
    result = upd.apply_hosts("1.2.3.4 example.com")
    assert not result["ok"]
    assert hosts_file.read_bytes() == Path(result["backup"]).read_bytes() == original
    assert list(hosts_file.parent.iterdir()) == [hosts_file]


def test_hosts_rejects_external_edit_before_install(monkeypatch, hosts_file):
    original, external = b"# local\r\n", b"# external editor\r\n"
    hosts_file.write_bytes(original)
    prepare = upd._replace_managed_block

    def edit_after_snapshot(snapshot, content):
        hosts_file.write_bytes(external)
        return prepare(snapshot, content)

    monkeypatch.setattr(upd, "_replace_managed_block", edit_after_snapshot)
    result = upd.apply_hosts("1.2.3.4 example.com")
    assert not result["ok"]
    assert upd.i18n.t("hosts изменился во время подготовки. Повторите обновление.") in result["error"]
    assert hosts_file.read_bytes() == external
    assert Path(result["backup"]).read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="simulates Windows ReplaceFileW partial failure")
@pytest.mark.parametrize("rename_fails", [False, True])
def test_hosts_recovers_or_reports_original_after_win32_partial_failure(monkeypatch, hosts_file, rename_fails):
    original = b"# local\r\n"
    hosts_file.write_bytes(original)
    retained = []
    original_rename = os.rename

    def replace_file(target, staged, backup, *_args):
        original_rename(target, backup)
        retained.append(Path(backup))
        ctypes.set_last_error(1177)
        return 0

    class Kernel:
        ReplaceFileW = staticmethod(replace_file)

    def rename(src, dst):
        if rename_fails:
            raise PermissionError("recovery blocked")
        original_rename(src, dst)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: Kernel())
    monkeypatch.setattr(upd.os, "rename", rename)
    result = upd.apply_hosts("1.2.3.4 example.com")
    assert not result["ok"]
    assert Path(result["backup"]).read_bytes() == original
    assert len(retained) == 1
    if rename_fails:
        assert not hosts_file.exists()
        assert retained[0].read_bytes() == original
        assert str(retained[0]) in result["error"]
    else:
        assert hosts_file.read_bytes() == original
        assert not retained[0].exists()


def test_hosts_commit_waits_for_reply_even_after_shutdown(monkeypatch, connect):
    started, release, closing, done = (threading.Event() for _ in range(4))
    client = gd_helper.HelperClient()
    client._conn, client.token = connect(), "00"
    monkeypatch.setattr(upd, "fetch_hosts_content", lambda: "1.2.3.4 example.com")

    def open_session(session, port, token):
        session._conn = connect()
        return True

    def apply(content):
        started.set()
        assert release.wait(3)
        return {"ok": True, "backup": "retained"}

    def check_shutdown():
        if closing.is_set():
            raise InterruptedError("application is closing")

    monkeypatch.setattr(gd_helper.HelperClient, "_connect", open_session)
    monkeypatch.setattr(upd, "apply_hosts", apply)
    results = []

    def perform():
        results.append(client.apply_zapret_hosts(check_shutdown))
        done.set()

    worker = threading.Thread(target=perform, daemon=True)
    worker.start()
    try:
        assert started.wait(2)
        closing.set()
        assert not done.wait(0.15)
    finally:
        release.set()
        worker.join(2)
    assert results == [{"ok": True, "data": {"ok": True, "backup": "retained"}, "error": None}]


def test_hosts_commit_lost_reply_reports_unknown_outcome(monkeypatch, connect):
    client = gd_helper.HelperClient()
    client._conn, client.token = connect(), "00"
    monkeypatch.setattr(upd, "fetch_hosts_content", lambda: "1.2.3.4 example.com")
    monkeypatch.setattr(upd, "apply_hosts", lambda content: {"ok": True})

    def open_session(session, port, token):
        session._conn = connect()
        return True

    real_receive = gd_helper.recv_message
    calls = 0

    def receive(conn):
        nonlocal calls
        calls += 1
        result = real_receive(conn)
        if calls == 2:
            raise EOFError
        return result

    monkeypatch.setattr(gd_helper.HelperClient, "_connect", open_session)
    monkeypatch.setattr(gd_helper, "recv_message", receive)
    result = client.apply_zapret_hosts(lambda: None)
    assert result["ok"] is None
    assert result["error"] == gd_helper.i18n.t("Связь с помощником потеряна. Результат записи hosts неизвестен.")
