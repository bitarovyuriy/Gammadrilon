"""Shutdown detaches read-only I/O, but must still wait for mutations."""
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt5.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gd_bg  # noqa: E402
import gd_autoupdate as autoupdate  # noqa: E402
import gd_zapret_update as zapret_update  # noqa: E402


@pytest.mark.parametrize(
    "gpu_session, gpu_reply, zapret_session, fallback_session, expected_client, errors",
    [
        (True, {"ok": True, "data": "bye"}, True, True, "gpu", []),
        (True, {"ok": False, "data": "bye", "error": "rollback failed"},
         True, True, "gpu", ["rollback failed"]),
        (True, {"ok": None, "error": "lost"}, True, True, "zapret", []),
        (True, {"ok": True}, True, True, "zapret", []),
        (True, {"ok": None, "error": "lost"}, False, False, None, ["lost"]),
        (False, {}, False, True, "fallback", []),
        (False, {}, False, False, None, []),
    ],
)
def test_rollback_shuts_down_the_shared_helper_once(
    monkeypatch, gpu_session, gpu_reply, zapret_session, fallback_session, expected_client, errors,
):
    import gd_helper
    import gd_journal
    import gd_metrics
    import main

    goodbye = {"ok": True, "data": "bye"}
    power = Mock()
    power.shutdown.return_value = {"ok": True}
    clients = {"gpu": Mock(), "zapret": Mock(), "fallback": Mock()}
    for key, available, response in (
        ("gpu", gpu_session, gpu_reply), ("zapret", zapret_session, goodbye),
        ("fallback", fallback_session, goodbye),
    ):
        clients[key].has_session.return_value = available
        clients[key].shutdown.return_value = response

    monkeypatch.setattr(gd_helper, "HelperClient", lambda: clients["fallback"])
    monkeypatch.setattr(gd_journal, "record", Mock())
    monkeypatch.setattr(gd_metrics, "stop", Mock())
    window = SimpleNamespace(
        _power_page=SimpleNamespace(client=power),
        _gpu_page=SimpleNamespace(client=clients["gpu"], keep_on_exit=lambda: True),
        _zapret_page=SimpleNamespace(client=clients["zapret"]),
        _restore_before_shutdown=lambda: (True, ""),
        _ddc=None,
        gamma_controller=Mock(),
    )

    assert main.MainWindow._cleanup_for_exit(window) == {"ok": not errors, "errors": errors}
    power.shutdown.assert_called_once_with(restore=True)
    if gpu_session:
        clients["gpu"].shutdown.assert_called_once_with(restore=False)
    else:
        clients["gpu"].shutdown.assert_not_called()
    for name in ("zapret", "fallback"):
        if expected_client == name:
            clients[name].shutdown.assert_called_once_with(restore=False)
        else:
            clients[name].shutdown.assert_not_called()


@pytest.mark.parametrize("keep_gpu", [True, False])
def test_shutdown_uses_saved_gpu_setting_without_gpu_page(monkeypatch, keep_gpu):
    import gd_helper
    import gd_metrics
    import main

    helper = Mock()
    helper.has_session.return_value = True
    helper.shutdown.return_value = {"ok": True, "data": "bye"}
    monkeypatch.setattr(gd_helper, "HelperClient", lambda: helper)
    monkeypatch.setattr(gd_metrics, "stop", Mock())
    window = SimpleNamespace(
        config={"settings": {"gpu_keep_on_exit": keep_gpu}},
        _restore_before_shutdown=lambda: (True, ""),
        _ddc=None,
        gamma_controller=Mock(),
    )

    assert main.MainWindow._cleanup_for_exit(window) == {"ok": True, "errors": []}
    helper.shutdown.assert_called_once_with(restore=not keep_gpu)


