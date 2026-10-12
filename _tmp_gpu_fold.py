# -*- coding: utf-8 -*-
"""Видно ли подтверждение лимита GPU без прокрутки страницы.

Собирает ту же оболочку, что main.py._add_page: заголовок + QScrollArea
с GpuWidget внутри, окно 910x700 (compact_size), и измеряет, где оказываются
кнопки «Применить»/«Оставить» и строка статуса относительно вьюпорта.
Плюс сохраняет снимок вьюпорта в build/gd_gpu_fold.png.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sysconfig as _sysconfig  # noqa: E402

_site = _sysconfig.get_paths().get("purelib", "")
_qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
    os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import gd_i18n as i18n  # noqa: E402

i18n.init_for_process()

import gd_metrics  # noqa: E402
from gd_gpu_ui import GpuWidget  # noqa: E402

app = QApplication(sys.argv)

shell = QWidget()
shell.setObjectName("PanelRoot")
outer = QVBoxLayout(shell)
outer.setContentsMargins(0, 0, 0, 0)

head = QWidget()
hl = QVBoxLayout(head)
hl.setContentsMargins(22, 16, 22, 6)
hl.addWidget(QLabel(i18n.t("tab.gpu.title")))
hl.addWidget(QLabel(i18n.t("tab.gpu.sub")))
outer.addWidget(head)

page = GpuWidget(standalone=False)
scroll = QScrollArea()
scroll.setObjectName("PageScroll")
scroll.setWidgetResizable(True)
scroll.setFrameShape(QScrollArea.NoFrame)
scroll.setWidget(page)
outer.addWidget(scroll, 1)

shell.resize(910, 700)          # compact_size из main.py
shell.show()
app.processEvents()

gd_metrics.refresh_now("gpu")
page._tick()
page.btn_keep.setVisible(True)   # как после успешного apply
page._set_status(i18n.t("Применено: лимит {v0} Вт. Подтвердите за {v1} с.", v0=212, v1=12))
page.profile_desc.setText(i18n.t("Лимит {v0} Вт — ожидает подтверждения.", v0=212))
page.sl_pl.setValue(212)
app.processEvents()

vp = scroll.viewport()
bar = scroll.verticalScrollBar()
print(f"окно: {shell.width()}x{shell.height()}, вьюпорт страницы: {vp.width()}x{vp.height()}")
print(f"страница sizeHint: {page.sizeHint().width()}x{page.sizeHint().height()}, "
      f"высота виджета: {page.height()}")
print(f"прокрутка нужна: {'да' if bar.maximum() > 0 else 'нет'} (диапазон 0..{bar.maximum()})")

for name, wid in (
    ("sl_pl", page.sl_pl),
    ("btn_apply", page.btn_apply),
    ("btn_keep", page.btn_keep),
    ("btn_restore", page.btn_restore),
    ("profile_desc", page.profile_desc),
    ("bottom_status", page.bottom_status),
):
    tl = wid.mapTo(vp, wid.rect().topLeft())
    bottom = tl.y() + wid.height()
    state = "видно" if 0 <= tl.y() and bottom <= vp.height() else "ЗА КАДРОМ"
    print(f"{name:14} y={tl.y():4} h={wid.height():3} низ={bottom:4} → {state}")

os.makedirs(os.path.join(HERE, "build"), exist_ok=True)
shot = os.path.join(HERE, "build", "gd_gpu_fold.png")
vp.grab().save(shot)
print("снимок:", shot, os.path.exists(shot))
