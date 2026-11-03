# -*- coding: utf-8 -*-
"""Доступность с клавиатуры: у каждого действия есть имя и фокус.

Зачем: программой часто пользуются с клавиатуры и скринридером. Кнопка без
текста («▶», «×») без `accessibleName` читается как «кнопка» — по такой не
понять, что делать. Переключатель без подписи — то же самое.

Что проверяет: все `QAbstractButton` и `QComboBox` на каждой вкладке должны
иметь непустое имя (текст, `accessibleName` или подсказку), а действия —
реагировать на Tab (`focusPolicy` не `NoFocus`).

Запуск: python tools/a11y_check.py    (код 1, если есть безымянные элементы)
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

import main as gd_main  # noqa: E402

_APP = None


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


def describe(widget) -> str:
    name = (widget.accessibleName() or "").strip()
    text = (getattr(widget, "text", lambda: "")() or "").strip()
    tip = (widget.toolTip() or "").strip()
    current = (getattr(widget, "currentText", lambda: "")() or "").strip()
    placeholder = (getattr(widget, "placeholderText", lambda: "")() or "").strip()
    return name or text or tip or current or placeholder


def check_page(page, key: str) -> list[str]:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QAbstractButton, QComboBox

    problems: list[str] = []
    for widget in page.findChildren(QAbstractButton) + page.findChildren(QComboBox):
        if not widget.isVisible() or not widget.isEnabled():
            continue
        name = describe(widget)
        if not name:
            problems.append(f"{key}: {type(widget).__name__} без имени")
        elif len(name) <= 2 and name in {"▶", "×", "+", "-", "?"}:
            problems.append(f"{key}: {type(widget).__name__} «{name}» без accessibleName")
        if widget.focusPolicy() == Qt.NoFocus and isinstance(widget, QAbstractButton):
            problems.append(f"{key}: «{name}» недоступна с Tab (NoFocus)")
    return problems


def main() -> int:
    app().setStyleSheet(gd_main.build_app_stylesheet())
    window = gd_main.MainWindow()
    window.resize(window.compact_size)
    window.show()
    pump(1500)

    mapping = getattr(window, "_key_to_index", {}) or {}
    pages = sorted(((key, index) for key, index in mapping.items()
                    if isinstance(index, int)), key=lambda item: item[1])

    problems: list[str] = []
    for key, index in pages:
        window.tabs.setCurrentIndex(index)
        pump(400)
        page = window.tabs.widget(index)
        if page is not None:
            problems.extend(check_page(page, key))

    window.close()
    if problems:
        print("ПРОБЛЕМЫ ДОСТУПНОСТИ:")
        for line in problems:
            print(f"  - {line}")
        return 1
    print(f"все элементы на {len(pages)} вкладках имеют имя и доступны с Tab")
    return 0


if __name__ == "__main__":
    sys.exit(main())
