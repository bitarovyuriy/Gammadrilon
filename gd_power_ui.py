# -*- coding: utf-8 -*-
"""Экран «Питание CPU» (Gammadrilon v3).

Связь с системой — через процесс-компаньон gd_power_service.py (named pipe):
окно поднимает его при первом обращении, при закрытии окна просит откат
и останавливает компаньон (авто-откат при выходе — по ТЗ).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import deque
from multiprocessing.connection import Client

# фикс PyQt5 для не-ASCII пути к окружению (см. комментарий в main.py)
try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
except Exception:
    pass

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

import gd_theme as th
import gd_i18n as i18n  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE_NAME = r"\\.\pipe\GammadrilonPower"

BOOST_LABELS = [i18n.t("Выключен"), i18n.t("Включён"), i18n.t("Агрессивный"), i18n.t("Эффективный"), i18n.t("Эффективный агрессивный")]
COOL_LABELS = [i18n.t("Пассивное"), i18n.t("Активное")]
PROFILE_NAMES = {"eco": i18n.t("ЭКО"), "normal": i18n.t("НОРМАЛ"), "boost": i18n.t("БУСТ"), "custom": i18n.t("СВОЙ")}

_TURBO_TIP = (
    i18n.t("99% — классический способ отключить Turbo Boost: Windows держит частоту ниже последнего P-state. На Intel работает предсказуемо, на AMD — частично, зависит от CPPC/PBO и настроек BIOS. Гарантий по температуре нет — смотрите фактические сенсоры ниже.")
)


# --------------------------------------------------------------------------
# клиент компаньона
# --------------------------------------------------------------------------


class PowerClient:
    def __init__(self, pipe: str = PIPE_NAME):
        self.pipe = pipe
        self._conn = None
        self._proc = None

    def ensure(self) -> bool:
        if self._conn is not None:
            return True
        try:
            self._conn = Client(address=self.pipe)
            return True
        except OSError:
            pass
        svc = os.path.join(HERE, "gd_power_service.py")
        if getattr(sys, "frozen", False):
            # в собранном exe сервис — тот же исполняемый файл с флагом режима
            cmd = [sys.executable, "--power-service", "--ui-pid", str(os.getpid()), "--pipe", self.pipe]
            cwd = None
        else:
            cmd = [sys.executable, svc, "--ui-pid", str(os.getpid()), "--pipe", self.pipe]
            cwd = HERE
        self._proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        for _ in range(40):
            time.sleep(0.2)
            try:
                self._conn = Client(address=self.pipe)
                return True
            except OSError:
                if self._proc.poll() is not None:
                    return False
        return False

    def call(self, obj: dict, timeout: float = 10.0) -> dict:
        if not self.ensure():
            return {"ok": False, "error": i18n.t("компаньон не запустился")}
        try:
            self._conn.send(obj)
            if not self._conn.poll(timeout):
                self._conn = None
                return {"ok": False, "error": i18n.t("таймаут ответа компаньона")}
            return self._conn.recv()
        except (OSError, EOFError):
            self._conn = None
            return {"ok": False, "error": i18n.t("связь с компаньоном потеряна")}

    def shutdown(self, restore: bool = True) -> None:
        if self._conn is not None:
            try:
                self.call({"cmd": "shutdown", "restore": restore})
            except Exception:  # noqa: BLE001
                pass
        self._conn = None


# --------------------------------------------------------------------------
# виджеты
# --------------------------------------------------------------------------


class Sparkline(QWidget):
    """Мини-график значений (частота CPU)."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(46)
        self._vals: deque = deque(maxlen=90)

    def push(self, v):
        self._vals.append(v)
        self.update()

    def paintEvent(self, _):
        if len(self._vals) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        vals = list(self._vals)
        vmin = min(vals) * 0.95
        vmax = max(vals) * 1.05
        rng = max(vmax - vmin, 1.0)
        path = QPainterPath()
        for i, v in enumerate(vals):
            x = 1 + i / (len(vals) - 1) * (w - 2)
            y = h - 3 - (v - vmin) / rng * (h - 6)
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        p.setPen(QPen(QColor(th.ACCENT), 1.6))
        p.drawPath(path)


