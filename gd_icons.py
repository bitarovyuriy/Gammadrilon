# -*- coding: utf-8 -*-
"""Линейные иконки сайдбара (SVG из макета design/variant-a-mono.html).

QT_PLUGIN_PATH-фикс: модуль может стать первым импортом PyQt5.
"""
from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    try:
        import sysconfig

        _qt_bin = os.path.join(sysconfig.get_paths()["purelib"], "PyQt5", "Qt5", "bin")
        if os.path.isdir(_qt_bin) and _qt_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = _qt_bin + os.pathsep + os.environ.get("PATH", "")
            os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_qt_bin, "plugins"))
            os.environ.setdefault(
                "QT_QPA_PLATFORM_PLUGIN_PATH", os.path.join(_qt_bin, "plugins", "platforms")
            )
    except Exception:  # noqa: BLE001
        pass

_SVG = {
    # 24x24, stroke 1.8, currentColor — как в макете
    "cpu": (
        '<rect x="5" y="5" width="14" height="14" rx="2"/>'
        '<rect x="9" y="9" width="6" height="6" rx="1"/>'
    ),
    "gpu": (
        '<rect x="3" y="7" width="18" height="11" rx="2"/>'
        '<circle cx="12" cy="12.5" r="3"/>'
    ),
    "gamma": (
        '<circle cx="12" cy="12" r="8"/>'
        '<path d="M12 4a8 8 0 0 0 0 16z" fill="currentColor" stroke="none"/>'
    ),
    "rules": '<path d="M4 6h16M4 12h10M4 18h13"/><circle cx="18" cy="12" r="2"/>',
    "utils": (
        '<path d="M14.5 5.5a4 4 0 0 0-5.6 5.1L4 15.5 8.5 20l4.9-4.9'
        'a4 4 0 0 0 5.1-5.6l-2.6 2.6-2.4-.6-.6-2.4z"/>'
    ),
    "dns": (
        '<circle cx="12" cy="12" r="8"/>'
        '<path d="M4 12h16M12 4c2.5 2.2 2.5 13.8 0 16M12 4c-2.5 2.2-2.5 13.8 0 16"/>'
    ),
    # главный экран: дом — стартовая вкладка
    "home": (
        '<path d="M4 11.2 12 4.4l8 6.8"/>'
        '<path d="M6.4 10.4V19.6h11.2v-9.2"/>'
    ),
    # кнопки титлбара
    "palette": (
        '<path d="M4 6h16M4 12h16M4 18h10"/>'
        '<circle cx="18" cy="18" r="3"/>'
    ),
    "api": (
        '<rect x="4" y="5" width="16" height="5.5" rx="1.5"/>'
        '<rect x="4" y="13.5" width="16" height="5.5" rx="1.5"/>'
        '<path d="M8 7.75h.01M8 16.25h.01"/>'
    ),
    "zapret": (
        '<path d="M4 12h9"/>'
        '<path d="M9.5 8.5 13 12l-3.5 3.5"/>'
        '<path d="M18 5.5v13"/>'
    ),
    "journal": '<path d="M5 4h11l3 3v13H5z"/><path d="M8 9h8M8 13h8M8 17h5"/>',
    "theme": '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none"/>',
}


def icon(name: str, size: int = 15, color: str = "#C4C4C4"):
    """QIcon из встроенного SVG-контура (цвет подставляется в currentColor)."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QIcon, QPainter, QPixmap
    from PyQt5.QtSvg import QSvgRenderer

    body = _SVG.get(name)
    if body is None:
        return QIcon()
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )
    renderer = QSvgRenderer(svg.encode("utf-8"))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return QIcon(pm)
