# -*- coding: utf-8 -*-
"""Надёжный клиент elevated-агента Gammadrilon.

Один elevated-процесс-компаньон обслуживает все привилегированные операции
(лимит GPU, очистка ОЗУ, DNS) по TCP-loopback с встроенным handshake (authkey).
Этот модуль — единственная точка связи с ним:

  * сессия валидируется, битый/чужой файл сессии не роняет приложение;
  * `call()` никогда не бросает исключение наружу — всегда возвращает dict;
  * `run_async()` уводит блокирующий запуск/UAC в фон, GUI-поток не замирает.

Тяжёлые зависимости (PyQt5) импортируются лениво: модуль безопасен и в консольном
хелпере, где Qt нет.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from multiprocessing.connection import Client

import gd_i18n as i18n
import gd_proc

TCP_HOST = "127.0.0.1"
TCP_PORT = 47318

HERE = os.path.dirname(os.path.abspath(__file__))
_SESSION_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Gammadrilon"
)
SESSION_FILE = os.path.join(_SESSION_DIR, "gpu_session.json")
#: журнал хелпера: в windowed-сборке stdout нет — диагностика идёт только в файл
LOG_FILE = os.path.join(_SESSION_DIR, "gpu_service.log")


def new_token() -> str:
    """Случайный ключ сессии: UI передаёт его хелперу при запуске."""
    import secrets

    return secrets.token_hex(16)


class HelperClient:
    """Клиент elevated-хелпера: подключение по loopback, запуск через UAC.

    Параметры позволяют переиспользовать класс для любого компаньона
    (по умолчанию — GPU-агент, который также выполняет память и DNS).
    """

    def __init__(
        self,
        port: int = TCP_PORT,
        session_file: str = SESSION_FILE,
        service: str = "gd_gpu_service.py",
        service_flag: str = "--gpu-service",
    ) -> None:
        self._conn = None
        self.port = port
        self.token: str | None = None
        self.session_file = session_file
        self.service = service
        self.service_flag = service_flag
        # Один запрос — один ответ на одно соединение. Спрашивают из разных
        # фоновых потоков (вкладка, правила автоматизации), а TCP-клиент не
        # различает, чей ответ пришёл: без замка ответы достались бы не тем,
        # кто спрашивал (та же болячка, что в PowerClient — см. _tmp_power_race.py).
        self._lock = threading.RLock()

    # -- сессия ------------------------------------------------------------
    def _load_session(self) -> dict | None:
        """Прочитать сохранённую сессию. Битый файл — просто None, без исключений."""
        try:
            import json

            with open(self.session_file, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            port = int(data["port"])
            token = str(data["token"])
            int(token, 16)  # токен обязан быть hex — иначе fromhex упадёт
        except (KeyError, TypeError, ValueError):
            return None
        return {"port": port, "token": token}

    def _save_session(self, port: int, token: str) -> None:
        try:
            import json

            os.makedirs(os.path.dirname(self.session_file), exist_ok=True)
            with open(self.session_file, "w", encoding="utf-8") as fh:
                json.dump({"port": port, "token": token}, fh)
        except OSError:
            pass

    # -- соединение --------------------------------------------------------
    def _connect(self, port: int, token: str) -> bool:
        try:
            conn = Client((TCP_HOST, int(port)), family="AF_INET", authkey=bytes.fromhex(token))
        except (OSError, ValueError):
            return False
        self._conn = conn
        self.port = port
        self.token = token
        return True

    def ensure(self) -> bool:
        """Подключиться к живому хелперу или запустить его (UAC)."""
        with self._lock:
            return self._ensure_locked()

    def _ensure_locked(self) -> bool:
        """Подключение/запуск хелпера (замок уже взят).

        Отдельным методом — чтобы ``call()`` держал замок непрерывно от
        соединения до ответа: два потока не поднимут два хелпера и не разберут
        чужие ответы.
        """
        if self._conn is not None:
            return True
        sess = self._load_session()
        if sess and self._connect(sess["port"], sess["token"]):
            return True

        port = self.port or TCP_PORT
        token = new_token()
        self._save_session(port, token)
        self._launch(port, token)
        for _ in range(60):
            time.sleep(0.25)
            if self._connect(port, token):
                return True
        return False

    def _launch(self, port: int, token: str) -> None:
        """Запустить хелпер с правами администратора (однократный UAC)."""
        tail = f"'--port','{port}','--token','{token}','--ui-pid','{os.getpid()}','--log','{LOG_FILE}'"
        if getattr(sys, "frozen", False):
            # в собранном exe сервис — тот же исполняемый файл с флагом режима
            args = f"-ArgumentList '{self.service_flag}',{tail}"
            exe = sys.executable
        else:
            svc = os.path.join(HERE, self.service)
            args = f"-ArgumentList '-u','{svc}',{tail}"
            exe = gd_proc.python_exe()
        cmd = f"Start-Process -FilePath '{exe}' {args} -Verb RunAs -WindowStyle Hidden"
        gd_proc.popen(["powershell", "-NoProfile", "-Command", cmd])

    # -- протокол ----------------------------------------------------------
    def has_session(self) -> bool:
        """Есть ли живой хелпер: только подключение — без запуска и без UAC.

        Позволяет спросить «права уже выданы?» и ничего не показать
        пользователю, если нет.
        """
        with self._lock:
            if self._conn is not None:
                return True
            sess = self._load_session()
            if not sess:
                return False
            try:
                return self._connect(sess["port"], sess["token"])
            except Exception:  # noqa: BLE001
                return False

    def call(self, obj: dict, timeout: float = 15.0) -> dict:
        """Отправить команду, при необходимости запустив хелпер (UAC). Никогда не бросает."""
        try:
            with self._lock:
                if not self._ensure_locked():
                    return {"ok": False, "error": i18n.t("хелпер не запустился (нужны права администратора)")}
                return self._call_connected_locked(obj, timeout)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def call_existing(self, obj: dict, timeout: float = 15.0) -> dict:
        """Команда только в уже запущенный хелпер: UAC отсюда **не** поднимается.

        Нужно фоновым действиям (правила автоматизации): окно UAC, всплывшее
        поверх чужой игры или фильма, — диалог, которого никто не ждал. Если
        живой сессии нет, возвращаем честный отказ (он попадёт в лог правил),
        а не запускаем хелпер.
        """
        if not self.has_session():
            return {"ok": False, "error": i18n.t(
                "нужны права администратора, а фоновое действие не поднимает UAC: "
                "откройте вкладку «Утилиты» или «DNS» и разрешите один раз")}
        with self._lock:
            return self._call_connected_locked(obj, timeout)

    def _call_connected(self, obj: dict, timeout: float) -> dict:
        """Отправка в установленное соединение. Никогда не бросает исключение."""
        with self._lock:
            return self._call_connected_locked(obj, timeout)

    def _call_connected_locked(self, obj: dict, timeout: float) -> dict:
        """Отправка и приём ответа (замок уже взят). Никогда не бросает.

        Замок держится на всю пару «вопрос — ответ»: ждать ответа из одного
        соединения из двух потоков одновременно нельзя — ответы разберутся в
        произвольном порядке, и вызывающий получит чужой. Ссылку на соединение
        держим локально: `shutdown` закрывает его из другого потока.
        """
        conn = self._conn
        try:
            conn.send(obj)
            if not conn.poll(timeout):
                self._conn = None
                return {"ok": False, "error": i18n.t("таймаут ответа хелпера")}
            return conn.recv()
        except (OSError, EOFError):
            self._conn = None
            return {"ok": False, "error": i18n.t("связь с хелпером потеряна")}
        except Exception as exc:  # noqa: BLE001
            self._conn = None
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def shutdown(self, restore: bool = True, force: bool = False) -> None:
        if self._conn is not None:
            try:
                self.call({"cmd": "shutdown", "restore": restore, "force": force})
            except Exception:  # noqa: BLE001
                pass
        self._conn = None

    def shutdown_async(self, restore: bool = True, force: bool = False) -> None:
        """Попросить хелпер завершиться с откатом, не дожидаясь ответа.

        Нужен при выходе приложения: GUI не должен ждать (``ensure()`` может
        поднять UAC и занять секунды). Если хелпер не успеет обработать команду,
        откат доделает его watchdog по смерти UI-процесса.

        `force=True` — откат обязателен вопреки настройке «не возвращать лимит
        при выходе»: так уходит неподтверждённое изменение (ТЗ 4.3).
        """
        if self._conn is None:
            return
        try:
            self._conn.send({"cmd": "shutdown", "restore": restore, "force": force})
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._conn = None


def run_async(fn, on_done, parent=None):
    """Выполнить `fn()` в фоновом потоке и вернуть результат в GUI-поток.

    `fn` возвращает dict (как `HelperClient.call`); любое исключение превращается
    в `{'ok': False, 'error': ...}`, чтобы вызывающий код не падал. `on_done`
    вызывается в GUI-потоке через сигнал. Возвращённый объект нужно сохранить —
    иначе поток соберётся GC до завершения.
    """
    from PyQt5.QtCore import QThread, pyqtSignal

    class _Worker(QThread):
        done = pyqtSignal(object)

        def run(self):  # noqa: D401
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self.done.emit(result)

    worker = _Worker(parent)
    worker.done.connect(on_done)
    worker.finished.connect(worker.deleteLater)
    worker.start()
    return worker