def make_card(title: str, tip: str = ""):
    frame = QFrame()
    frame.setObjectName("Card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 12, 14, 14)
    lay.setSpacing(10)
    if title:
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel(title)
        lbl.setObjectName("CardLabel")
        row.addWidget(lbl)
        row.addStretch(1)
        if tip:
            info = QLabel("ⓘ")
            info.setObjectName("Hint")
            info.setToolTip(tip)
            info.setCursor(Qt.WhatsThisCursor)
            row.addWidget(info)
        lay.addLayout(row)
    return frame, lay


# --------------------------------------------------------------------------
# окно
# --------------------------------------------------------------------------


class PowerWindow(QWidget):
    def __init__(self, standalone: bool = True):
        super().__init__()
        self.setObjectName("PanelRoot")
        self._standalone = standalone
        if standalone:
            self.setWindowTitle(i18n.t("Gammadrilon — Питание CPU"))
            self.resize(470, 680)
        # собственный стиль — работает и при встраивании вкладкой в главное окно
        self.setStyleSheet(th.STYLESHEET)
        self.client = PowerClient()
        self._profile = None
        self._orig_values = None
        self._custom_mode = False
        self._last_mhz = None
        self._freq_before = None

        self._started = False
        self._build()

        self._sensor_timer = QTimer(self)
        self._sensor_timer.timeout.connect(self._tick)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)

        if standalone:
            self.activate()

    def activate(self):
        """Вкладка стала активной (или окно открылось): компаньон + сенсоры."""
        if not self._started:
            self._started = True
            self._refresh_status()
        self._sensor_timer.start(1000)
        self._status_timer.start(5000)

    def deactivate(self):
        """Вкладка неактивна — сенсоры не тикают зря."""
        self._sensor_timer.stop()
        self._status_timer.stop()

    # -- построение --------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(14)

        head = QHBoxLayout()
        self._title = QLabel(i18n.t("Питание CPU"))
        self._title.setObjectName("Title")
        # во вкладке заголовок дублировал бы название таба — прячем
        self._title.setVisible(self._standalone)
        head.addWidget(self._title)
        head.addStretch(1)
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("Hint")
        self.status_name = QLabel("—")
        self.status_name.setObjectName("Hint")
        head.addWidget(self.status_dot)
        head.addWidget(self.status_name)
        root.addLayout(head)

        # --- профиль ---
        card, lay = make_card(
            i18n.t("ПРОФИЛЬ"),
            i18n.t("Профиль применяется к схеме питания через её клон — исходная схема не меняется, откат в один клик. Значения пишутся и в AC, и в DC."),
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self.profile_group = QButtonGroup(self)
        self.profile_btns = {}
        for pid, label in (("eco", i18n.t("ЭКО 50%")), ("normal", i18n.t("НОРМА 99%")),
                           ("boost", i18n.t("БУСТ 100%")), ("custom", i18n.t("СВОЙ"))):
            b = QPushButton(label)
            b.setObjectName("Segment")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, p=pid: self._on_profile_click(p))
            self.profile_group.addButton(b)
            self.profile_btns[pid] = b
            row.addWidget(b)
        lay.addLayout(row)

        self.profile_desc = QLabel(i18n.t("Профиль не применён — активна исходная схема."))
        self.profile_desc.setObjectName("Hint")
        self.profile_desc.setWordWrap(True)
        lay.addWidget(self.profile_desc)
        root.addWidget(card)

        # --- сейчас ---
        card2, lay2 = make_card(
            i18n.t("СЕЙЧАС"),
            i18n.t("Частота — оценка: номинал × % производительности (счётчик PDH). Температура недоступна без дополнительного ПО (датчик не отдаётся системой)."),
        )
        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self.lbl_freq = QLabel("—")
        self.lbl_load = QLabel("—")
        self.lbl_perf = QLabel("—")
        self.lbl_temp = QLabel(i18n.t("н/д"))
        for lbl, cap in ((self.lbl_freq, i18n.t("частота")), (self.lbl_load, i18n.t("загрузка")),
                         (self.lbl_perf, i18n.t("производит.")), (self.lbl_temp, i18n.t("температура"))):
            col = QVBoxLayout()
            col.setSpacing(2)
            lbl.setObjectName("Value")
            c = QLabel(cap)
            c.setObjectName("Hint")
            col.addWidget(lbl)
            col.addWidget(c)
            metrics.addLayout(col)
        metrics.addStretch(1)
        lay2.addLayout(metrics)
        self.spark = Sparkline()
        lay2.addWidget(self.spark)
        root.addWidget(card2)

        # --- точная настройка ---
        card3, lay3 = make_card(
            i18n.t("ТОЧНАЯ НАСТРОЙКА"),
            i18n.t("Точные значения профиля. «СВОЙ» — применить произвольную комбинацию."),
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        grid.addWidget(self._hint(i18n.t("Макс. состояние ⓘ"), _TURBO_TIP), 0, 0)
        self.sl_max = QSlider(Qt.Horizontal)
        self.sl_max.setRange(5, 100)
        self.sl_max.setToolTip(_TURBO_TIP)
        self.lbl_max = QLabel("100%")
        self.lbl_max.setObjectName("Value")
        self.lbl_max.setToolTip(_TURBO_TIP)
        grid.addWidget(self.sl_max, 0, 1)
        grid.addWidget(self.lbl_max, 0, 2)
        self.sl_max.valueChanged.connect(lambda v: self.lbl_max.setText(f"{v}%"))

        grid.addWidget(self._hint("EPP", i18n.t("Приоритет энергосбережения: 0 — максимальная производительность, 100 — максимальная экономия.")), 1, 0)
        self.sl_epp = QSlider(Qt.Horizontal)
        self.sl_epp.setRange(0, 100)
        self.lbl_epp = QLabel("0")
        self.lbl_epp.setObjectName("Value")
        grid.addWidget(self.sl_epp, 1, 1)
        grid.addWidget(self.lbl_epp, 1, 2)
        self.sl_epp.valueChanged.connect(lambda v: self.lbl_epp.setText(str(v)))

        lay3.addLayout(grid)
        root.addWidget(card3)

        # --- дополнительно ---
        card4, lay4 = make_card(
            i18n.t("ДОПОЛНИТЕЛЬНО"),
            i18n.t("Режим усиления, охлаждение и применение своей комбинации."),
        )
        grid4 = QGridLayout()
        grid4.setHorizontalSpacing(12)
        grid4.setVerticalSpacing(10)

        grid4.addWidget(self._hint(i18n.t("Режим усиления")), 0, 0)
        self.cb_boost = QComboBox()
        self.cb_boost.addItems(BOOST_LABELS)
        grid4.addWidget(self.cb_boost, 0, 1, 1, 2)

        grid4.addWidget(self._hint(i18n.t("Охлаждение")), 1, 0)
        self.cb_cool = QComboBox()
        self.cb_cool.addItems(COOL_LABELS)
        grid4.addWidget(self.cb_cool, 1, 1, 1, 2)

        lay4.addLayout(grid4)

        row3 = QHBoxLayout()
        self.btn_apply_custom = QPushButton(i18n.t("Применить как «СВОЙ»"))
        self.btn_apply_custom.setObjectName("PrimaryButton")
        self.btn_apply_custom.clicked.connect(self._apply_custom)
        row3.addWidget(self.btn_apply_custom)
        row3.addStretch(1)
        lay4.addLayout(row3)
        root.addWidget(card4)

        # --- низ ---
        bottom = QHBoxLayout()
        self.btn_restore = QPushButton(i18n.t("Вернуть как было"))
        self.btn_restore.clicked.connect(self._restore)
        bottom.addWidget(self.btn_restore)
        bottom.addStretch(1)
        root.addLayout(bottom)

        self.bottom_status = QLabel("")
        self.bottom_status.setObjectName("Hint")
        self.bottom_status.setWordWrap(True)
        root.addWidget(self.bottom_status)

        root.addStretch(1)

    @staticmethod
    def _hint(text: str, tip: str = "") -> QLabel:
        lbl = QLabel(text)
        if tip:
            lbl.setToolTip(tip)
        return lbl

    # -- действия ----------------------------------------------------------
    def _on_profile_click(self, pid: str):
        if pid == "custom":
            self._custom_mode = True
            self.profile_desc.setText(
                i18n.t("Режим «СВОЙ»: настройте параметры ниже и нажмите «Применить как «СВОЙ»».")
            )
            return
        self._custom_mode = False
        self._call_apply({"cmd": "apply", "profile": pid})

    def _apply_custom(self):
        custom = {
            "max_state": self.sl_max.value(),
            "boost_mode": self.cb_boost.currentIndex(),
            "epp": self.sl_epp.value(),
            "cool_policy": self.cb_cool.currentIndex(),
        }
        self._call_apply({"cmd": "apply", "profile": "custom", "custom": custom})

    def _call_apply(self, payload: dict):
        self._freq_before = self._last_mhz
        resp = self.client.call(payload)
        if resp.get("ok"):
            self._set_bottom_status(i18n.t("Профиль применён. Проверяю реакцию частоты…"), ok=True)
            self._refresh_status()
            self._journal_apply(payload)
            # реакцию частоты проверяем только когда сенсоры живы (вкладка открыта)
            if self._sensor_timer.isActive():
                QTimer.singleShot(4500, self._freq_reaction_check)
        else:
            self._set_bottom_status(i18n.t("Ошибка: {v0}", v0=resp.get('error')), ok=False)

    def _journal_apply(self, payload: dict):
        """Записать изменение в журнал (ТЗ 5.5)."""
        try:
            import gd_journal

            pid = payload.get("profile")
            name = PROFILE_NAMES.get(pid, str(pid))
            before = after = None
            o = self._orig_values or {}
            if o.get("max_state", {}).get("ac") is not None:
                before = i18n.t("макс {v0}%", v0=o['max_state']['ac'])
            st = self.client.call({"cmd": "status"})
            if st.get("ok"):
                v = (st["data"].get("values") or {}).get("max_state", {}).get("ac")
                if v is not None:
                    after = i18n.t("макс {v0}%", v0=v)
            gd_journal.record(
                "cpu_profile", name, before, after,
                why=i18n.t("профиль питания CPU"),
                source=getattr(self, "_source", "UI"),
            )
            import gd_toast
            import gd_i18n as i18n

            tail = f" ({before} → {after})" if before and after else ""
            gd_toast.show_toast(i18n.t("toast.cpu_profile", name=name, tail=tail), "ok")
        except Exception:  # noqa: BLE001
            pass

    def _freq_reaction_check(self):
        """Честность: показать, отреагировала ли частота на профиль (ТЗ 3.3)."""
        if not self._freq_before or not self._last_mhz:
            return
        delta = abs(self._last_mhz - self._freq_before) / self._freq_before
        if delta < 0.03:
            self._set_bottom_status(
                i18n.t("Профиль записан, но частота пока не отреагировала. Вероятные причины: AMD PBO / настройки BIOS / драйверный сервис перехватывает управление."),
                ok=False,
            )
        else:
            self._set_bottom_status(
                i18n.t("Реакция есть: {v0:.2f} → {v1:.2f} ГГц", v0=self._freq_before / 1000, v1=self._last_mhz / 1000),
                ok=True,
            )

    def _restore(self):
        resp = self.client.call({"cmd": "restore"})
        if resp.get("ok"):
            self._set_bottom_status(i18n.t("Схема возвращена к исходной."), ok=True)
            self._refresh_status()
            try:
                import gd_journal

                gd_journal.record("restore", i18n.t("питание CPU"),
                                  why=i18n.t("возврат исходной схемы"),
                                  source=getattr(self, "_source", "UI"))
                import gd_toast
                import gd_i18n as i18n

                gd_toast.show_toast(i18n.t("toast.cpu_restore"), "info")
            except Exception:  # noqa: BLE001
                pass
        else:
            self._set_bottom_status(i18n.t("Ошибка отката: {v0}", v0=resp.get('error')), ok=False)

    # -- внешнее управление (трей, палитра команд) -------------------------
    def apply_profile(self, pid: str, source: str = i18n.t("трей")):
        """Применить профиль извне — как действие, а не загрузку пресета в контролы."""
        self._source = source
        if pid == "restore":
            self._restore()
            return
        self._custom_mode = False
        self._call_apply({"cmd": "apply", "profile": pid})

    def apply_custom(self, source: str = i18n.t("трей")):
        self._source = source
        self._custom_mode = True
        self._apply_custom()

    def current_profile(self):
        return getattr(self, "_profile", None)

    def _set_bottom_status(self, text: str, ok: bool = True):
        color = th.MUTED if ok else th.WARN
        self.bottom_status.setStyleSheet(f"color: {color};")
        self.bottom_status.setText(text)

    # -- обновление --------------------------------------------------------
    def _refresh_status(self):
        resp = self.client.call({"cmd": "status"})
        if not resp.get("ok"):
            self.status_name.setText(i18n.t("компаньон недоступен"))
            return
        data = resp["data"]
        profile = data.get("profile")
        self._profile = profile
        self._orig_values = data.get("original_values")

        for pid, btn in self.profile_btns.items():
            btn.setChecked(pid == profile and not self._custom_mode)

        if profile:
            self.status_dot.setStyleSheet(f"color: {th.GOOD};")
            self.status_name.setText(f"{PROFILE_NAMES.get(profile, profile)}")
        else:
            self.status_dot.setStyleSheet(f"color: {th.MUTED};")
            self.status_name.setText(i18n.t("исходная схема"))

        # значения слайдеров — из активной схемы
        vals = data.get("values") or {}
        if not self._custom_mode:
            if vals.get("max_state", {}).get("ac") is not None:
                self.sl_max.setValue(vals["max_state"]["ac"])
            if vals.get("epp", {}).get("ac") is not None:
                self.sl_epp.setValue(vals["epp"]["ac"])
            bm = vals.get("boost_mode", {}).get("ac")
            if bm is not None and 0 <= bm < self.cb_boost.count():
                self.cb_boost.setCurrentIndex(bm)
            cp = vals.get("cool_policy", {}).get("ac")
            if cp is not None and 0 <= cp < self.cb_cool.count():
                self.cb_cool.setCurrentIndex(cp)

        # описание
        if profile and self._orig_values:
            o, n = self._orig_values, vals
            parts = []
            for key, label, unit in (("max_state", i18n.t("Макс"), "%"), ("boost_mode", "Boost", ""),
                                     ("epp", "EPP", ""), ("cool_policy", i18n.t("Охлаждение"), "")):
                ov, nv = o.get(key, {}).get("ac"), n.get(key, {}).get("ac")
                if ov is not None and nv is not None:
                    parts.append(f"{label}: {ov}{unit} → {nv}{unit}")
            self.profile_desc.setText(i18n.t("Применено — ") + " · ".join(parts) if parts else i18n.t("Профиль применён."))
        elif not profile:
            self.profile_desc.setText(i18n.t("Профиль не применён — активна исходная схема."))

    def _tick(self):
        s = self._sensors_sample()
        if s["mhz"]:
            self._last_mhz = s["mhz"]
            self.lbl_freq.setText(i18n.t("{v0:.2f} ГГц", v0=s['mhz'] / 1000))
            self.spark.push(s["mhz"])
        else:
            self.lbl_freq.setText("—")
        self.lbl_load.setText(f"{s['load_pct']:.0f}%" if s["load_pct"] is not None else "—")
        self.lbl_perf.setText(f"{s['perf_pct']:.0f}%" if s["perf_pct"] is not None else "—")
        self.lbl_temp.setText(i18n.t("н/д") if s["temp_c"] is None else f"{s['temp_c']:.0f}°C")

    def _sensors_sample(self) -> dict:
        if not hasattr(self, "_sensors"):
            from gd_sensors import CpuSensors

            self._sensors = CpuSensors()
        return self._sensors.sample()

    # -- закрытие ----------------------------------------------------------
    def closeEvent(self, event):
        # standalone-режим: закрытие окна = выход приложения -> откат.
        # Во встроенном режиме компаньон живёт вместе с главным приложением,
        # откат выполняется при его выходе (quit_app) или watchdog'ом.
        if self._standalone:
            self.client.shutdown(restore=True)
        super().closeEvent(event)


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyleSheet(th.STYLESHEET)
    win = PowerWindow()
    win.show()
    sys.exit(app.exec_())
