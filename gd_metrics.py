# -*- coding: utf-8 -*-
"""Единый кэш живых метрик: GPU, CPU/RAM, DNS.

Зачем: раньше статусбар, тултип трея и вкладки независимо дёргали
``nvidia-smi`` и ``netsh`` прямо из GUI-потока — отсюда подвисания и
расхождение значений. Здесь один фоновой поток снимает метрики по TTL и
складывает их в общий снимок; UI читает его мгновенно и никогда не блокируется.

``snapshot()`` лениво запускает поток при первом обращении. ``stop()``
вызывается на ``QApplication.aboutToQuit``.

Темп опроса адаптивный (:func:`set_activity`): живая телеметрия стоит внешних
процессов — ``nvidia-smi`` ~40 мс CPU за запуск, — поэтому когда окно ушло в трей,
опрос редеет, а открытая вкладка GPU наоборот учащает.

DNS по таймеру не читается вовсе: его конфигурация тоже стоит внешнего процесса
(``netsh`` ~50 мс) и меняется редко. Чтение — только по запросу: :func:`request_dns`
(разово, в фоне) либо :func:`set_dns` — когда вкладка «DNS» прочитала её сама.
"""
from __future__ import annotations

import threading
import time

#: как часто обновлять источник (секунды)
CPU_TTL = 1.0
#: GPU: основной путь — внешний nvidia-smi (~40 мс CPU за запуск!), поэтому
#: частота привязана к тому, смотрит ли кто-то на цифры (см. set_activity)
GPU_TTL = 5.0
GPU_TTL_FOCUS = 2.0
#: окно ушло в трей: смотреть не на что — фон экономим
CPU_TTL_TRAY = 3.0
GPU_TTL_TRAY = 20.0
# DNS-таймера здесь нет намеренно: DNS читается только по запросу (request_dns)
#: если GPU не отвечает N раз подряд — уходим в редкий опрос (нет NVIDIA и т.п.)
GPU_MISS_LIMIT = 3
GPU_TTL_IDLE = 30.0
#: шаг цикла: мельче, чем самый короткий TTL
_TICK = 0.5
_TICK_TRAY = 1.0

_lock = threading.Lock()
_state: dict = {"cpu": None, "ram": None, "gpu": None, "dns": None}
_ts: dict = {"cpu": 0.0, "gpu": 0.0, "dns": 0.0}
_thread: threading.Thread | None = None
_wake = threading.Event()
_stop = threading.Event()
_gpu_miss = 0                 # сколько раз подряд nvidia-smi не ответил
_activity = {"visible": True, "gpu_focus": False}
_dns_request = False          # кто-то попросил разово прочитать DNS (см. request_dns)

_EMPTY = {
    "cpu": {"mhz": None, "perf_pct": None, "load_pct": None,
            "temp_c": None, "vrm_c": None},
    "ram": None,
    "gpu": None,
    "dns": None,
}


# -- публичный интерфейс ---------------------------------------------------