@pytest.mark.parametrize("close_error", [False, True])
def test_transfer_checks_response_close_before_committing(monkeypatch, tmp_path, close_error):
    monkeypatch.setattr(gd_bg, "_SHUTDOWN", threading.Event())
    url = "https://github.com/example/repo/releases/download/v1/release.zip"
    destination = tmp_path / "release.zip"
    destination.write_bytes(b"previous")
    response_closed = threading.Event()

    class Response(BytesIO):
        headers = {"Content-Length": "8"}

        def geturl(self):
            return url

        def __exit__(self, *args):
            super().__exit__(*args)
            response_closed.set()
            if close_error:
                raise OSError("response close failed")

    monkeypatch.setattr(autoupdate.gd_net, "open_url",
                        lambda *_args, **_kwargs: Response(b"complete"))

    def download():
        autoupdate.download_asset({"url": url, "size": 8}, destination,
                                  transfer=gd_bg.transfer_network)

    if close_error:
        with pytest.raises(autoupdate.UpdateError, match="response close failed"):
            download()
    else:
        download()
    assert response_closed.is_set()
    assert destination.read_bytes() == (b"previous" if close_error else b"complete")
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("kind", ["app", "zapret"])
def test_shutdown_cleans_partial_download_with_blocked_body(monkeypatch, tmp_path, kind):
    shutdown = threading.Event()
    monkeypatch.setattr(gd_bg, "_SHUTDOWN", shutdown)
    entered = threading.Event()
    release = threading.Event()
    destination = tmp_path / "release.zip"
    destination.write_bytes(b"last complete release")

    class Response:
        headers = {"Content-Length": "100"}

        def __enter__(self):
            self.reads = 0
            return self

        def __exit__(self, *_args):
            pass

        def geturl(self):
            return zapret_update.IPSET_URL

        def read(self, _size):
            self.reads += 1
            if self.reads == 1:
                return b"prefix"
            entered.set()
            release.wait()
            return b""

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    if kind == "app":
        url = "https://github.com/example/repo/releases/download/v1/release.zip"
        monkeypatch.setattr(Response, "geturl", lambda _self: url)
        monkeypatch.setattr(autoupdate.gd_net, "open_url", Opener().open)

        def download():
            with pytest.raises(InterruptedError):
                autoupdate.download_asset({"url": url}, destination,
                                           transfer=gd_bg.transfer_network)
    else:
        monkeypatch.setattr(zapret_update.urllib.request, "build_opener", lambda *_: Opener())

        def download():
            ok, _ = zapret_update.download(zapret_update.IPSET_URL, destination,
                                           transfer=gd_bg.transfer_network)
            assert not ok

    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(download)
        try:
            assert entered.wait(2)
            shutdown.set()
            job.result(timeout=1)
            assert destination.read_bytes() == b"last complete release"
            assert list(tmp_path.iterdir()) == [destination]
        finally:
            release.set()


def test_shutdown_releases_network_wait_but_joins_mutation(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gd_bg, "_CLOSING", False)
    monkeypatch.setattr(gd_bg, "_SHUTDOWN", threading.Event())
    monkeypatch.setattr(gd_bg, "_WORKERS", set())
    network_entered = threading.Event()
    network_release = threading.Event()
    mutation_entered = threading.Event()
    mutation_release = threading.Event()
    delivered = []

    def stalled(entered, release):
        entered.set()
        release.wait()
        return {"ok": True}

    network = gd_bg.submit_network_read(
        lambda: stalled(network_entered, network_release), delivered.append)
    gd_bg.submit(
        lambda: stalled(mutation_entered, mutation_release), lambda result: None)
    try:
        assert network_entered.wait(2)
        assert mutation_entered.wait(2)
        gd_bg.begin_shutdown()
        assert network.wait(1000), "Qt worker should exit while the socket is still stuck"
        app.processEvents()
        assert delivered == []
        assert not gd_bg.wait_all(0), "mutation must finish before the system can be restored"
        mutation_release.set()
        assert gd_bg.wait_all(1000)
    finally:
        network_release.set()
        mutation_release.set()
        gd_bg.wait_all(1000)
        app.processEvents()

def test_network_read_ignores_result_after_widget_deletion(monkeypatch):
    from PyQt5 import sip
    from PyQt5.QtCore import QCoreApplication, QEvent
    from PyQt5.QtWidgets import QWidget

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gd_bg, "_CLOSING", False)
    monkeypatch.setattr(gd_bg, "_SHUTDOWN", threading.Event())
    monkeypatch.setattr(gd_bg, "_WORKERS", set())
    owner = QWidget()
    entered = threading.Event()
    release = threading.Event()
    delivered = []

    def read():
        entered.set()
        release.wait()
        return {"ok": True}

    worker = gd_bg.submit_network_read(read, delivered.append, parent=owner)
    try:
        assert entered.wait(2)
        assert worker.parent() is None
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert sip.isdeleted(owner)
        assert gd_bg.is_running(worker)
    finally:
        release.set()
        gd_bg.wait_all(1000)
        app.processEvents()

    assert delivered == []

@pytest.mark.parametrize("kind", ["app", "zapret"])
@pytest.mark.parametrize("truncated", [False, True])
def test_failed_download_preserves_existing_file(monkeypatch, tmp_path, kind, truncated):
    from http.client import IncompleteRead

    destination = tmp_path / "release.zip"
    destination.write_bytes(b"previous download")
    url = zapret_update.IPSET_URL if kind == "zapret" else (
        "https://github.com/example/repo/releases/download/v1/release.zip")

    class Response(BytesIO):
        headers = {}

        def geturl(self):
            return url

        def read(self, size):
            if truncated:
                raise IncompleteRead(b"partial", 50)
            return super().read(size)

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    if kind == "app":
        monkeypatch.setattr(autoupdate.gd_net, "open_url", Opener().open)
        with pytest.raises(autoupdate.UpdateError):
            autoupdate.download_asset({"url": url}, destination)
    else:
        monkeypatch.setattr(zapret_update.urllib.request, "build_opener", lambda *_: Opener())
        ok, _ = zapret_update.download(url, destination)
        assert not ok

    assert destination.read_bytes() == b"previous download"
    assert list(tmp_path.iterdir()) == [destination]
