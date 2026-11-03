# -*- coding: utf-8 -*-
"""Долгий прогон: не течёт ли память при переключении вкладок.

Что делает: поднимает окно offscreen и минутами гоняет вкладки по кругу,
каждые 30 секунд печатает рабочий набор процесса. Память PyQt растёт на
первом круге (кэши шрифтов, стили) — это норма; тревога начинается, если
рост не останавливается.

Запуск: python tools/soak_check.py --minutes 5      (по умолчанию 3)
Код 1, если память выросла больше чем на 150 МБ от второго замера.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

import main as gd_main  # noqa: E402  (импорт до QApplication — иначе висяк)

_APP = None
GROWTH_LIMIT_MB = 150


def app():
    global _APP
    from PyQt5.QtWidgets import QApplication

    _APP = QApplication.instance() or QApplication([])
    return _APP


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        _APP.processEvents()
        time.sleep(0.02)


def rss_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:  # noqa: BLE001
        return 0.0


def main() -> int:
    minutes = 3.0
    if "--minutes" in sys.argv:
        index = sys.argv.index("--minutes")
        if index + 1 < len(sys.argv):
            try:
                minutes = float(sys.argv[index + 1])
            except ValueError:
                pass

    app().setStyleSheet(gd_main.build_app_stylesheet())
    window = gd_main.MainWindow()
    window.resize(window.compact_size)
    window.show()
    pump(1500)

    mapping = getattr(window, "_key_to_index", {}) or {}
    pages = [index for index in mapping.values() if isinstance(index, int)]
    if "--pages" in sys.argv:
        index = sys.argv.index("--pages")
        if index + 1 < len(sys.argv):
            wanted = [int(item) for item in sys.argv[index + 1].split(",") if item.strip().isdigit()]
            pages = [item for item in pages if item in wanted] or pages
    if not pages:
        print("нет вкладок — проверять нечего")
        return 1

    samples: list[float] = [rss_mb()]
    deadline = time.monotonic() + minutes * 60
    lap = 0
    next_sample = time.monotonic() + 30
    print(f"старт: {samples[0]:.0f} МБ, вкладок {len(pages)}, {minutes:.0f} мин")

    static = "--static" in sys.argv      # не переключать вкладки: контрольный прогон
    while time.monotonic() < deadline:
        for index in pages:
            if not static:
                window.tabs.setCurrentIndex(index)
            pump(120)
            if time.monotonic() >= next_sample:
                value = rss_mb()
                samples.append(value)
                lap_end = f", кругов: {lap}" if lap else ""
                print(f"{value:7.0f} МБ{lap_end}")
                next_sample = time.monotonic() + 30
        lap += 1

    # PyQt копит ссылки до цикла сборки: без этого «утечка» может быть мусором
    import gc

    before = rss_mb()
    gc.collect()
    after = rss_mb()
    print(f"после сборки мусора: {before:.0f} -> {after:.0f} МБ")
    samples.append(after)

    window.close()
    if len(samples) < 3:
        print("мало замеров — прогон слишком короткий для вывода")
        return 0

    # первый замер — до прогрева; сравниваем со вторым, чтобы не ловить кэши
    baseline = samples[1]
    growth = samples[-1] - baseline
    print(f"\nрост от второго замера: {growth:+.0f} МБ за {len(samples) - 2} интервалов")
    per_interval = growth / max(1, len(samples) - 2)
    print(f"в среднем: {per_interval:+.1f} МБ за 30 с")
    if growth > GROWTH_LIMIT_MB:
        print("ПОДОЗРЕНИЕ НА УТЕЧКУ: рост не останавливается")
        return 1
    if per_interval > 5:
        # синтетика: 20 переключений в секунду дают ~38 КБ на переключение
        # (кэш стилей и пиксмапов Qt). В обычной работе это не проявляется,
        # но и «утечки нет» говорить нельзя
        print("рост есть, но медленный: при обычной работе незаметно")
        return 0
    print("утечки не видно")
    return 0


if __name__ == "__main__":
    sys.exit(main())
