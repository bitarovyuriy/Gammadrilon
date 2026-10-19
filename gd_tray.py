# -*- coding: utf-8 -*-
"""Трей-ассистент (ТЗ 5.1): живая иконка-индикатор, тултип, режим-мастер.

Иконка окрашивается по текущему профилю и коротко мигает при смене.
Тултип показывает живые данные (обновляется раз в секунду, без всплывающих окон).
Правый клик — режим-мастер: питание CPU / питание GPU / утилиты / выход.
"""
from __future__ import annotations

import os
import sys

# --- PyQt5 и не-ASCII путь к venv (exit 127 без traceback) ---
try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
except Exception:  # noqa: BLE001
    pass

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QAction, QActionGroup, QMenu, QSystemTrayIcon

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_theme as th  # noqa: E402
import gd_i18n as i18n  # noqa: E402

CPU_PROFILES = (("eco", "ЭКО"), ("normal", "НОРМАЛ"), ("boost", "БУСТ"))
GPU_PROFILES = (("quiet", "Тихий"), ("default", "Дефолт"),
                ("game", "Игровой"), ("max", "Макс"))

PROFILE_LABELS_EN = {
    "eco": "ECO", "normal": "NORMAL", "boost": "BOOST",
    "quiet": "Quiet", "default": "Default", "game": "Gaming", "max": "Max",
}


def _plabel(pid: str, ru: str) -> str:
    """Название профиля: при английском языке — английское."""
    return PROFILE_LABELS_EN.get(pid, ru) if i18n.current() == "en" else ru

PROFILE_COLOR = {
    "eco": th.GOOD,
    "normal": th.ACCENT,
    "boost": th.WARN,
    "quiet": th.GOOD,
    "default": th.ACCENT,
    "game": th.WARN,
    "max": "#D97B5B",
}
NEUTRAL = th.MUTED


