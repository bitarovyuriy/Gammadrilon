# -*- coding: utf-8 -*-
"""Экран «Питание CPU» (Gammadrilon v3).

Связь с системой — через процесс-компаньон gd_power_service.py (named pipe):
окно поднимает его при первом обращении, при закрытии окна просит откат
и останавливает компаньон (авто-откат при выходе — по ТЗ).
"""

from __future__ import annotations

import os
import sys
import time
from collections import deque
from multiprocessing.connection import Client

import gd_proc

import gd_bg

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
import gd_agent  # noqa: E402
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
            # pythonw: консольный python.exe дал бы мелькающее окно
            cmd = [gd_proc.python_exe(), svc, "--ui-pid", str(os.getpid()), "--pipe", self.pipe]
            cwd = HERE
        self._proc = gd_proc.popen(cmd, cwd=cwd)
        for _ in range(40):
            time.sleep(0.2)
            try:
                self._conn = Client(address=self.pipe)
                return True
            except OSError:
                if self._proc.poll() is not None:
                    return False
        return False

    def call_existing(self, obj: dict, timeout: float = 10.0) -> dict:
        """Спросить уже работающего компаньона — процесс не поднимая.

        Для пассивного чтения состояния: фоновый опрос не должен рождать
        процесс до первого действия пользователя (см. :func:`read_status`).
        Нет живой сессии — честный отказ, без запуска и без UAC.
        """
        if self._conn is None:
            try:
                self._conn = Client(address=self.pipe)
            except OSError:
                return {"ok": False, "error": i18n.t("компаньон не запущен")}
        try:
            self._conn.send(obj)
            if not self._conn.poll(timeout):
                self._conn = None
                return {"ok": False, "error": i18n.t("таймаут ответа компаньона")}
            return self._conn.recv()
        except (OSError, EOFError):
            self._conn = None
            return {"ok": False, "error": i18n.t("связь с компаньоном потеряна")}

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

    def alive(self) -> bool:
        """Живой ли компаньон — без запуска и без обращений к пайпу.

        Если процесс поднимали мы (:attr:`_proc`), истина — его состояние
        (``poll()`` это O(1)); если подключились к чужому экземпляру — пока
        держится соединение. Нужно честному индикатору в футе сайдбара.
        """
        proc = self._proc
        if proc is not None:
            return proc.poll() is None
        return self._conn is not None

    def shutdown(self, restore: bool = True) -> None:
        if self._conn is not None:
            try:
                self.call({"cmd": "shutdown", "restore": restore})
            except Exception:  # noqa: BLE001
                pass
        self._conn = None

    def shutdown_async(self, restore: bool = True) -> None:
        """Попросить компаньон завершиться с откатом, не дожидаясь ответа.

        Если компаньона нет (профиль не применяли) — откатывать нечего, молча
        выходим. Если есть: ждать ответа нельзя, а откат подстрахует watchdog
        компаньона по смерти UI-процесса.
        """
        if self._conn is None:
            return
        try:
            self._conn.send({"cmd": "shutdown", "restore": restore})
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._conn = None


