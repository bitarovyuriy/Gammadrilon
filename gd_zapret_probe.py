# -*- coding: utf-8 -*-
"""Проверка доступности целей через текущий обход DPI.

У Flowseal в ``utils/targets.txt`` лежит список целей (Discord, YouTube,
Google, Cloudflare). Консольный ``Run tests`` гоняет по ним длинный сценарий,
но для «работает ли обход сейчас» достаточно короткого честного запроса:
TLS/HTTP с таймаутом и замером времени.

Модуль ничего не меняет: только сеть. Запускается из вкладки «Обход DPI»
в фоне, результат — сколько целей ответило и средняя задержка.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gd_i18n as i18n

#: цели, если файла targets.txt нет (те же, что в комплекте Flowseal)
DEFAULT_TARGETS: tuple[tuple[str, str], ...] = (
    ("Discord", "https://discord.com"),
    ("Discord CDN", "https://cdn.discordapp.com"),
    ("YouTube", "https://www.youtube.com"),
    ("YouTube image", "https://i.ytimg.com"),
    ("Google", "https://www.google.com"),
    ("Cloudflare", "https://www.cloudflare.com"),
)

_LINE_RE = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*"(.*)"\s*$')


def parse_targets(root: str | None) -> list[tuple[str, str]]:
    """Прочитать цели из ``utils\\targets.txt``; битый файл — не повод падать."""
    if not root:
        return list(DEFAULT_TARGETS)
    path = Path(root) / "utils" / "targets.txt"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return list(DEFAULT_TARGETS)
    targets: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip()
        if value.lower().startswith("ping:"):
            continue                     # ICMP-цели проверяются отдельно, не тут
        if value.startswith("http"):
            targets.append((name, value))
    return targets or list(DEFAULT_TARGETS)


def probe_url(url: str, timeout: float = 6.0) -> dict:
    """Один запрос: ``{ok, ms, error}``. Ошибка TLS/сети — это ``ok=False``."""
    start = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Gammadrilon probe)",
            "Accept": "*/*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(64)                 # достаточно заголовков/начала тела
        return {"ok": True, "ms": (time.perf_counter() - start) * 1000.0, "error": None}
    except urllib.error.HTTPError as exc:
        # 403/404 от CDN — уже признак работающего соединения; 5xx — нет
        ok = int(exc.code) < 500
        return {"ok": ok, "ms": (time.perf_counter() - start) * 1000.0,
                "error": None if ok else f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "ms": (time.perf_counter() - start) * 1000.0,
                "error": f"{type(exc).__name__}: {exc}"}


def probe_all(root: str | None, timeout: float = 6.0,
              max_workers: int = 8, on_progress=None) -> dict:
    """Проверить все цели параллельно.

    ``on_progress(done, total, name, ok)`` вызывается после каждой цели,
    чтобы окно могло показать прогресс, не замирая.
    """
    targets = parse_targets(root)
    results: list[dict] = []
    total = len(targets)

    def work(item):
        idx, (name, url) = item
        res = probe_url(url, timeout=timeout)
        res.update({"name": name, "url": url})
        if on_progress:
            try:
                on_progress(idx + 1, total, name, res["ok"])
            except Exception:  # noqa: BLE001 — прогресс не должен ломать проверку
                pass
        return idx, res

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, total or 1))) as pool:
        for _, res in pool.map(work, list(enumerate(targets))):
            results.append(res)

    results.sort(key=lambda r: r["name"])
    ok_count = sum(1 for r in results if r["ok"])
    good = [r["ms"] for r in results if r["ok"]]
    return {
        "ok": ok_count == total and total > 0,
        "total": total,
        "passed": ok_count,
        "failed": total - ok_count,
        "avg_ms": (sum(good) / len(good)) if good else None,
        "results": results,
        "checked_at": time.time(),
    }


def describe(result: dict) -> str:
    """Короткая строка для строки состояния UI."""
    total = int(result.get("total") or 0)
    passed = int(result.get("passed") or 0)
    avg = result.get("avg_ms")
    tail = i18n.t("средняя {v0:.0f} мс", v0=avg) if avg else i18n.t("средняя н/д")
    failed = [r["name"] for r in (result.get("results") or []) if not r.get("ok")]
    text = i18n.t("Доступно {v0} из {v1}, {v2}", v0=passed, v1=total, v2=tail)
    if failed:
        text += " " + i18n.t("не ответили: {v0}", v0=", ".join(failed[:5]))
    return text
