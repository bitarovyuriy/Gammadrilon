# -*- coding: utf-8 -*-
"""Тосты (ТЗ 5, «Обратная связь»): любое действие → всплывающее подтверждение + строка
в журнале. Тост не забирает фокус и не принимает мышь, сам исчезает.

Вызов: `gd_toast.show_toast("DNS переключён на Cloudflare", kind="ok")`.
"""
from __future__ import annotations

import os
import sys

try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
except Exception:  # noqa: BLE001
    pass

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_theme as th  # noqa: E402

LIFETIME_MS = 3500
MARGIN = 18
GAP = 8

COLORS = {
    "ok": th.GOOD,
    "warn": th.WARN,
    "error": "#E08B7A",
    "info": th.ACCENT,
}


class Toast(QWidget):
    def __init__(self, text: str, kind: str = "ok"):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(f"""
            QWidget {{ background: {th.SURFACE}; border: 1px solid {th.RULE};
                       border-radius: 8px; }}
            QLabel {{ color: {th.INK}; font-family: "Bahnschrift", "Segoe UI", Arial;
                      font-size: 11px; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 9, 14, 9)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setMaximumWidth(360)
        lay.addWidget(label)
        self._label = label
        self._color = COLORS.get(kind, th.ACCENT)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        from PyQt5.QtGui import QColor, QPainter

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(self._color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(14, 13, 7, 7)


_stack: list[Toast] = []
_suppressed = False


def suppress(on: bool = True):
    """На время массового действия (сброс всего) — не плодить тосты."""
    global _suppressed
    _suppressed = bool(on)


def _reposition():
    app = QApplication.instance()
    if app is None:
        return
    screen = app.primaryScreen()
    if screen is None:
        return
    area = screen.availableGeometry()
    y = area.bottom() - MARGIN
    for toast in reversed(_stack):
        toast.adjustSize()
        h = toast.height()
        w = toast.width()
        toast.move(area.right() - w - MARGIN, y - h)
        y -= h + GAP


def _drop(toast: Toast):
    if toast in _stack:
        _stack.remove(toast)
    toast.close()
    toast.deleteLater()
    _reposition()


def show_toast(text: str, kind: str = "ok"):
    """Показать тост. Без активного QApplication — тихо ничего не делаем."""
    app = QApplication.instance()
    if app is None or not text or _suppressed:
        return
    try:
        toast = Toast(text, kind)
        toast.show()
        _stack.append(toast)
        _reposition()
        QTimer.singleShot(LIFETIME_MS, lambda t=toast: _drop(t))
    except Exception:  # noqa: BLE001
        pass
