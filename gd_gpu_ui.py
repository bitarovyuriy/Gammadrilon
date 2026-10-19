# -*- coding: utf-8 -*-
"""Gammadrilon v3 — вкладка «Питание GPU» (NVIDIA).

Чтение состояния — напрямую через nvidia-smi (без прав администратора).
Изменение лимита мощности — через elevated-хелпер (`GpuClient`).

Таймера «подтвердите изменение за 12 с» здесь больше нет (живой разбор
12.10.2026): он откатывал лимит тому, кто применил его не из вкладки —
горячей клавише, трею, палитре, правилам и локальному API, — а подтверждать
там некому, поэтому переключатель производительности работал «в никуда».
Теперь применённый лимит держится до кнопки «Вернуть как было», до выхода из
приложения (если снята галочка «не возвращать лимит при выходе») или до
следующего применения. План старта (`settings.gpu_auto_pl`) пишется сразу по
факту применения — «неподтверждённых» значений больше не бывает.

План — «какой лимит должен быть на карте» — сверяется с фактом не только на
старте, но и всю сессию (`check_drift`, зовёт main.py на тике статусбара): сон,
TDR и сброс драйвера возвращают номинал молча, а приложение об этом молчать не
должно. Галочка «применять лимит при запуске» отвечает только за старт —
драйвер внутри сессии приложение трогает лишь по явному действию.
"""
from __future__ import annotations

import os
import sys
import time

# --- PyQt5 и не-ASCII путь (как в main.py) ---------------------------------
try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
except Exception:  # noqa: BLE001
    pass

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_bg  # noqa: E402
import gd_gpu  # noqa: E402
import gd_agent  # noqa: E402
import gd_state  # noqa: E402
import gd_theme as th  # noqa: E402
import gd_i18n as i18n  # noqa: E402
from gd_power_ui import make_card  # noqa: E402

PROFILE_ORDER = ("quiet", "default", "game", "max")


def _presets_path() -> str:
    """presets.json рядом с приложением.

    Нужен только standalone-окну: вкладка внутри main.py получает config и
    save_fn от приложения и файл не трогает.
    """
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else HERE
    return os.path.join(base, "presets.json")


