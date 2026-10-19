# -*- coding: utf-8 -*-
"""Проверка обновлений по GitHub Releases: по желанию, выключена по умолчанию.

Запрос уходит на ``api.github.com`` и не несёт ничего, кроме самого запроса: ни
версии приложения, ни идентификаторов, ни счётчиков. GitHub при этом видит IP —
поэтому галочка выключена, пока человек не поставит её сам («Утилиты» →
«Обновления»). Обещание «без облака и телеметрии» это не нарушает: без галочки
модуль не вызывается вообще.

Модуль ничего не знает про Qt: результат — обычный dict, пригодный и для
фонового вызова (:func:`gd_bg.submit`), и для теста.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import gd_i18n as i18n

#: канонический адрес: ссылки bonzeros/... ведут сюда же (старый аккаунт,
#: GitHub редиректит), но проверять версию лучше по текущему имени.
REPO = "bitarovyuriy/Gammadrilon"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE_URL = f"https://github.com/{REPO}/releases/latest"

#: ждать дольше смысла нет: проверка не срочная, а окно не должно «висеть»
TIMEOUT = 8.0


def parse_version(text: str) -> tuple[int, ...]:
    """«v3.10.2» → ``(3, 10, 2)``; непонятная строка («nightly», «») → ``()``.

    Сравнение кортежей даёт правильный порядок для 3.10 > 3.9 (в отличие от
    сравнения строк). Пустой кортеж означает «не разобрал» — такой тег всегда
    считается не новее установленного, чтобы не пугать ложным «есть обновление».
    """
    digits: list[int] = []
    for part in str(text or "").strip().lstrip("vV").split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        digits.append(int(num) if num else 0)
    return tuple(digits) if any(digits) else ()


def is_newer(latest: str, current: str) -> bool:
    """Строго новее: ``(3, 2) > (3, 1)``; равные — нет; непонятный тег — нет."""
    parsed = parse_version(latest)
    if not parsed:
        return False
    return parsed > parse_version(current)


def fetch_latest(timeout: float = TIMEOUT) -> dict:
    """Последний релиз с GitHub. Никогда не бросает: ``{"ok": bool, ...}``.

    Успех: ``{"ok": True, "tag", "name", "url", "published"}``.
    Отказ: ``{"ok": False, "error"}`` — текст готов для человека (его показывают
    в карточке «Обновления» и в тосте), а не для лога.
    """
    request = urllib.request.Request(
        API_URL,
        headers={
            # GitHub без User-Agent отвечает 403: представляемся честно
            "User-Agent": "Gammadrilon-update-check",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": i18n.t("GitHub ответил {v0}", v0=exc.code)}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": i18n.t("нет связи: {v0}", v0=exc.reason)}
    except (OSError, ValueError) as exc:  # таймаут, обрыв, неразобранный JSON
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        return {"ok": False, "error": i18n.t("в ответе GitHub нет тега версии")}
    return {
        "ok": True,
        "tag": tag,
        "name": str(data.get("name") or tag),
        "url": str(data.get("html_url") or PAGE_URL),
        "published": str(data.get("published_at") or "")[:10],
    }