class TrayAssistant(QSystemTrayIcon):
    cpuProfileRequested = pyqtSignal(str)
    gpuProfileRequested = pyqtSignal(str)
    perfToggleRequested = pyqtSignal()
    showRequested = pyqtSignal()
    paletteRequested = pyqtSignal()
    journalRequested = pyqtSignal()
    rulesRequested = pyqtSignal()
    utilsRequested = pyqtSignal()
    themeRequested = pyqtSignal()
    resetEverythingRequested = pyqtSignal()
    quitRequested = pyqtSignal()

    def __init__(self, icon_path=None, parent=None):
        super().__init__(parent)
        self._cpu = None
        self._gpu = None
        self._blink_left = 0
        self._base_icon = None
        if icon_path and os.path.exists(str(icon_path)):
            try:
                self._base_icon = QIcon(str(icon_path))
            except Exception:  # noqa: BLE001
                self._base_icon = None

        self._cpu_actions: dict[str, QAction] = {}
        self._gpu_actions: dict[str, QAction] = {}
        self._perf_limited = False     # галка «Переключить производительность»
        self._tip: str | None = None       # кэш тултипа: explorer не дёргаем зря
        self._icon_key = None              # кэш отрисовки иконки (цвет + мигание)
        self._build_menu()

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(180)
        self._blink_timer.timeout.connect(self._blink_tick)

        self.activated.connect(self._on_activated)
        self.refresh(None, None, None)
        self.show()

    # -- меню (режим-мастер) ----------------------------------------------
    def _build_menu(self):
        menu = QMenu()

        act = menu.addAction(i18n.t("tray.open"))
        act.triggered.connect(self.showRequested.emit)

        menu.addSeparator()

        cpu_menu = menu.addMenu(i18n.t("tray.cpu_menu"))
        grp = QActionGroup(self)
        grp.setExclusive(True)
        for pid, label in CPU_PROFILES:
            a = cpu_menu.addAction(_plabel(pid, label))
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, p=pid: self.cpuProfileRequested.emit(p))
            grp.addAction(a)
            self._cpu_actions[pid] = a
        self._cpu_restore = cpu_menu.addAction(i18n.t("tray.cpu_restore"))
        self._cpu_restore.triggered.connect(lambda: self.cpuProfileRequested.emit("restore"))

        gpu_menu = menu.addMenu(i18n.t("tray.gpu_menu"))
        ggrp = QActionGroup(self)
        ggrp.setExclusive(True)
        for pid, label in GPU_PROFILES:
            a = gpu_menu.addAction(_plabel(pid, label))
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, p=pid: self.gpuProfileRequested.emit(p))
            ggrp.addAction(a)
            self._gpu_actions[pid] = a
        self._gpu_restore = gpu_menu.addAction(i18n.t("tray.gpu_restore"))
        self._gpu_restore.triggered.connect(lambda: self.gpuProfileRequested.emit("restore"))

        # переключатель производительности — то же действие, что и горячая клавиша
        # (пара профилей CPU+GPU): галка = ограничения включены
        self._perf_act = menu.addAction(i18n.t("tray.perf_toggle"))
        self._perf_act.setCheckable(True)
        self._perf_act.setChecked(self._perf_limited)
        self._perf_act.triggered.connect(self.perfToggleRequested.emit)

        util_menu = menu.addMenu(i18n.t("tray.utils_menu"))
        a = util_menu.addAction(i18n.t("tray.palette"))
        a.triggered.connect(self.paletteRequested.emit)
        a = util_menu.addAction(i18n.t("tray.journal"))
        a.triggered.connect(self.journalRequested.emit)
        a = util_menu.addAction(i18n.t("tray.rules"))
        a.triggered.connect(self.rulesRequested.emit)
        a = util_menu.addAction(i18n.t("tray.memory"))
        a.triggered.connect(self.utilsRequested.emit)
        self._theme_act = util_menu.addAction(i18n.t("tray.theme_light"))
        self._theme_act.setCheckable(True)
        self._theme_act.triggered.connect(self.themeRequested.emit)

        # «вернуть всё как было» — то же действие, что кнопка в «Утилитах»;
        # отделено и стоит в конце: промахнуться по нему не должно быть легко
        util_menu.addSeparator()
        a = util_menu.addAction(i18n.t("tray.reset_all"))
        a.triggered.connect(self.resetEverythingRequested.emit)

        menu.addSeparator()
        act = menu.addAction(i18n.t("tray.quit"))
        act.triggered.connect(self.quitRequested.emit)

        self._menu = menu
        self.setContextMenu(menu)

    # -- иконка ------------------------------------------------------------
    def set_theme_checked(self, light: bool):
        """Галка «Светлая тема» — текущее состояние."""
        try:
            self._theme_act.setChecked(bool(light))
        except Exception:  # noqa: BLE001
            pass

    def set_perf_limited(self, limited: bool):
        """Галка «Переключить производительность» — ограничения включены?

        Состояние хранится и в самом трее: меню пересобирается при смене языка
        (`retranslate`), и без этого галка терялась бы при каждой смене.
        """
        self._perf_limited = bool(limited)
        try:
            self._perf_act.setChecked(self._perf_limited)
        except Exception:  # noqa: BLE001
            pass

    def retranslate(self):
        """Перестроить меню после смены языка."""
        try:
            self._build_menu()
        except Exception:  # noqa: BLE001
            pass

    def _make_icon(self, color: str, invert: bool = False) -> QIcon:
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        ring = QColor(th.PAPER if invert else color)
        fill = QColor(color if invert else th.SURFACE)
        p.setBrush(QBrush(fill))
        p.setPen(QPen(ring, 7))
        p.drawEllipse(8, 8, 48, 48)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(th.PAPER if invert else color)))
        p.drawEllipse(24, 24, 16, 16)
        p.end()
        return QIcon(pm)

    def _apply_icon(self):
        """Перерисовать иконку — только если она реально изменилась.

        ``setIcon`` уходит в explorer (shell_NotifyIcon), поэтому дёргать его
        каждую секунду при неизменном состоянии — чистая трата.
        """
        color = PROFILE_COLOR.get(self._cpu) or PROFILE_COLOR.get(self._gpu) or NEUTRAL
        blink = self._blink_left > 0
        key = (color, blink, th.PAPER, th.SURFACE)   # тема тоже меняет вид точки
        if key == self._icon_key:
            return
        self._icon_key = key
        self.setIcon(self._make_icon(color, invert=blink))

    def _start_blink(self):
        self._blink_left = 3
        if not self._blink_timer.isActive():
            self._blink_timer.start()
        self._apply_icon()

    def _blink_tick(self):
        self._blink_left -= 1
        if self._blink_left <= 0:
            self._blink_timer.stop()
            self._blink_left = 0
        self._apply_icon()

    # -- состояние ---------------------------------------------------------
    def refresh(self, cpu_profile, gpu_profile, tooltip: str | None):
        changed = (cpu_profile, gpu_profile) != (self._cpu, self._gpu)
        self._cpu, self._gpu = cpu_profile, gpu_profile
        for pid, a in self._cpu_actions.items():
            a.setChecked(pid == cpu_profile)
        if hasattr(self, "_cpu_restore"):
            self._cpu_restore.setChecked(cpu_profile is None)
        for pid, a in self._gpu_actions.items():
            a.setChecked(pid == gpu_profile)
        if hasattr(self, "_gpu_restore"):
            self._gpu_restore.setChecked(gpu_profile is None)
        if changed:
            self._start_blink()
        else:
            self._apply_icon()
        if tooltip is not None and tooltip != self._tip:
            self._tip = tooltip
            self.setToolTip(tooltip)     # explorer трогаем только по изменению

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.showRequested.emit()


CPU_MAX = {"eco": 50, "normal": 99, "boost": 100}


def build_tooltip(cpu_profile, sensors, gpu=None, gpu_profile=None, ram_pct=None) -> str:
    """«ЭКО · макс 50% · 3.1 ГГц · RAM 62% · GPU 39 °C»."""
    names = {"eco": i18n.t("ЭКО"), "normal": i18n.t("НОРМАЛ"), "boost": i18n.t("БУСТ")}
    head = names.get(cpu_profile, i18n.t("Схема по умолчанию"))
    parts = [head]
    if cpu_profile in CPU_MAX:
        parts.append(i18n.t("макс {v0}%", v0=CPU_MAX[cpu_profile]))
    if sensors:
        if sensors.get("mhz"):
            parts.append(i18n.t("{v0:.2f} ГГц", v0=sensors['mhz'] / 1000))
        if sensors.get("temp_c") is not None:
            parts.append(f"{sensors['temp_c']:.0f} °C")
    if ram_pct is not None:
        parts.append(f"RAM {ram_pct:.0f}%")
    if gpu:
        gl = []
        if gpu.get("temp") is not None:
            gl.append(f"{gpu['temp']:.0f} °C")
        if gpu.get("pl") is not None and gpu.get("pl_max") is not None:
            gl.append(i18n.t("{v0:.0f}/{v1:.0f} Вт", v0=gpu['pl'], v1=gpu['pl_max']))
        if gl:
            parts.append("GPU " + ", ".join(gl))
    return " · ".join(parts)