def read_status(client: PowerClient) -> dict:
    """Состояние питания: у живого компаньона или локально (без запуска).

    Порядок честный и дешёвый: сначала спрашиваем компаньона, если он уже
    работает (``call_existing`` — без спавна), иначе читаем powercfg сами
    (:func:`gd_power.state_snapshot`). Смысл: компаньон нужен только для записи
    и отката при краше UI, а вкладка CPU открыта с самого старта — раньше её
    опрос статуса поднимал процесс до первого действия пользователя.
    """
    resp = client.call_existing({"cmd": "status"})
    if resp.get("ok"):
        return resp
    try:
        import gd_power

        return {"ok": True, "data": gd_power.state_snapshot()}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


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
            info = QLabel("i")  # глиф «ⓘ» есть не во всех шрифтах — рисуем круг стилями
            info.setObjectName("CardInfo")
            info.setAlignment(Qt.AlignCenter)
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
        self._apply_busy = False       # идёт apply/restore (фон) — блокирует кнопки
        self._leftover_checked = False  # смотрели ли профиль от прошлой сессии
        self._leftover_busy = False
        self._clones_checked = False    # искали ли свои копии схем в системе
        self._clones_busy = False
        self._clones_notified = False   # о найденных копиях сказано один раз
        self._state_note_shown = False  # о непригодном state сказано один раз
        self._clones: list = []
        self._build()

        self._sensor_timer = QTimer(self)
        self._sensor_timer.timeout.connect(self._tick)
        # статус — фоновой опрос чтением powercfg (read_status): компаньона он НЕ
        # поднимает, поэтому GUI ничего не ждёт; повторный запрос не стартует,
        # пока идёт предыдущий
        self._status_periodic = gd_bg.periodic(
            self, 5000, lambda: read_status(self.client), self._refresh_done
        )

        if standalone:
            self.activate()

    def activate(self):
        """Вкладка стала активной (или окно открылось): компаньон + сенсоры."""
        if not self._started:
            self._started = True
            self.status_name.setText(i18n.t("Читаю состояние питания…"))
            self._status_periodic.trigger()
        self._sensor_timer.start(1000)
        self._status_periodic.start()

    def deactivate(self):
        """Вкладка неактивна — сенсоры не тикают зря."""
        self._sensor_timer.stop()
        self._status_periodic.stop()

    def _set_busy(self, busy: bool) -> None:
        """Заблокировать управление, пока идёт операция (запуск компаньона + запись)."""
        for btn in self.profile_btns.values():
            btn.setEnabled(not busy)
        self.btn_apply_custom.setEnabled(not busy)
        self.btn_restore.setEnabled(not busy)
        self.btn_clean_clones.setEnabled(not busy)

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

        grid.addWidget(self._hint(i18n.t("Макс. состояние"), _TURBO_TIP), 0, 0)
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
        # кнопка видна только тогда, когда в системе нашлись свои копии схем
        # (остатки прошлых сессий) — иначе она была бы загадкой
        self.btn_clean_clones = QPushButton(i18n.t("Убрать мои копии схем"))
        self.btn_clean_clones.setObjectName("Segment")
        self.btn_clean_clones.setToolTip(
            i18n.t("Профиль применяется к копии схемы питания. Если файл состояния пропал, копия остаётся в системе — здесь её видно и можно убрать.")
        )
        self.btn_clean_clones.clicked.connect(self._clean_clones)
        self.btn_clean_clones.setVisible(False)
        bottom.addWidget(self.btn_clean_clones)
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
        self._source = "UI"
        if pid == "custom":
            self._custom_mode = True
            self.profile_desc.setText(
                i18n.t("Режим «СВОЙ»: настройте параметры ниже и нажмите «Применить как «СВОЙ»».")
            )
            return
        self._custom_mode = False
        self._call_apply({"cmd": "apply", "profile": pid})

    def _apply_custom(self):
        self._source = "UI"
        custom = {
            "max_state": self.sl_max.value(),
            "boost_mode": self.cb_boost.currentIndex(),
            "epp": self.sl_epp.value(),
            "cool_policy": self.cb_cool.currentIndex(),
        }
        self._call_apply({"cmd": "apply", "profile": "custom", "custom": custom})

    def _call_apply(self, payload: dict):
        """Применить профиль: компаньон поднимается здесь (это осознанный клик
        пользователя), а запись идёт в фоне — окно не замирает."""
        if self._apply_busy:
            gd_agent.finish(getattr(self, "_ext_done", None), False,
                            i18n.t("занято: операция уже выполняется"))
            self._ext_done = None
            return
        self._apply_busy = True
        self._freq_before = self._last_mhz
        self._set_busy(True)
        self._set_bottom_status(i18n.t("Применяю профиль CPU…"), ok=True)
        gd_bg.submit(
            lambda: self.client.call(payload),
            lambda resp: self._apply_done(resp, payload),
            parent=self,
        )

    def _apply_done(self, resp: dict, payload: dict):
        self._apply_busy = False
        self._set_busy(False)
        if resp.get("ok"):
            self._set_bottom_status(i18n.t("Профиль применён. Проверяю реакцию частоты…"), ok=True)
            self._refresh_status()
            self._journal_apply(payload)
            # реакцию частоты проверяем только когда сенсоры живы (вкладка открыта)
            if self._sensor_timer.isActive():
                QTimer.singleShot(4500, self._freq_reaction_check)
        else:
            self._set_bottom_status(i18n.t("Ошибка: {v0}", v0=resp.get('error')), ok=False)
        gd_agent.finish(getattr(self, "_ext_done", None), resp.get("ok"), resp.get("error"))
        self._ext_done = None

    def _journal_apply(self, payload: dict):
        """Записать изменение в журнал (ТЗ 5.5): дозапрос статуса — в фоне."""
        gd_bg.submit(
            lambda: read_status(self.client),
            lambda st: self._journal_write(payload, st),
            parent=self,
        )

    def _journal_write(self, payload: dict, st):
        """Собственно запись в журнал и тост — после фонового опроса статуса."""
        try:
            import gd_journal

            pid = payload.get("profile")
            name = PROFILE_NAMES.get(pid, str(pid))
            before = after = None
            o = self._orig_values or {}
            if o.get("max_state", {}).get("ac") is not None:
                before = i18n.t("макс {v0}%", v0=o['max_state']['ac'])
            if isinstance(st, dict) and st.get("ok"):
                v = (st["data"].get("values") or {}).get("max_state", {}).get("ac")
                if v is not None:
                    after = i18n.t("макс {v0}%", v0=v)
            gd_journal.record(
                "cpu_profile", name, before, after,
                why=i18n.t("профиль питания CPU"),
                source=getattr(self, "_source", "UI"),
            )
            import gd_toast

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
        """Вернуть исходную схему — компаньон отвечает в фоне."""
        if self._apply_busy:
            gd_agent.finish(getattr(self, "_ext_done", None), False,
                            i18n.t("занято: операция уже выполняется"))
            self._ext_done = None
            return
        self._apply_busy = True
        self._set_busy(True)
        self._set_bottom_status(i18n.t("Возвращаю исходную схему…"), ok=True)
        gd_bg.submit(lambda: self.client.call({"cmd": "restore"}), self._restore_done, parent=self)

    def _restore_done(self, resp: dict):
        self._apply_busy = False
        self._set_busy(False)
        if resp.get("ok"):
            self._active_profile = None
            self._set_bottom_status(i18n.t("Схема возвращена к исходной."), ok=True)
            self._refresh_status()
            try:
                import gd_journal

                gd_journal.record("restore", i18n.t("питание CPU"),
                                  why=i18n.t("возврат исходной схемы"),
                                  source=getattr(self, "_source", "UI"))
                import gd_toast

                gd_toast.show_toast(i18n.t("toast.cpu_restore"), "info")
            except Exception:  # noqa: BLE001
                pass
        else:
            self._set_bottom_status(i18n.t("Ошибка отката: {v0}", v0=resp.get('error')), ok=False)
        gd_agent.finish(getattr(self, "_ext_done", None), resp.get("ok"), resp.get("error"))
        self._ext_done = None

    # -- внешнее управление (трей, палитра команд) -------------------------
    def apply_profile(self, pid: str, source: str = i18n.t("трей"), on_done=None):
        """Применить профиль извне — как действие, а не загрузку пресета в контролы.

        `on_done(result)` — необязательный хук внешних вызовов (локальный API,
        ТЗ этап 8): один раз получает ``{"ok", "error"}`` по завершении операции,
        в том числе при отказе «занято». Из вкладки вызывается без хука.
        """
        if self._apply_busy:
            gd_agent.finish(on_done, False, i18n.t("занято: профиль уже применяется"))
            return
        self._source = source
        self._ext_done = on_done
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

    def service_alive(self) -> bool:
        """Работает ли компаньон питания прямо сейчас (для фута сайдбара).

        Компаньон ленивый: до первого применения профиля его нет, и подпись
        «Служба питания активна» в этом состоянии была бы неправдой.
        """
        return self.client.alive()

    def _set_bottom_status(self, text: str, ok: bool = True):
        color = th.MUTED if ok else th.WARN
        self.bottom_status.setStyleSheet(f"color: {color};")
        self.bottom_status.setText(text)

    # -- обновление --------------------------------------------------------
    def _refresh_status(self):
        """Перечитать состояние питания в фоне (чтение powercfg, без компаньона)."""
        self._status_periodic.trigger()

    def _refresh_done(self, resp):
        if not isinstance(resp, dict):
            resp = {}
        if not resp.get("ok"):
            self.status_name.setText(i18n.t("состояние питания недоступно"))
            return
        data = resp["data"]
        profile = data.get("profile")
        self._profile = profile
        self._orig_values = data.get("original_values")

        # файл состояния есть, но непригоден: «профиль не применён» и «откатывать
        # нечем, а копия схемы может быть жива» — разные вещи, говорим один раз
        problem = data.get("state_problem")
        if problem and not self._state_note_shown:
            self._state_note_shown = True
            self._set_bottom_status(
                i18n.t("Файл состояния питания нечитаем ({v0}) — остаток профиля мог "
                       "остаться в системе.", v0=problem),
                ok=False,
            )

        # профиль от прошлой сессии, а компаньона (он сторожит откат) нет:
        # откатываем сами — иначе чужой клон схемы живёт до следующего
        # применения, а фут честно пишет «служба не запущена»
        if profile and not self._leftover_checked:
            self._leftover_checked = True
            if not self.client.alive():
                self._rollback_leftover()
                return

        # свои копии схем ищем один раз за сессию: файл состояния мог пропасть
        # или быть повреждённым, а копия при этом живёт в системе и в списке схем
        # Windows от пользовательской не отличается
        if not self._clones_checked:
            self._clones_checked = True
            self._scan_clones()

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

    # -- откат профиля прошлой сессии ---------------------------------------

    def _rollback_leftover(self) -> None:
        """Откатить профиль, оставшийся от прошлой сессии (компаньона нет).

        Профиль пишется в клон активной схемы, а схема переживает перезапуск
        приложения и перезагрузку системы: если UI упал вместе с компаньоном или
        машина выключилась с применённым профилем, откатывать некому. powercfg
        прав администратора не требует, поэтому откатываем сами — в фоне — и
        говорим об этом честно (журнал + строка состояния).
        """
        if self._leftover_busy or self._apply_busy:
            return
        self._leftover_busy = True
        self.status_name.setText(i18n.t("откатываю профиль прошлой сессии…"))
        gd_bg.submit(self._do_rollback, self._rollback_done, parent=self)

    @staticmethod
    def _do_rollback() -> dict:
        import gd_power

        return gd_power.restore_report()

    def _rollback_done(self, rep) -> None:
        self._leftover_busy = False
        if not isinstance(rep, dict):          # совместимость с простым bool
            rep = {"ok": bool(rep), "applied": bool(rep), "note": "", "clones": []}
        note = rep.get("note") or ""
        if not rep.get("ok"):
            self._set_bottom_status(
                i18n.t("Профиль прошлой сессии откатить не удалось: {v0}",
                       v0=note or i18n.t("причина неизвестна")),
                ok=False,
            )
        elif rep.get("applied"):
            self._set_bottom_status(
                i18n.t("Профиль прошлой сессии откатил — компаньона не было."), ok=True
            )
        else:
            self._set_bottom_status(i18n.t("Следов профиля не осталось."), ok=True)
        try:
            import gd_journal

            gd_journal.record(
                "cpu_profile",
                i18n.t("откат профиля прошлой сессии"),
                i18n.t("профиль применён"),
                i18n.t("исходная схема"),
                why=i18n.t("профиль остался от прошлой сессии, а компаньон не работал")
                + (f" — {note}" if note else ""),
                source=i18n.t("автооткат"),
                reversible=not rep.get("ok"),
            )
        except Exception:  # noqa: BLE001
            pass
        if rep.get("clones"):
            self._show_clones(rep["clones"])
        self._refresh_status()

    # -- свои копии схем питания в системе ------------------------------------

    def _scan_clones(self) -> None:
        """Найти свои копии схем, оставшиеся от прошлых сессий.

        Поиск по метке в имени (``gd_power.list_clones``) — единственный способ
        увидеть остаток, когда файл состояния потерян: без метки копия в списке
        схем Windows неотличима от пользовательской.
        """
        gd_bg.submit(self._do_scan_clones, self._clones_done, parent=self)

    @staticmethod
    def _do_scan_clones() -> list:
        import gd_power

        return gd_power.list_clones()

    def _clones_done(self, rows) -> None:
        self._clones = list(rows or [])
        if not self._clones:
            self.btn_clean_clones.setVisible(False)
            return
        self._show_clones(self._clones)

    def _show_clones(self, rows) -> None:
        """Показать, что в системе остались наши копии схем питания."""
        self._clones = list(rows)
        names = ", ".join(r["name"] for r in rows if r.get("name"))
        self.btn_clean_clones.setVisible(True)
        self.btn_clean_clones.setToolTip(
            i18n.t("Копии в системе: {v0}", v0=names or i18n.t("без имени")))
        if self._clones_notified:
            return
        self._clones_notified = True
        self._set_bottom_status(
            i18n.t("В системе остались наши копии схем питания: {v0}. Это след прошлых "
                   "сессий — их можно убрать.", v0=len(rows)),
            ok=False,
        )
        try:
            import gd_journal

            gd_journal.record("cpu_profile", i18n.t("копии схем питания"),
                              None, None,
                              why=i18n.t("остатки прошлых сессий, файла состояния нет") + f": {names}",
                              source=i18n.t("вкладка CPU"), reversible=False)
        except Exception:  # noqa: BLE001
            pass

    def _clean_clones(self) -> None:
        """Убрать свои копии схем (явное действие пользователя)."""
        if self._clones_busy or self._apply_busy:
            return
        self._clones_busy = True
        self.btn_clean_clones.setEnabled(False)
        self._set_bottom_status(i18n.t("Убираю свои копии схем питания…"), ok=True)
        gd_bg.submit(self._do_clean_clones, self._clones_cleaned, parent=self)

    @staticmethod
    def _do_clean_clones() -> dict:
        import gd_power

        return gd_power.cleanup_clones()

    def _clones_cleaned(self, rep) -> None:
        self._clones_busy = False
        self.btn_clean_clones.setEnabled(True)
        rep = rep if isinstance(rep, dict) else {"removed": [], "failed": {}}
        removed = list(rep.get("removed") or [])
        failed = dict(rep.get("failed") or {})
        if failed:
            reasons = "; ".join(f"{guid}: {note}" for guid, note in failed.items())
            self._set_bottom_status(
                i18n.t("Убрано копий: {v0}. Не убрано: {v1}", v0=len(removed), v1=reasons),
                ok=False,
            )
        else:
            self._set_bottom_status(
                i18n.t("Убрано своих копий схем: {v0}.", v0=len(removed)), ok=True
            )
        # остались неубранные копии — кнопка нужна для повторной попытки
        self.btn_clean_clones.setVisible(bool(failed))
        try:
            import gd_journal

            gd_journal.record("cpu_profile", i18n.t("уборка копий схем питания"),
                              i18n.t("копий: {v0}", v0=len(removed) + len(failed)),
                              i18n.t("убрано: {v0}", v0=len(removed)),
                              why=i18n.t("явная уборка остатков") if not failed
                              else "; ".join(failed.values()),
                              source=i18n.t("вкладка CPU"), reversible=False)
        except Exception:  # noqa: BLE001
            pass
        if removed:
            import gd_toast

            gd_toast.show_toast(i18n.t("toast.clones_cleaned", count=len(removed)), "ok")
        self._refresh_status()

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
            self.client.shutdown_async(restore=True)
        super().closeEvent(event)


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyleSheet(th.STYLESHEET)
    win = PowerWindow()
    win.show()
    sys.exit(app.exec_())