def _read_setting_file(key: str, default=None):
    """Настройка из presets.json. Нет файла или ключа — честный default."""
    try:
        import json

        with open(_presets_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return (data.get("settings") or {}).get(key, default)
    except Exception:  # noqa: BLE001 — файла нет или он битый
        return default


def _write_setting_file(key: str, value) -> bool:
    """Записать настройку в presets.json (нужно standalone-окну) — атомарно.

    Тем же способом, что главное окно: обрыв записи не оставит обрезанный
    presets.json. Битый или занятый файл не затираем (пусть решает главное
    окно со своим восстановлением) — честный False. Файла ещё нет — создаём
    минимальный: главное окно дополнит его при следующем старте.
    """
    import json

    path = _presets_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return False
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        return False
    if not isinstance(data.get("settings"), (dict, type(None))):
        return False
    data.setdefault("settings", {})[key] = value
    return gd_state.write_json_atomic(path, data, indent=4)


def _read_keep_flag_file() -> bool:
    """Настройка «не возвращать лимит при выходе» из файла. Нет файла — включена."""
    return bool(_read_setting_file("gpu_keep_on_exit", True))


def _write_keep_flag_file(on: bool) -> bool:
    return _write_setting_file("gpu_keep_on_exit", bool(on))


class GpuWidget(QWidget):
    def __init__(self, standalone: bool = False, config: dict | None = None,
                 save_fn=None):
        super().__init__()
        self.setObjectName("PanelRoot")
        self._standalone = standalone
        if standalone:
            self.setWindowTitle(i18n.t("Gammadrilon — Питание GPU"))
            self.resize(470, 680)

        self.client = gd_gpu.GpuClient()
        self._state = None
        self._pl_dirty = False        # на слайдере — цель пользователя, ещё не применённая
        self._active_profile = None   # имя применённого профиля (для трея/палитры)
        self._apply_worker = None     # фоновое применение лимита (UAC + nvidia-smi)
        self._restore_worker = None   # фоновый откат
        self._pre_apply_pl = None     # лимит до применения — для записи «было → стало»
        self._autostart_checked = False   # проверка автолимита при запуске — один раз
        # Сверка факта с планом внутри сессии (сон, TDR, сброс драйвера):
        self._drift_ready = False     # молчит, пока стартовая проверка не отработала
        self._drift_reported = None   # план, о слетевшем значении которого уже сказали
        self._drift_mute = 0.0        # тишина после своего применения: кэш метрик стареет

        # Настройка «не возвращать лимит при выходе» (settings.gpu_keep_on_exit):
        # config и save_fn передаёт main.py, standalone-окно пишет файл само.
        self._config = config
        self._save_fn = save_fn
        self._keep_on_exit = self._read_keep_on_exit()

        self._build()

        self._sensor_timer = QTimer(self)
        self._sensor_timer.timeout.connect(self._tick)

        if standalone:
            self.activate()

    # -- активация (ленивая вкладка) ---------------------------------------
    def activate(self):
        self._tick()
        self._sensor_timer.start(1000)

    def deactivate(self):
        self._sensor_timer.stop()
        # Приостановка только чтения: применённый лимит живёт на карте и никуда
        # не пропадает — откатывать его при уходе с вкладки больше нечего.

    # -- настройка «не возвращать лимит при выходе» -------------------------
    def _read_keep_on_exit(self) -> bool:
        """Настройка settings.gpu_keep_on_exit. По умолчанию — включена."""
        if self._config is not None:
            try:
                settings = self._config.get("settings") or {}
                return bool(settings.get("gpu_keep_on_exit", True))
            except Exception:  # noqa: BLE001
                return True
        return _read_keep_flag_file()

    def keep_on_exit(self) -> bool:
        """Оставлять ли лимит после выхода приложения (спрашивает main.py)."""
        return bool(self._keep_on_exit)

    def _on_keep_on_exit(self, state):
        """Галочка переключена: пишем настройку и сообщаем её хелперу.

        Хелперу — только в уже живую сессию (`call_existing`): поднимать UAC
        ради галочки нельзя, поэтому при её переключении до первого применения
        флаг уйдёт вместе с командой apply.
        """
        self._keep_on_exit = state == Qt.Checked
        saved = True
        if self._config is not None:
            try:
                self._config.setdefault("settings", {})["gpu_keep_on_exit"] = self._keep_on_exit
                if self._save_fn is not None:
                    self._save_fn(self._config)
            except Exception:  # noqa: BLE001
                pass
        else:
            saved = _write_keep_flag_file(self._keep_on_exit)

        on = self._keep_on_exit
        gd_bg.submit(
            lambda: self.client.call_existing({"v": 1, "cmd": "keep", "on": on}, timeout=5.0),
            lambda _resp: None,
            parent=self,
        )
        self._set_status(
            (i18n.t("Лимит останется после выхода приложения.") if on
             else i18n.t("Лимит вернётся к исходному при выходе приложения."))
            if saved else i18n.t("Настройка не сохранена: файл настроек недоступен для записи."),
            ok=saved,
        )

    # -- настройка «применять лимит при запуске» ----------------------------
    def _apply_on_start(self) -> bool:
        """Настройка settings.gpu_apply_on_start. По умолчанию — выключена."""
        if self._config is not None:
            try:
                settings = self._config.get("settings") or {}
                return bool(settings.get("gpu_apply_on_start", False))
            except Exception:  # noqa: BLE001
                return False
        return bool(_read_setting_file("gpu_apply_on_start", False))

    def _on_autostart_changed(self, state):
        """Галочка переключена: пишем настройку (UAC и хелпер здесь не нужны).

        Возврат лимита делает `ensure_autostart_pl` при следующем запуске —
        поднимать UAC ради галочки нельзя (как и у «не возвращать при выходе»).
        """
        on = state == Qt.Checked
        saved = True
        if self._config is not None:
            try:
                self._config.setdefault("settings", {})["gpu_apply_on_start"] = on
                if self._save_fn is not None:
                    self._save_fn(self._config)
            except Exception:  # noqa: BLE001
                pass
        else:
            saved = _write_setting_file("gpu_apply_on_start", on)
        self._set_status(
            (i18n.t("Лимит вернётся при запуске приложения.") if on
             else i18n.t("Лимит не будет применяться при запуске."))
            if saved else i18n.t("Настройка не сохранена: файл настроек недоступен для записи."),
            ok=saved,
        )

    # -- построение --------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(*th.PAGE_MARGINS)
        root.setSpacing(th.PAGE_SPACING)

        head = QHBoxLayout()
        title = QLabel(i18n.t("Питание GPU"))
        title.setObjectName("Title")
        title.setVisible(self._standalone)
        head.addWidget(title)
        head.addStretch(1)
        self.lbl_gpu = QLabel("—")
        self.lbl_gpu.setObjectName("Hint")
        head.addWidget(self.lbl_gpu)
        root.addLayout(head)

        # Доступность GPU и конфликт с вендорским софтом определяются в фоне:
        # nvidia-smi (timeout 6 с) и psutil.process_iter в GUI-потоке давали паузу.
        import gd_metrics

        self._pending_gpu_probe = gd_metrics.gpu() is None and not gd_metrics.gpu_unavailable()
        self.conflict = None
        self._gpu_warn = QLabel("")
        self._gpu_warn.setObjectName("Hint")
        self._gpu_warn.setWordWrap(True)
        self._gpu_warn.setVisible(False)
        root.addWidget(self._gpu_warn)
        self._conflict_note = QLabel("")
        self._conflict_note.setObjectName("Hint")
        self._conflict_note.setWordWrap(True)
        self._conflict_note.setVisible(False)
        root.addWidget(self._conflict_note)
        self._conflict_worker = gd_bg.submit(self._detect_conflict, self._conflict_done, parent=self)

        # --- профиль ---
        card, lay = make_card(
            i18n.t("ПРОФИЛЬ GPU"),
            i18n.t("Профиль — ярлык над слайдером лимита, а не отдельная настройка: «Тихий» — 150 Вт, «Дефолт» — номинал, «Игровой» — 93 % максимума, «Макс» — максимум драйвера."),
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self.profile_group = QButtonGroup(self)
        self.profile_btns = {}
        for pid in PROFILE_ORDER:
            b = QPushButton(gd_gpu.GPU_PROFILES[pid]["label"])
            b.setObjectName("Segment")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, p=pid: self._ui_profile(p))
            self.profile_group.addButton(b)
            self.profile_btns[pid] = b
            row.addWidget(b)
        lay.addLayout(row)
        self.profile_desc = QLabel(i18n.t("Профиль не применён."))
        self.profile_desc.setObjectName("Hint")
        self.profile_desc.setWordWrap(True)
        lay.addWidget(self.profile_desc)
        root.addWidget(card)

        # --- сейчас ---
        card2, lay2 = make_card(
            i18n.t("СЕЙЧАС"),
            i18n.t("Температура, частота, лимит мощности и загрузка — прямо из драйвера (nvidia-smi), без прав администратора."),
        )
        metrics = QHBoxLayout()
        metrics.setSpacing(th.SPACE_L)
        self.lbl_temp = QLabel("—")
        self.lbl_clk = QLabel("—")
        self.lbl_pl = QLabel("—")
        self.lbl_util = QLabel("—")
        for lbl, cap in ((self.lbl_temp, i18n.t("температура")), (self.lbl_clk, i18n.t("частота")),
                         (self.lbl_pl, i18n.t("лимит")), (self.lbl_util, i18n.t("загрузка"))):
            col = QVBoxLayout()
            col.setSpacing(th.SPACE_XS)
            lbl.setObjectName("Value")
            c = QLabel(cap)
            c.setObjectName("Hint")
            col.addWidget(lbl)
            col.addWidget(c)
            metrics.addLayout(col)
        metrics.addStretch(1)
        lay2.addLayout(metrics)
        self.lbl_mem = QLabel("—")
        self.lbl_mem.setObjectName("Hint")
        lay2.addWidget(self.lbl_mem)
        root.addWidget(card2)

        # --- лимит мощности ---
        card3, lay3 = make_card(
            i18n.t("ЛИМИТ МОЩНОСТИ"),
            i18n.t("Лимит ставится с правами администратора: при первом применении Windows спросит UAC. Держится до кнопки «Вернуть как было» или до закрытия приложения (см. галочку ниже)."),
        )
        self.lbl_range = QLabel("—")
        self.lbl_range.setObjectName("Hint")
        self.lbl_range.setWordWrap(True)
        lay3.addWidget(self.lbl_range)

        srow = QHBoxLayout()
        srow.setSpacing(12)
        self.sl_pl = QSlider(Qt.Horizontal)
        self.sl_pl.setRange(100, 366)
        self.lbl_pl_val = QLabel("—")
        self.lbl_pl_val.setObjectName("Value")
        srow.addWidget(self.sl_pl, 1)
        srow.addWidget(self.lbl_pl_val)
        lay3.addLayout(srow)
        self.sl_pl.valueChanged.connect(self._on_slider)

        brow = QHBoxLayout()
        brow.setSpacing(8)
        self.btn_apply = QPushButton(i18n.t("Применить"))
        self.btn_apply.setObjectName("PrimaryButton")
        self.btn_apply.clicked.connect(self._on_apply)
        brow.addWidget(self.btn_apply)
        lay3.addLayout(brow)

        self.chk_keep = QCheckBox(i18n.t("Не возвращать лимит при выходе приложения"))
        self.chk_keep.setChecked(self._keep_on_exit)
        self.chk_keep.stateChanged.connect(self._on_keep_on_exit)
        lay3.addWidget(self.chk_keep)
        keep_hint = QLabel(i18n.t("Стоит — лимит остаётся на карте после закрытия (до перезагрузки или кнопки «Вернуть как было»). Снята — при закрытии возвращается к исходному."))
        keep_hint.setObjectName("Hint")
        keep_hint.setWordWrap(True)
        lay3.addWidget(keep_hint)

        self.chk_autostart = QCheckBox(i18n.t("Применять лимит при запуске"))
        self.chk_autostart.setChecked(self._apply_on_start())
        self.chk_autostart.stateChanged.connect(self._on_autostart_changed)
        lay3.addWidget(self.chk_autostart)
        autostart_hint = QLabel(i18n.t("Стоит — при запуске Gammadrilon вернёт лимит к последнему значению (спросит права администратора). Снята — приложение только скажет, что лимит слетел."))
        autostart_hint.setObjectName("Hint")
        autostart_hint.setWordWrap(True)
        lay3.addWidget(autostart_hint)

        root.addWidget(card3)

        self.btn_restore = QPushButton(i18n.t("Вернуть как было"))
        self.btn_restore.setObjectName("Segment")
        self.btn_restore.clicked.connect(self._ui_restore)
        root.addWidget(self.btn_restore)

        root.addStretch(1)
        self.bottom_status = QLabel("")
        self.bottom_status.setObjectName("Hint")
        self.bottom_status.setWordWrap(True)
        root.addWidget(self.bottom_status)

    def _detect_conflict(self) -> str | None:
        """MSI Afterburner / RTSS — известные «перебиватели» (ТЗ 4.5)."""
        try:
            import psutil

            names = {p.info.get("name", "").lower() for p in psutil.process_iter(["name"])}
        except Exception:  # noqa: BLE001
            return None
        if any(n.startswith("msiafterburner") for n in names):
            return "MSI Afterburner"
        return None

    def _conflict_done(self, name) -> None:
        """Показать предупреждение о вендорском софте (проверка шла в фоне)."""
        if not name:
            return
        self.conflict = name
        try:
            self._conflict_note.setText(i18n.t(
                "Обнаружен {v0}: он тоже управляет лимитом мощности и частотами. Ярлыками пользоваться не стоит — меняйте лимит вручную и следите, чтобы значения не «перебивались».",
                v0=name))
            self._conflict_note.setVisible(True)
        except RuntimeError:      # виджет уже удалён
            pass

    def _set_controls_enabled(self, on: bool) -> None:
        """Включить/заблокировать управление лимитом (например, GPU недоступен)."""
        for btn in getattr(self, "profile_btns", {}).values():
            btn.setEnabled(on)
        for attr in ("sl_pl", "btn_apply", "btn_restore", "chk_keep"):
            w = getattr(self, attr, None)
            if w is not None:
                w.setEnabled(on)

    # -- обновление --------------------------------------------------------
    def _tick(self):
        import gd_metrics

        st = gd_metrics.gpu() or self._state
        if st is None:
            self.lbl_gpu.setText(i18n.t("GPU не отвечает"))
            if not self._pending_gpu_probe and gd_metrics.gpu_unavailable():
                # nvidia-smi молчит несколько попыток подряд — карты нет
                self._gpu_warn.setText(i18n.t(
                    "NVIDIA GPU или nvidia-smi не найдены. Управление недоступно: для этой карты/системы публичного способа менять лимит мощности нет."))
                self._gpu_warn.setVisible(True)
                self._set_controls_enabled(False)
            return
        if self._pending_gpu_probe:
            self._pending_gpu_probe = False      # первый ответ пришёл — вкладка живая
            self._gpu_warn.setVisible(False)
        self._state = st
        self.lbl_gpu.setText(st["name"])
        self.lbl_temp.setText(f"{st['temp']:.0f} °C" if st["temp"] is not None else "—")
        self.lbl_clk.setText(i18n.t("{v0:.0f} МГц", v0=st['clock']) if st["clock"] is not None else "—")
        self.lbl_pl.setText(i18n.t("{v0:.0f}/{v1:.0f} Вт", v0=st['pl'], v1=st['pl_max']) if st["pl"] is not None else "—")
        self.lbl_util.setText(f"{st['util']:.0f} %" if st["util"] is not None else "—")
        mem = ""
        if st["clock_mem"]:
            mem = i18n.t("память {v0:.0f} МГц", v0=st['clock_mem'])
        if st["mem_used"] and st["mem_total"]:
            mem += ("  ·  " if mem else "") + i18n.t("VRAM {v0:.1f}/{v1:.1f} ГБ", v0=st['mem_used'] / 1024, v1=st['mem_total'] / 1024)
        self.lbl_mem.setText(mem or "—")

        if st["pl_min"] is not None and st["pl_max"] is not None:
            self.sl_pl.blockSignals(True)   # смена диапазона — не действие пользователя
            self.sl_pl.setRange(int(st["pl_min"]), int(st["pl_max"]))
            self.sl_pl.blockSignals(False)
        # Слайдер переписываем, только когда он показывает состояние карты, а не
        # цель пользователя: иначе тик раз в секунду возвращал «ползунок на 300»
        # (живой разбор 12.10.2026) — сдвинутое значение не доживало до «Применить».
        if (not self._pl_dirty and not self.sl_pl.isSliderDown()
                and st["pl"] is not None):
            self.sl_pl.blockSignals(True)
            self.sl_pl.setValue(int(st["pl"]))
            self.sl_pl.blockSignals(False)
        self.lbl_pl_val.setText(i18n.t("{v0} Вт", v0=self.sl_pl.value()))
        self._update_range_label()

    def _update_range_label(self):
        st = self._state
        if not st:
            return
        cur = st["pl"]
        nom = st["pl_default"]
        lo, hi = st["pl_min"], st["pl_max"]
        txt = (i18n.t("Номинал {v0:.0f} Вт · диапазон {v1:.0f}–{v2:.0f} Вт · сейчас {v3:.0f} Вт", v0=nom, v1=lo, v2=hi, v3=cur))
        self.lbl_range.setText(txt)

    def _on_slider(self, value):
        """Сдвиг слайдера — цель пользователя, а не состояние карты.

        Живой разбор 12.10.2026 («ползунок сам возвращается на 300»): тик метрик
        раз в секунду переписывал слайдер значением драйвера, пока изменение не
        применено, — сдвинутое значение не доживало до кнопки «Применить», а в
        журнал уходило применение прежних 300 Вт (то есть «ничего не менялось»).
        Теперь цель держится до применения (`_pl_dirty`), а не до опроса.
        """
        self._pl_dirty = True
        self.lbl_pl_val.setText(i18n.t("{v0} Вт", v0=value))

    def _tick_later(self, delay_ms: int = 800) -> None:
        """Обновить показания после операции: пусть кэш метрик пере-снимет GPU."""
        import gd_metrics

        gd_metrics.refresh_now("gpu")
        QTimer.singleShot(delay_ms, self._tick)

    # -- действия ----------------------------------------------------------
    def _on_profile(self, name):
        import gd_metrics

        st = self._state or gd_metrics.gpu()
        if not st:
            gd_agent.finish(getattr(self, "_ext_done", None), False,
                            i18n.t("нет данных GPU (nvidia-smi недоступен)"))
            self._ext_done = None
            return
        target = gd_gpu.profile_target(st, name)
        pl = target.get("pl")
        if pl is None:
            gd_agent.finish(getattr(self, "_ext_done", None), False,
                            i18n.t("драйвер не сообщил лимит для этого профиля"))
            self._ext_done = None
            return
        self.sl_pl.setValue(int(pl))
        self.profile_desc.setText(
            i18n.t("Профиль «{v0}» → лимит {v1} Вт. Применяю…", v0=gd_gpu.GPU_PROFILES[name]['label'], v1=pl)
        )
        self._apply({"pl": pl}, profile_name=name)

    def _on_apply(self):
        self._source = "UI"
        self._apply({"pl": self.sl_pl.value()})

    def _ui_profile(self, name: str):
        """Клик по профилю в вкладке: источник — UI, а не трей/агент."""
        self._source = "UI"
        self._on_profile(name)

    def _apply(self, payload: dict, profile_name=None, autostart: bool = False):
        """Применить лимит. `autostart` — возврат плана при запуске: меняет только
        подписи и причину в журнале (нажатия пользователя не было), поведение то же."""
        # запуск хелпера (UAC) и запись nvidia-smi долгие — не морозим GUI
        if gd_bg.is_running(self._apply_worker):
            gd_agent.finish(getattr(self, "_ext_done", None), False,
                            i18n.t("занято: операция уже выполняется"))
            self._ext_done = None
            return
        self.btn_apply.setEnabled(False)
        self.profile_desc.setText(i18n.t("Применяю…"))

        keep = self._keep_on_exit      # настройка на момент применения
        self._apply_worker = gd_bg.submit(
            lambda: self.client.call({"v": 1, "cmd": "apply",
                                      "keep_on_exit": keep, **payload}),
            lambda resp: self._apply_done(resp, payload, profile_name, autostart=autostart),
            parent=self,
        )

    def _apply_done(self, resp, payload: dict, profile_name, autostart: bool = False):
        self.btn_apply.setEnabled(True)
        if not resp.get("ok"):
            self._set_status(i18n.t("Драйвер не принял изменение: {v0}", v0=resp.get('error')), ok=False)
            self.profile_desc.setText(i18n.t("Изменение не применено."))
            gd_agent.finish(getattr(self, "_ext_done", None), False, resp.get("error"))
            self._ext_done = None
            return
        self._pl_dirty = False       # цель пользователя теперь на карте
        self._active_profile = profile_name
        self._pre_apply_pl = (self._state or {}).get("pl")
        # Лимит применён и держится: подтверждать нечего, а план старта — это и
        # есть применённое значение (раньше он ждал «Оставить», и место на карте
        # занимало значение, которого через 12 с не становилось).
        self._remember_plan(payload["pl"])
        # общий кэш метрик ещё показывает прежнее значение (nvidia-smi читается по
        # TTL) — свою сверку факта с планом на это время глушим
        self._drift_mute = time.monotonic() + 12.0
        self._drift_reported = None
        if autostart:
            self.profile_desc.setText(i18n.t("Лимит {v0} Вт возвращён при запуске.", v0=payload['pl']))
            self._set_status(
                i18n.t("Лимит {v0} Вт возвращён при запуске (настройка «применять при запуске»).",
                       v0=payload['pl']),
                ok=True, color=th.ACCENT)
            why = i18n.t("запуск приложения")
        else:
            self.profile_desc.setText(i18n.t("Лимит {v0} Вт применён.", v0=payload['pl']))
            self._set_status(i18n.t("Применено: лимит {v0} Вт.", v0=payload['pl']),
                             ok=True, color=th.ACCENT)
            why = i18n.t("лимит мощности GPU")
        self._journal("gpu_pl", i18n.t("{v0} Вт", v0=payload['pl']),
                      before=self._pre_apply_pl, after=payload["pl"], why=why)
        gd_agent.finish(getattr(self, "_ext_done", None), True)
        self._ext_done = None
        self._tick_later()

    def _restore_async(self, on_done) -> None:
        """Откат лимита в фоне (кнопка «Вернуть как было», откат при выходе)."""
        if gd_bg.is_running(self._restore_worker):
            gd_agent.finish(getattr(self, "_ext_done", None), False,
                            i18n.t("занято: операция уже выполняется"))
            self._ext_done = None
            return

        self._restore_worker = gd_bg.submit(
            lambda: self.client.call({"v": 1, "cmd": "restore"}), on_done, parent=self
        )

    def _ui_restore(self):
        """Клик «Вернуть исходный лимит» в вкладке: источник — UI."""
        self._source = "UI"
        self._on_restore()

    def _on_restore(self):
        self._active_profile = None
        self.btn_restore.setEnabled(False)
        self._set_status(i18n.t("Возвращаю исходный лимит…"), ok=True)
        self._restore_async(self._restore_done)

    def _restore_done(self, resp):
        self.btn_restore.setEnabled(True)
        if resp.get("ok"):
            self._pl_dirty = False
            self._remember_plan(None)    # лимит возвращён к исходному — плана нет
            # плана больше нет: сверять нечего, а кэш метрик ещё показывает старый
            # лимит — прежний доклад о слетевшем плане снимаем
            self._drift_mute = time.monotonic() + 12.0
            self._drift_reported = None
            self._set_status(i18n.t("Исходный лимит возвращён."), ok=True)
            self.profile_desc.setText(i18n.t("Профиль не применён."))
            self._journal("restore", i18n.t("лимит GPU"), why=i18n.t("возврат исходного лимита"))
            self.profile_group.setExclusive(False)
            for b in self.profile_btns.values():
                b.setChecked(False)
            self.profile_group.setExclusive(True)
        else:
            self._set_status(i18n.t("Откат не удался: {v0}", v0=resp.get('error')), ok=False)
        gd_agent.finish(getattr(self, "_ext_done", None), resp.get("ok"), resp.get("error"))
        self._ext_done = None
        self._tick_later()

    def _set_status(self, text: str, ok: bool = True, color: str | None = None):
        self.bottom_status.setStyleSheet(f"color: {color or (th.MUTED if ok else th.WARN)};")
        self.bottom_status.setText(text)
        self._reveal_status()

    def _reveal_status(self) -> None:
        """Прокрутить страницу к строке статуса.

        Замер `_tmp_gpu_fold.py` (живой разбор 12.10.2026): в компактном окне
        910×700 строка статуса стоит на y=637 при высоте вьюпорта 640 — то есть
        «Применено: лимит 212 Вт.» уходило за нижнюю границу, и результат нажатия
        читался только по тосту. Страница прокручивается вниз ровно настолько,
        чтобы статус был виден целиком.
        """
        try:
            from PyQt5.QtWidgets import QScrollArea

            parent = self.parentWidget()
            while parent is not None and not isinstance(parent, QScrollArea):
                parent = parent.parentWidget()
            if parent is not None:
                parent.ensureWidgetVisible(self.bottom_status, 0, 8)
        except Exception:  # noqa: BLE001
            pass

    def _journal(self, kind: str, target: str, before=None, after=None, why: str = "",
                 toast: tuple | None = None):
        """Строка в журнал + тост.

        `toast=(текст, вид)` — когда тост должен отличаться от общей подписи
        «Питание GPU: …» (например, предупреждение о слетевшем лимите).
        """
        try:
            import gd_journal

            gd_journal.record(kind, target, before, after, why=why,
                              source=getattr(self, "_source", "UI"))
            import gd_toast

            text, tone = toast if toast else (
                i18n.t("toast.gpu", target=target),
                "info" if str(kind).startswith("restore") else "ok")
            gd_toast.show_toast(text, tone)
        except Exception:  # noqa: BLE001
            pass

    # -- внешнее управление (трей, палитра команд, локальный API) -----------
    def apply_profile(self, name: str, source: str = i18n.t("трей"), on_done=None):
        """Применить профиль GPU извне (или вернуть исходный лимит).

        `on_done(result)` — хук внешних вызовов (локальный API, ТЗ этап 8):
        один раз получает ``{"ok", "error"}`` по завершении, в том числе при
        отказе «занято» или отсутствии данных nvidia-smi.
        """
        if gd_bg.is_running(self._apply_worker) or gd_bg.is_running(self._restore_worker):
            gd_agent.finish(on_done, False, i18n.t("занято: операция уже выполняется"))
            return
        self._source = source
        self._ext_done = on_done
        if name == "restore":
            self._on_restore()
            return
        self._on_profile(name)

    def apply_limit(self, watts: int, source: str = i18n.t("трей"), on_done=None,
                    autostart: bool = False):
        """Лимит мощности в ваттах извне: как движение слайдера + «Применить».

        Диапазон берём у драйвера (pl_min/pl_max из nvidia-smi), а не из
        слайдера: вкладка могла ещё не открываться, и границы были бы чужими.
        `autostart=True` — возврат плана при запуске (меняются только подписи).
        """
        if gd_bg.is_running(self._apply_worker) or gd_bg.is_running(self._restore_worker):
            gd_agent.finish(on_done, False, i18n.t("занято: операция уже выполняется"))
            self._set_status(i18n.t("занято: операция уже выполняется"), ok=False)
            return
        import gd_metrics

        st = self._state or gd_metrics.gpu() or gd_gpu.read_state() or {}
        lo = st.get("pl_min")
        hi = st.get("pl_max")
        if lo and hi and not (float(lo) <= float(watts) <= float(hi)):
            msg = i18n.t(
                "лимит {v0} Вт вне диапазона драйвера {v1}..{v2} Вт",
                v0=watts, v1=int(lo), v2=int(hi))
            gd_agent.finish(on_done, False, msg)
            self._set_status(msg, ok=False)   # видно и в самой вкладке, не только вызывающему
            return
        self._source = source
        self._ext_done = on_done
        self.sl_pl.setValue(int(watts))
        self._apply({"pl": int(watts)}, autostart=autostart)

    def _remember_plan(self, pl) -> None:
        """Запомнить «какой лимит должен быть» (settings.gpu_auto_pl) — план старта.

        План — «какой лимит должен быть на карте». Пишется по факту применения
        (вкладка, трей, правила, горячая клавиша, автозапуск) и обнуляется, когда
        лимит возвращён к исходному: кнопкой «Вернуть как было» или откатом при
        выходе. `None` — плана нет.
        """
        if self._config is None:
            return
        try:
            self._config.setdefault("settings", {})["gpu_auto_pl"] = (
                None if pl is None else int(pl))
            if self._save_fn is not None:
                self._save_fn(self._config)
        except Exception:  # noqa: BLE001
            pass

    def _plan_value(self):
        """План («какой лимит должен быть») — settings.gpu_auto_pl. None — плана нет.

        План пишется по факту применения (`_remember_plan`) и обнуляется, когда
        лимит возвращён к исходному, — поэтому «план есть, а факт другой» значит
        ровно одно: применённый лимит слетел без нашего участия.
        """
        settings = {}
        if self._config is not None:
            try:
                settings = self._config.get("settings") or {}
            except Exception:  # noqa: BLE001
                settings = {}
        plan = settings.get("gpu_auto_pl")
        try:
            return None if plan is None else int(plan)
        except (TypeError, ValueError):
            return None

    def ensure_autostart_pl(self) -> None:
        """Старт приложения: держать лимит по настройке «применять при запуске».

        Один раз за сессию, в фоне (nvidia-smi). План — settings.gpu_auto_pl
        (последнее применённое значение):

          * факт совпал с планом — тишина;
          * план слетел (перезагрузка вернула номинал) и галочка включена —
            вернуть план на карту (единственный путь применения, который
            поднимает UAC без клика: так решил пользователь);
          * план слетел, галочка выключена — только честно сказать в журнал и
            статус, драйвер не трогаем.
        """
        if self._autostart_checked:
            return
        self._autostart_checked = True

        plan = self._plan_value()
        if plan is None:
            self._drift_ready = True   # сверять нечего — сторож внутри сессии пуст
            return
        apply_on_start = self._apply_on_start()

        def done(resp):
            try:
                self._autostart_report(resp, plan, apply_on_start)
            finally:
                # стартовый доклад отработал: с этого места факт с планом
                # сверяет check_drift, и второй доклад об одном и том же не придёт
                self._drift_ready = True

        gd_bg.submit(lambda: {"state": gd_gpu.read_state()}, done, parent=self)

    def _autostart_report(self, resp, plan: int, apply_on_start: bool) -> None:
        """Разобрать ответ стартовой проверки: вернуть план или сказать о слетевшем."""
        st = (resp or {}).get("state") or {}
        cur = st.get("pl")
        if cur is None:      # GPU не отвечает — молча: это не ошибка старта
            return
        if abs(float(cur) - float(plan)) < 1:
            return           # всё на месте
        if apply_on_start:
            self.apply_limit(plan, source=i18n.t("запуск приложения"),
                             autostart=True)
            return
        # план слетел, а возвращать его не просили — сказать честно
        # (слёт уже заявлен: тик статусбара об этом же промолчит)
        self._drift_reported = plan
        self._journal(
            "gpu_pl", i18n.t("лимит GPU"), before=cur, after=plan,
            why=i18n.t("лимит слетел после перезапуска"),
            toast=(i18n.t("Лимит GPU слетел: был {v0:.0f} Вт, сейчас {v1:.0f} Вт",
                          v0=plan, v1=cur), "warn"))
        self._set_status(
            i18n.t("Лимит GPU слетел: был {v0:.0f} Вт, сейчас {v1:.0f} Вт. "
                   "Включите «Применять лимит при запуске», чтобы возвращать его автоматически.",
                   v0=plan, v1=cur),
            ok=False)

    def check_drift(self) -> None:
        """Не вернула ли система свой лимит внутри сессии? Сказать один раз.

        Сторож нужен для того же, ради чего он есть у гаммы: сон, TDR и сброс
        драйвера возвращают номинал без нашего участия, а приложение раньше
        узнавало об этом, только когда пользователь сам открывал вкладку —
        применённый профиль при этом выглядел рабочим. Драйвер здесь не
        трогаем: возврат лимита — решение пользователя, а не тихий автоповтор.

        Проверка дешёвая: значение пла (и его факт) уже лежат в общем кэше
        метрик, который читает тик статусбара, — подпроцессов не запускаем.
        """
        import gd_metrics

        if not self._drift_ready:
            return                      # стартовая проверка ещё не сказала своего
        if time.monotonic() < self._drift_mute:
            return                      # только что применили: кэш метрик мог отстать
        if self._pl_dirty or self.sl_pl.isSliderDown():
            return                      # цель пользователя ещё не на карте
        if gd_bg.is_running(self._apply_worker) or gd_bg.is_running(self._restore_worker):
            return                      # своя операция в работе — она и расскажет
        plan = self._plan_value()
        if plan is None:
            return                      # плана нет: слетать нечему
        st = gd_metrics.gpu()
        cur = (st or {}).get("pl")
        if cur is None:
            return                      # GPU не отвечает: это не «слетел»
        if abs(float(cur) - float(plan)) < 1:
            self._drift_reported = None  # снова совпало — следующий слёт сообщим
            return
        if self._drift_reported == plan:
            return                      # об этом слёте уже сказали: не повторяемся
        self._drift_reported = plan
        self._active_profile = None      # профиль на карте больше не держится
        self._journal(
            "gpu_pl", i18n.t("лимит GPU"), before=plan, after=cur,
            why=i18n.t("лимит слетел в работе"),
            toast=(i18n.t("Лимит GPU слетел: был {v0:.0f} Вт, сейчас {v1:.0f} Вт",
                          v0=plan, v1=cur), "warn"))
        self._set_status(
            i18n.t("Лимит GPU слетел в работе: было {v0:.0f} Вт, стало {v1:.0f} Вт. "
                   "Система вернула своё значение — примените лимит снова.",
                   v0=plan, v1=cur),
            ok=False)

    def current_profile(self):
        return getattr(self, "_active_profile", None)

    def shutdown_for_exit(self) -> None:
        """Выход приложения: решить, откатывать ли лимит.

        Настройка «не возвращать лимит при выходе» (по умолчанию включена)
        оставляет лимит на карте. Снята — лимит возвращается к исходному перед
        закрытием, и «плана» на следующий запуск не остаётся.

        `force` больше не выставляется: «неподтверждённых» изменений не бывает,
        каждое применение сразу становится состоянием карты.
        """
        restore = not self._keep_on_exit
        if restore:
            # Лимит вернётся к исходному — «плана» на следующий запуск нет.
            self._remember_plan(None)
        try:
            self.client.shutdown_async(restore=restore, force=False)
        except Exception:  # noqa: BLE001
            pass

    # -- отдельное окно ----------------------------------------------------
    def closeEvent(self, event):
        if self._standalone:
            self.shutdown_for_exit()
        super().closeEvent(event)


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyleSheet(th.STYLESHEET)
    w = GpuWidget(standalone=True)
    w.show()
    sys.exit(app.exec_())