def start() -> None:
    """Запустить фоновой сборщик метрик (идемпотентно)."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="gd-metrics", daemon=True)
    _thread.start()


def stop(timeout: float = 2.0) -> None:
    """Остановить сборщик (вызывается при выходе приложения)."""
    _stop.set()
    _wake.set()
    t = _thread
    if t is not None and t.is_alive():
        t.join(timeout)


def snapshot() -> dict:
    """Последний снимок метрик. Никогда не блокирует и не бросает исключений."""
    start()
    with _lock:
        snap = dict(_state)
        snap["ts"] = dict(_ts)
    return snap


def cpu() -> dict:
    """Снимок CPU-сенсоров (пустые поля, если данных ещё нет)."""
    return snapshot()["cpu"] or dict(_EMPTY["cpu"])


def ram() -> float | None:
    """Процент занятой памяти (None, если psutil недоступен)."""
    return snapshot()["ram"]


def gpu() -> dict | None:
    """Последний снимок GPU (None, если nvidia-smi недоступен)."""
    return snapshot()["gpu"]


def gpu_unavailable() -> bool:
    """True, если nvidia-smi не отвечает уже несколько попыток подряд (GPU нет)."""
    return _gpu_miss >= GPU_MISS_LIMIT


def dns() -> dict | None:
    """Последнее прочитанное состояние DNS: {'iface','mode','servers'}.

    ``None``, пока DNS не читали: по таймеру он не читается (см. :func:`request_dns`).
    """
    return snapshot()["dns"]


def dns_age() -> float | None:
    """Сколько секунд назад читался DNS (None — не читался ни разу).

    Цифры DNS читаются только по запросу, поэтому их возраст — часть честного
    показа: статусбар дописывает его к значению (``DNS 1.1.1.1 (12 мин)``).
    """
    with _lock:
        ts = _ts["dns"]
    return None if ts <= 0.0 else max(time.monotonic() - ts, 0.0)


def request_dns() -> None:
    """Разово прочитать DNS-конфигурацию в фоне — периодического опроса нет.

    Чтение стоит внешнего процесса (``netsh`` ~50 мс), поэтому запускается только
    по явному запросу: открытие вкладки «DNS», смена/возврат конфигурации.
    """
    global _dns_request
    with _lock:
        _dns_request = True
    _wake.set()


def set_dns(st: dict | None) -> None:
    """Положить уже прочитанное состояние DNS в общий кэш — без нового чтения.

    Вкладка «DNS» читает конфигурацию сама (своим фоновым заданием); результат
    кладётся сюда, чтобы статусбар брал значение из кэша, а не запускал ``netsh``
    второй раз.
    """
    if not st:
        return
    with _lock:
        _state["dns"] = dict(st)
        _ts["dns"] = time.monotonic()


def refresh_now(what: str | None = None) -> None:
    """Сбросить TTL — ближайший проход цикла снимет метрику заново.

    ``what``: ``"gpu"`` | ``"cpu"`` | ``"dns"`` | None (всё). Для DNS это разовый
    запрос на чтение (периодического опроса DNS нет), для остальных — сброс TTL.
    """
    global _dns_request
    if what == "dns":
        request_dns()
        return
    with _lock:
        for k in ("cpu", "gpu") if what is None else [what]:
            if k in _ts:
                _ts[k] = 0.0
        if what is None:
            _dns_request = True          # «обновить всё» — заодно разово прочитать DNS
    _wake.set()


def set_activity(visible: bool, gpu_focus: bool = False) -> None:
    """Сказать сборщику, насколько живые цифры нужны прямо сейчас.

    ``visible`` — окно на экране (не свёрнуто в трей), ``gpu_focus`` — открыта
    вкладка GPU. Политика частот живёт здесь: каждый опрос GPU — запуск внешнего
    ``nvidia-smi`` (~40 мс CPU), поэтому в трее он редеет до :data:`GPU_TTL_TRAY`,
    а при открытой вкладке GPU учащается до :data:`GPU_TTL_FOCUS`.

    Смена режима будит поток сразу: иначе новая частота применилась бы только
    после текущего ``_wake.wait``.
    """
    changed = (_activity["visible"] != bool(visible)
               or _activity["gpu_focus"] != bool(gpu_focus))
    _activity["visible"] = bool(visible)
    _activity["gpu_focus"] = bool(gpu_focus)
    if changed:
        _wake.set()


# -- внутреннее ------------------------------------------------------------


def _ttls() -> tuple[float, float, float]:
    """Действующие TTL (cpu, gpu) и шаг цикла — по интересу к цифрам сейчас.

    DNS здесь нет намеренно: он не читается по таймеру (см. :func:`request_dns`).
    """
    if _activity["visible"]:
        gpu = GPU_TTL_FOCUS if _activity["gpu_focus"] else GPU_TTL
        return CPU_TTL, gpu, _TICK
    return CPU_TTL_TRAY, GPU_TTL_TRAY, _TICK_TRAY


def _loop() -> None:
    global _gpu_miss, _dns_request

    from gd_sensors import CpuSensors, ram_usage

    sensors = None

    while not _stop.is_set():
        now = time.monotonic()
        with _lock:
            due = {k: now - _ts[k] for k in ("cpu", "gpu")}
            dns_wanted, _dns_request = _dns_request, False
        _wake.clear()

        cpu_ttl, gpu_ttl, tick = _ttls()
        if _gpu_miss >= GPU_MISS_LIMIT:
            gpu_ttl = GPU_TTL_IDLE          # GPU нет — не дёргаем nvidia-smi зря

        if due["cpu"] >= cpu_ttl:
            try:
                if sensors is None:
                    sensors = CpuSensors()
                sample = sensors.sample()
                ram_pct = ram_usage()
            except Exception:  # noqa: BLE001
                sample, ram_pct = None, None
            with _lock:
                if sample is not None:
                    _state["cpu"] = sample
                _state["ram"] = ram_pct
                _ts["cpu"] = now

        if due["gpu"] >= gpu_ttl:
            try:
                import gd_gpu

                st = gd_gpu.read_state()
            except Exception:  # noqa: BLE001
                st = None
            with _lock:
                if st is not None:
                    _state["gpu"] = st
                _ts["gpu"] = now
            _gpu_miss = _gpu_miss + 1 if st is None else 0

        if dns_wanted:                 # DNS — только по запросу, по таймеру не читаем
            try:
                import gd_dns

                st = gd_dns.current_state()
            except Exception:  # noqa: BLE001
                st = None
            with _lock:
                if st:
                    _state["dns"] = st
                _ts["dns"] = now

        _wake.wait(tick)

    if sensors is not None:
        try:
            sensors.close()
        except Exception:  # noqa: BLE001
            pass
