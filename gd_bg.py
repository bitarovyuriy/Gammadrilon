# -*- coding: utf-8 -*-
"""Фоновые задачи GUI: разовые и периодические.

Тонкая надстройка над :func:`gd_helper.run_async` — единый механизм вместо
самописных QThread в каждом экране:

  * результат всегда приходит в GUI-поток (сигнал Qt);
  * повторный тик не запускает вторую задачу, пока первая в полёте;
  * задачи не переживают свой виджет (``destroyed`` → ``stop()``).

Модуль не хранит состояний между вызовами, поэтому его безопасно использовать
из любого виджета и из тестов.
"""
from __future__ import annotations

import time

#: активные фоновые воркеры: нужны, чтобы дождаться их при выходе приложения
_WORKERS: set = set()


def submit(fn, on_done, parent=None):
    """Выполнить ``fn()`` в фоне, результат отдать в ``on_done`` (GUI-поток).

    Возвращённый QThread нужно сохранить до завершения — иначе он соберётся GC.
    После завершения воркер удаляется (``deleteLater``, см. ``gd_helper.run_async``),
    поэтому его состояние проверяют через :func:`is_running`, а не ``worker.isRunning()``.
    """
    import gd_helper

    worker = gd_helper.run_async(fn, on_done, parent)
    _WORKERS.add(worker)
    worker.finished.connect(lambda w=worker: _WORKERS.discard(w))
    return worker


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


def wait_all(timeout_ms: int = 2000) -> None:
    """Дождаться завершения фоновых задач (вызывается при выходе приложения).

    ``timeout_ms`` — общий дедлайн на все воркеры, а не на каждый. Без ожидания
    Qt удалит ещё работающий QThread вместе с родителем и напечатает
    «QThread: Destroyed while thread is still running» — вплоть до падения.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    for worker in list(_WORKERS):
        left_ms = int((deadline - time.monotonic()) * 1000)
        if left_ms <= 0:
            break
        try:
            worker.wait(left_ms)
        except RuntimeError:      # C++-объект уже удалён
            pass
    _WORKERS.clear()


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
