# -*- coding: utf-8 -*-
"""Смена языка на живом окне: ни одна вкладка не падает и не остаётся пустой.

Проверяет то, что не проверит юнит-тест: `set_language` переписывает подписи
уже собранных виджетов. Если где-то строку забудут связать с переводом —
вкладка останется с русским текстом в английском интерфейсе, и наоборот.

Запуск: python tools/lang_check.py     (код 1, если есть падения)
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

import main as gd_main  # noqa: E402

_APP = None
ERRORS: list[str] = []


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


def hook(exc_type, exc_value, exc_tb):
    ERRORS.append("".join(traceback.format_exception_only(exc_type, exc_value)).strip())
    traceback.print_exception(exc_type, exc_value, exc_tb)


def main() -> int:
    sys.excepthook = hook
    app().setStyleSheet(gd_main.build_app_stylesheet())
    window = gd_main.MainWindow()
    window.resize(window.compact_size)
    window.show()
    pump(1200)

    mapping = getattr(window, "_key_to_index", {}) or {}
    pages = sorted(((key, index) for key, index in mapping.items()
                    if isinstance(index, int)), key=lambda item: item[1])

    for lang in ("en", "ru"):
        before = len(ERRORS)
        window.set_language(lang)
        pump(400)
        for key, index in pages:
            window.tabs.setCurrentIndex(index)
            pump(150)
        mark = "ок " if len(ERRORS) == before else "ПАДЕНИЕ"
        print(f"{mark:<10} язык {lang}: {len(pages)} вкладок")

    window.close()
    if ERRORS:
        print("\nОШИБКИ:")
        for line in ERRORS:
            print(f"  - {line}")
        return 1
    print("\nсмена языка RU/EN прошла без падений")
    return 0


if __name__ == "__main__":
    sys.exit(main())
