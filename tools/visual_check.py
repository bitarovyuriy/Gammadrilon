# -*- coding: utf-8 -*-
"""Визуальная регрессия: снимки страниц и сравнение с эталоном.

Зачем: вёрстка ломается молча. Тесты проверяют данные и логику, смоук — что
окно собралось, но «карточка уехала под футер» или «подпись обрезалась»
не видит ни один из них.

Как работает: окно поднимается offscreen, по очереди открывается каждая
вкладка, снимается `grab()`. Первый прогон с `--update` пишет эталоны в
`build/shots/baseline/`, обычный прогон сравнивает попиксельно (с шагом,
чтобы не считать антиалиасинг) и падает, если расхождение выше порога.

Запуск:
  python tools/visual_check.py            # сравнить с эталоном
  python tools/visual_check.py --update   # перезаписать эталоны
  QT_SCALE_FACTOR=1.5 python tools/visual_check.py --suffix @150
                                         # тот же набор при масштабе 150 %
Код возврата: 0 — всё совпало (или эталоны обновлены), 1 — есть расхождения.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# без этого offscreen не находит системные шрифты и рисует «блоки без текста»:
# обрезанную подпись в таком снимке не увидеть. С папкой шрифтов Windows
# текст на месте, и регрессия ловит реальную вёрстку
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt5.QtGui import QImage  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

import main as gd_main  # noqa: E402

SHOTS = ROOT / "build" / "shots"
BASELINE = SHOTS / "baseline"
CURRENT = SHOTS / "current"

#: доля различающихся пикселей, после которой снимок считается «поехал».
#: 2 %, а не 0,5 %: на «стабильных» страницах всё равно живут цифры (загрузка
#: CPU, температура в плитках) — они дают до 0,5 % между прогонами, а реальный
#: сдвиг блока или обрезанная подпись — проценты
DIFF_LIMIT = 0.02
#: шаг выборки: антиалиасинг шрифтов даёт единичные отличия, их не считаем
STEP = 4
#: страницы с живыми данными (метрики, статусы, списки процессов): снимок
#: снимаем для истории, но с эталоном не сравниваем — они меняются сами
VOLATILE = {"cpu.png", "gpu.png", "zapret.png", "diagnose.png", "dns.png"}

_APP = None


def app():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        _APP.processEvents()
        time.sleep(0.02)


def tabs(window) -> list[tuple[str, int]]:
    """Все вкладки окна: ключ и индекс."""
    mapping = getattr(window, "_key_to_index", {}) or {}
    return sorted(((key, index) for key, index in mapping.items()
                   if isinstance(index, int)), key=lambda item: item[1])


def shot(window, key: str, path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    return bool(window.grab().save(str(path)))


def diff_ratio(path_a: Path, path_b: Path) -> float:
    """Доля различающихся пикселей (выборка с шагом STEP)."""
    first, second = QImage(str(path_a)), QImage(str(path_b))
    if first.isNull() or second.isNull() or first.size() != second.size():
        return 1.0
    different = total = 0
    for y in range(0, first.height(), STEP):
        for x in range(0, first.width(), STEP):
            total += 1
            if first.pixel(x, y) != second.pixel(x, y):
                different += 1
    return different / max(1, total)


def _suffix(argv: list[str]) -> str:
    for index, item in enumerate(argv):
        if item == "--suffix" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--suffix="):
            return item.split("=", 1)[1]
    return ""


def main(argv: list[str]) -> int:
    update = "--update" in argv
    suffix = _suffix(argv)
    app()
    app().setStyleSheet(gd_main.build_app_stylesheet())

    window = gd_main.MainWindow()
    window.resize(window.compact_size)
    window.show()
    pump(1500)

    problems: list[str] = []
    compared = 0
    for key, index in tabs(window):
        window.tabs.setCurrentIndex(index)
        pump(900)
        name = key.replace("tab.", "") + suffix + ".png"
        current = CURRENT / name
        if not shot(window, key, current):
            problems.append(f"{key}: снимок не сохранился")
            continue
        baseline = BASELINE / name
        base_name = name.split("@")[0]
        if not base_name.endswith(".png"):
            base_name += ".png"
        if base_name in VOLATILE and not update:
            print(f"{'живая':<14} {name:<16} (без сравнения)")
            continue
        if update or not baseline.is_file():
            BASELINE.mkdir(parents=True, exist_ok=True)
            current.replace(baseline)
            print(f"эталон: {name}")
            continue
        compared += 1
        ratio = diff_ratio(baseline, current)
        status = "ок " if ratio <= DIFF_LIMIT else "РАСХОЖДЕНИЕ"
        print(f"{status:<14} {name:<16} {ratio * 100:.2f}%")
        if ratio > DIFF_LIMIT:
            problems.append(f"{name}: {ratio * 100:.2f}% (эталон {baseline.name})")

    if update:
        print("\nэталоны обновлены")
        return 0
    if problems:
        print("\nПРОБЛЕМЫ:")
        for line in problems:
            print(f"  - {line}")
        print("Сравнить глазами: build/shots/baseline против build/shots/current")
        return 1
    print(f"\nвсе {compared} страниц совпали с эталоном")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
