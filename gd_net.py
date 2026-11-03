# -*- coding: utf-8 -*-
"""Сетевые запросы приложения: прямое соединение, затем локальный прокси.

В России GitHub часто открывается только через локальный прокси (Xray слушает
127.0.0.1:10808 и отдаёт и HTTP, и SOCKS5). Требовать от человека настройки
системного прокси нельзя: приложение обязано само перебрать маршруты.

Порядок: напрямую → системный прокси Windows → локальный HTTP-прокси 10808.
Рабочий маршрут запоминается на процесс, чтобы не тратить таймаут на каждую
попытку; если он перестал работать — перебор начинается заново.

SOCKS5 здесь не используется: он требует PySocks (лишняя зависимость и лишний
файл в сборке), а Xray на том же порту отвечает и по HTTP.
"""
from __future__ import annotations

import urllib.request

#: локальные прокси, которые пробуем после прямого соединения
LOCAL_PROXIES = ("http://127.0.0.1:10808",)

#: "" — напрямую; "system" — настройки Windows; иначе URL прокси
ROUTES = ("", "system", *LOCAL_PROXIES)

#: рабочий маршрут этого процесса (None — ещё не определён)
_working: str | None = None


def _build(route: str) -> urllib.request.OpenerDirector:
    if route == "system":
        return urllib.request.build_opener()
    if route == "":
        # Пустой ProxyHandler отключает системный прокси: иначе «прямое»
        # соединение молча пойдёт через настройки IE.
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": route, "https": route})
    )


def _clone(request: urllib.request.Request) -> urllib.request.Request:
    """Свежая копия запроса на каждую попытку.

    urllib при проходе через прокси перезаписывает ``Request.host`` и не
    возвращает его назад — повторное использование того же объекта ломает
    следующий, уже прямой маршрут (он идёт на адрес мёртвого прокси).
    """
    return urllib.request.Request(
        request.full_url,
        data=request.data,
        headers=dict(request.header_items()),
        method=request.get_method(),
        unverifiable=getattr(request, "unverifiable", False),
    )


def open_url(request: urllib.request.Request, timeout: float = 20.0):
    """Открыть URL, перебирая маршруты. Последняя ошибка уходит вызывающему."""
    global _working
    order = ([_working] if _working is not None else []) + [
        route for route in ROUTES if route != _working
    ]
    last: Exception | None = None
    for route in order:
        try:
            response = _build(route).open(_clone(request), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — нужен перебор, а не классификация
            last = exc
            continue
        _working = route
        return response
    assert last is not None
    raise last


def reset() -> None:
    """Забыть рабочий маршрут (например, после смены сети)."""
    global _working
    _working = None


def working_route() -> str | None:
    """Для диагностики: каким маршрутом ходили в последний раз."""
    return _working
