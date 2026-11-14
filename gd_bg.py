# -*- coding: utf-8 -*-
"""Фоновые задачи GUI: разовые и периодические.

Тонкая надстройка над :func:`gd_helper.run_async` — единый механизм вместо
самописных QThread в каждом экране:

  * результат всегда приходит в GUI-поток (сигнал Qt);
  * повторный тик не запускает вторую задачу, пока первая в полёте;
  * результаты сетевых чтений отбрасываются при закрытии приложения или виджета.
"""
from __future__ import annotations

import queue
import threading
import time

#: активные фоновые воркеры: нужны, чтобы дождаться их при выходе приложения
_WORKERS: set = set()
_CLOSING = False
_SHUTDOWN = threading.Event()


def submit(fn, on_done, parent=None, *, during_shutdown=False):
    """Выполнить ``fn()`` в фоне, результат отдать в ``on_done`` (GUI-поток).

    Возвращённый QThread нужно сохранить до завершения — иначе он соберётся GC.
    После завершения воркер удаляется (``deleteLater``, см. ``gd_helper.run_async``),
    поэтому его состояние проверяют через :func:`is_running`, а не ``worker.isRunning()``.
    """
    import gd_helper

    if _CLOSING and not during_shutdown:
        return None
    worker = gd_helper.run_async(fn, on_done, parent)
    register(worker)
    return worker


def register(worker):
    """Также учитывать специализированные QThread измерений."""
    _WORKERS.add(worker)
    worker.finished.connect(lambda w=worker: _WORKERS.discard(w))
    return worker


def wait_network_read(fn):
    """Run a read-only diagnostic without making exit depend on blocking socket I/O.

    The daemon may still be in DNS or a socket read when the process exits.
    Its callable must not touch Qt, change system settings or write to disk.
    """
    if _SHUTDOWN.is_set():
        raise InterruptedError("application is closing")
    ready = threading.Event()
    result = None
    error = None

    def read():
        nonlocal result, error
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            error = exc
        finally:
            ready.set()

    threading.Thread(target=read, daemon=True).start()
    while not _SHUTDOWN.is_set():
        if ready.wait(0.05):
            if error is not None:
                raise error
            return result
    raise InterruptedError("application is closing")


def submit_network_read(fn, on_done, parent=None):
    """Run a read independently of the widget and its callback lifetime."""
    from PyQt5 import sip

    def deliver(result):
        if not _SHUTDOWN.is_set() and (parent is None or not sip.isdeleted(parent)):
            on_done(result)

    # Closing a widget must not delete a QThread that is still running.
    return submit(lambda: wait_network_read(fn), deliver)


def transfer_network(open_response, write_chunk, on_headers, chunk_size):
    """Pass socket data to the caller without letting the socket own the file.

    Only the caller runs callbacks. On shutdown it can close and remove the
    partial file even if the reader is still blocked in DNS or recv.
    """
    pending = queue.Queue(maxsize=2)
    stop = threading.Event()

    def publish(kind, value=None):
        while not stop.is_set():
            try:
                pending.put((kind, value), timeout=0.05)
                return True
            except queue.Full:
                pass
        return False

    def read():
        try:
            with open_response() as response:
                if not publish("headers", (response.geturl(), response.headers)):
                    return
                while not stop.is_set():
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    if not publish("chunk", chunk):
                        return
            publish("done")
        except Exception as exc:  # noqa: BLE001
            publish("error", exc)

    if _SHUTDOWN.is_set():
        raise InterruptedError("application is closing")
    threading.Thread(target=read, daemon=True).start()
    try:
        while not _SHUTDOWN.is_set():
            try:
                kind, value = pending.get(timeout=0.05)
            except queue.Empty:
                continue
            check_shutdown()
            if kind == "headers":
                on_headers(*value)
            elif kind == "chunk":
                write_chunk(value)
            elif kind == "error":
                raise value
            else:
                return
        raise InterruptedError("application is closing")
    finally:
        stop.set()


def check_shutdown():
    """Check cancellation immediately before starting a local transaction."""
    if _SHUTDOWN.is_set():
        raise InterruptedError("application is closing")


def begin_shutdown() -> None:
    global _CLOSING
    _CLOSING = True
    _SHUTDOWN.set()


def is_running(worker) -> bool:
    """Работает ли фоновой воркер (:func:`submit`) прямо сейчас.

    После завершения воркер удаляется через ``deleteLater``, и обращение к его
    C++-объекту бросает ``RuntimeError: wrapped C/C++ object ... has been deleted``
    (так падал повторный клик «Применить» на вкладке GPU). Для guard'ов
    («не запускать второй раз, пока идёт первый») удалённый воркер — это
    «уже не работает», а не ошибка: все такие проверки идут через этот хелпер.
    """
    if worker is None:
        return False
    try:
        return bool(worker.isRunning())
    except RuntimeError:      # C++-объект уже удалён
        return False


def wait_all(timeout_ms: int = 2000) -> bool:
    """Дождаться завершения фоновых задач (вызывается при выходе приложения).

    ``timeout_ms`` — общий дедлайн на все воркеры, а не на каждый. Без ожидания
    Qt удалит ещё работающий QThread вместе с родителем и напечатает
    «QThread: Destroyed while thread is still running» — вплоть до падения.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    for worker in list(_WORKERS):
        if not is_running(worker):
            _WORKERS.discard(worker)
            continue
        left_ms = int((deadline - time.monotonic()) * 1000)
        if left_ms <= 0:
            continue
        try:
            worker.wait(left_ms)
        except RuntimeError:      # C++-объект уже удалён
            pass
        if not is_running(worker):
            _WORKERS.discard(worker)
    return not _WORKERS


class Periodic:
    """Периодический фоновой опрос с защитой от наложения.

    Пока предыдущий вызов не вернулся, следующий тик пропускается — дорогая
    операция никогда не встаёт в очередь. Останавливается вместе с ``owner``
    (любой QObject) или явным :meth:`stop`.
    """

    def __init__(self, owner, interval_ms: int, fn, on_done):
        from PyQt5.QtCore import QTimer

        self._owner = owner
        self._fn = fn
        self._on_done = on_done
        self._busy = False
        self._worker = None
        self._timer = QTimer(owner)
        self._timer.setInterval(int(interval_ms))
        self._timer.timeout.connect(self._tick)
        if owner is not None:
            owner.destroyed.connect(self._on_destroyed)

    # -- управление --------------------------------------------------------
    def _on_destroyed(self, *_args):
        self.stop()

    @property
    def busy(self) -> bool:
        """Идёт ли сейчас фоновой вызов (для guard'ов в UI)."""
        return self._busy

    def start(self, interval_ms: int | None = None) -> None:
        if interval_ms:
            self._timer.setInterval(int(interval_ms))
        self._timer.start()

    def stop(self) -> None:
        try:
            self._timer.stop()
        except RuntimeError:      # C++-объект уже удалён
            pass

    def trigger(self) -> None:
        """Внеочередной опрос (например, сразу после операции)."""
        self._tick()

    # -- внутреннее --------------------------------------------------------
    def _tick(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._worker = submit(self._fn, self._done, self._owner)

    def _done(self, result) -> None:
        self._busy = False
        self._worker = None
        cb = self._on_done
        if cb is not None:
            cb(result)


def periodic(owner, interval_ms: int, fn, on_done) -> Periodic:
    """Создать :class:`Periodic` (удобная фабрика для читаемости вызовов)."""
    return Periodic(owner, interval_ms, fn, on_done)
