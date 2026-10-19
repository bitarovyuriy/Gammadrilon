# -*- coding: utf-8 -*-
"""Вкладка «Утилиты» (ТЗ 6.1): очистка standby list и рабочих наборов.

Честные подписи: не «ускорить ПК», а «очистить standby list и рабочие наборы».
Таблица памяти Total / In Use / Modified / Standby / Free — до и после, с дельтой.
Действия требуют прав администратора → выполняются elevated-хелпером.
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

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_bg  # noqa: E402
import gd_journal  # noqa: E402
import gd_memory  # noqa: E402
import gd_state  # noqa: E402
import gd_theme as th  # noqa: E402
import gd_i18n as i18n  # noqa: E402
from gd_power_ui import make_card  # noqa: E402

ROWS = ("Total", "In Use", "Modified", "Standby", "Free")


def human(v):
    if v is None:
        return i18n.t("н/д")
    return i18n.t("{v0:.2f} ГБ", v0=v / 1024 ** 3)


class ProcessPicker(QDialog):
    """Выбор процессов для сжатия рабочих наборов."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("Сжать рабочие наборы"))
        self.setStyleSheet(th.STYLESHEET)
        self.resize(480, 520)

        root = QVBoxLayout(self)
        hint = QLabel(
            i18n.t("Отметьте процессы. Их рабочие наборы будут сжаты: Windows выгрузит страницы, к которым давно не обращались. Для чужих процессов нужны права администратора.")
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        self._proc_worker = None
        self._err = ""
        loading = QListWidgetItem(i18n.t("Читаю список процессов…"))
        loading.setFlags(Qt.NoItemFlags)
        self.list.addItem(loading)
        # psutil.process_iter — дорогой вызов: список наполняем из фонового потока
        self._proc_worker = gd_bg.submit(
            lambda: gd_memory.list_processes_report(40), self._fill_list, parent=self
        )
        root.addWidget(self.list, 1)

        # отказ чтения списка — не то же самое, что «процессов нет»: говорим текстом
        self.lbl_err = QLabel("")
        self.lbl_err.setObjectName("Hint")
        self.lbl_err.setWordWrap(True)
        self.lbl_err.setVisible(False)
        root.addWidget(self.lbl_err)

        row = QHBoxLayout()
        btn_all = QPushButton(i18n.t("Отметить все"))
        btn_all.setObjectName("Segment")
        btn_all.clicked.connect(lambda: self._set_all(Qt.Checked))
        btn_none = QPushButton(i18n.t("Снять все"))
        btn_none.setObjectName("Segment")
        btn_none.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        self._pick_btns = (btn_all, btn_none)
        row.addWidget(btn_all)
        row.addWidget(btn_none)
        row.addStretch(1)
        root.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(i18n.t("Сжать выбранные"))
        buttons.button(QDialogButtonBox.Cancel).setText(i18n.t("Отмена"))
        self._ok_btn = buttons.button(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _fill_list(self, rep):
        """Заполнить список после фонового чтения (список + причина отказа)."""
        rows, err = rep if isinstance(rep, tuple) else (rep or [], "")
        self.list.clear()
        self._err = err
        for btn in self._pick_btns:
            btn.setEnabled(not err)
        self._ok_btn.setEnabled(not err)
        self.lbl_err.setVisible(bool(err))
        if err:
            self.lbl_err.setText(
                i18n.t("Список процессов не прочитан: {v0}. Сжать рабочие наборы сейчас "
                       "нельзя.", v0=err))
            it = QListWidgetItem(i18n.t("— список недоступен —"))
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
            return
        for p in rows:
            it = QListWidgetItem(i18n.t("{v0}  ·  pid {v1}  ·  {v2:.0f} МБ", v0=p['name'], v1=p['pid'], v2=p['rss'] / 1024 ** 2))
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Unchecked)
            it.setData(Qt.UserRole, p["pid"])
            self.list.addItem(it)

    def _set_all(self, state):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def selected_pids(self) -> list[int]:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        return out


class UtilsWidget(QWidget):
    def __init__(self, standalone: bool = False, host=None):
        super().__init__()
        self.setObjectName("PanelRoot")
        self._standalone = standalone
        self.host = host
        self.setStyleSheet(th.STYLESHEET)

        self.client = None
        self._sensors = None
        self._before = None
        self._worker = None       # фоновая очистка standby
        self._ws_worker = None    # фоновое сжатие рабочих наборов

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel(i18n.t("Утилиты"))
        title.setObjectName("Title")
        title.setVisible(self._standalone)
        head.addWidget(title)
        head.addStretch(1)
        self.lbl_admin = QLabel("")
        self.lbl_admin.setObjectName("Hint")
        head.addWidget(self.lbl_admin)
        root.addLayout(head)

        # --- память ---
        card, lay = make_card(i18n.t("ОЧИСТКА ОЗУ — STANDBY LIST И РАБОЧИЕ НАБОРЫ"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        self.value_labels = {}
        self.delta_labels = {}
        for i, key in enumerate(ROWS):
            name = QLabel(key)
            name.setObjectName("Hint")
            val = QLabel("—")
            val.setObjectName("Value")
            delta = QLabel("")
            delta.setObjectName("Hint")
            grid.addWidget(name, i, 0)
            grid.addWidget(val, i, 1)
            grid.addWidget(delta, i, 2)
            self.value_labels[key] = val
            self.delta_labels[key] = delta
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)

        brow = QHBoxLayout()
        self.btn_purge = QPushButton(i18n.t("Очистить standby list"))
        self.btn_purge.setObjectName("PrimaryButton")
        self.btn_purge.clicked.connect(self._purge)
        self.btn_ws = QPushButton(i18n.t("Сжать рабочие наборы…"))
        self.btn_ws.setObjectName("Segment")
        self.btn_ws.clicked.connect(self._pick_and_empty)
        brow.addWidget(self.btn_purge)
        brow.addWidget(self.btn_ws)
        brow.addStretch(1)
        lay.addLayout(brow)

        # подпись обязательна и видима (ТЗ 6.1) — не только в тултипе
        sign = QLabel(gd_memory.SIGNATURE)
        sign.setObjectName("Hint")
        sign.setWordWrap(True)
        lay.addWidget(sign)
        root.addWidget(card)

        # --- автоочистка ---
        card2, lay2 = make_card(i18n.t("АВТООЧИСТКА"))
        arow = QHBoxLayout()
        self.chk_auto = QCheckBox(i18n.t("Включить автоочистку"))
        self.chk_auto.stateChanged.connect(self._save_auto)
        arow.addWidget(self.chk_auto)
        arow.addStretch(1)
        lay2.addLayout(arow)

        srow = QHBoxLayout()
        srow.setSpacing(8)
        srow.addWidget(QLabel(i18n.t("Standby больше")))
        self.sp_standby = QSpinBox()
        self.sp_standby.setRange(64, 32768)
        self.sp_standby.setSuffix(i18n.t(" МБ"))
        self.sp_standby.setValue(gd_memory.AUTO_DEFAULTS["standby_mb"])
        self.sp_standby.valueChanged.connect(self._save_auto)
        srow.addWidget(self.sp_standby)
        srow.addWidget(QLabel(i18n.t("и свободно меньше")))
        self.sp_free = QSpinBox()
        self.sp_free.setRange(64, 32768)
        self.sp_free.setSuffix(i18n.t(" МБ"))
        self.sp_free.setValue(gd_memory.AUTO_DEFAULTS["free_mb"])
        self.sp_free.valueChanged.connect(self._save_auto)
        srow.addWidget(self.sp_free)
        srow.addStretch(1)
        lay2.addLayout(srow)
        auto_note = QLabel(
            i18n.t("Как в ISLC: если standby больше первого порога И свободной памяти меньше второго — очищать автоматически.")
        )
        auto_note.setObjectName("Hint")
        auto_note.setWordWrap(True)
        lay2.addWidget(auto_note)
        self.lbl_auto = QLabel(i18n.t("Автоочистка выключена."))
        self.lbl_auto.setObjectName("Hint")
        lay2.addWidget(self.lbl_auto)
        root.addWidget(card2)

        # --- запуск вместе с Windows ---
        # Факт (файл в «Автозагрузке»), а не флаг конфига: ярлык могли удалить
        # вручную — тогда галочка честно снята.
        self._startup_host = host if hasattr(host, "set_start_with_windows") else None

        card_run, lay_run = make_card(i18n.t("ЗАПУСК ВМЕСТЕ С WINDOWS"))
        self.chk_startup = QCheckBox(i18n.t("Запускать вместе с Windows"))
        self.chk_startup.setChecked(self._startup_enabled())
        self.chk_startup.setEnabled(self._startup_host is not None)
        self.chk_startup.stateChanged.connect(self._startup_changed)
        lay_run.addWidget(self.chk_startup)

        run_note = QLabel(i18n.t(
            "Файл автозапуска ложится в папку «Автозагрузка» текущего пользователя — "
            "без окна и без запроса прав. Приложение стартует в трей, настройки экрана "
            "применяются как обычно."))
        run_note.setObjectName("Hint")
        run_note.setWordWrap(True)
        lay_run.addWidget(run_note)

        self.lbl_startup = QLabel("")
        self.lbl_startup.setObjectName("Hint")
        self.lbl_startup.setWordWrap(True)
        if self._startup_host is None:
            self.lbl_startup.setText(i18n.t("Автозапуск настраивается из главного окна."))
        lay_run.addWidget(self.lbl_startup)
        root.addWidget(card_run)

        # --- обновления (по желанию) ---
        # Галочка выключена по умолчанию: приложение обещает «без облака», и
        # включать сеть за человека оно не имеет права.
        card_upd, lay_upd = make_card(i18n.t("ОБНОВЛЕНИЯ"))
        self._updates_host = host if (host is not None and hasattr(host, "check_updates")) else None

        self.chk_updates = QCheckBox(i18n.t("Проверять при запуске"))
        self.chk_updates.setEnabled(self._updates_host is not None)
        self.chk_updates.stateChanged.connect(self._updates_changed)
        self.btn_updates = QPushButton(i18n.t("Проверить сейчас"))
        self.btn_updates.setObjectName("Segment")
        self.btn_updates.setEnabled(self._updates_host is not None)
        self.btn_updates.clicked.connect(self._check_updates)

        urow = QHBoxLayout()
        urow.addWidget(self.chk_updates)
        urow.addWidget(self.btn_updates)
        urow.addStretch(1)
        lay_upd.addLayout(urow)

        upd_note = QLabel(i18n.t(
            "Запрос уходит на api.github.com и не несёт ничего, кроме самого запроса: "
            "ни версии приложения, ни идентификаторов, ни счётчиков. GitHub при этом "
            "видит IP — поэтому галочка выключена, пока её не поставить."))
        upd_note.setObjectName("Hint")
        upd_note.setWordWrap(True)
        lay_upd.addWidget(upd_note)

        self.lbl_updates = QLabel(i18n.t("Обновления не проверялись."))
        self.lbl_updates.setObjectName("Hint")
        self.lbl_updates.setWordWrap(True)
        self.lbl_updates.setOpenExternalLinks(True)
        if self._updates_host is None:
            self.lbl_updates.setText(i18n.t("Проверка обновлений доступна из главного окна."))
        lay_upd.addWidget(self.lbl_updates)
        root.addWidget(card_upd)
        self._sync_updates()

        # --- прочие утилиты (ТЗ 6.3) ---
        try:
            from gd_extra_ui import ExtraPanel

            self.extra = ExtraPanel(host=host)
            root.addWidget(self.extra)
        except Exception as exc:  # noqa: BLE001
            self.extra = None
            err = QLabel(i18n.t("Панель прочих утилит не загрузилась: {v0}", v0=exc))
            err.setObjectName("Hint")
            err.setWordWrap(True)
            root.addWidget(err)

        root.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self._load_auto()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if standalone:
            self.activate()

    # -- жизненный цикл вкладки -------------------------------------------
    def activate(self):
        self._refresh()
        self._sync_startup()
        self._timer.start(2000)
        extra = getattr(self, "extra", None)
        if extra is not None:
            extra.refresh()

    def deactivate(self):
        self._timer.stop()

    # -- данные ------------------------------------------------------------
    def _ensure(self):
        if self.client is None:
            import gd_gpu

            self.client = gd_gpu.GpuClient()
        if self._sensors is None:
            self._sensors = gd_memory.MemorySensors()
        return self.client

    def _refresh(self):
        self._ensure()
        snap = self._sensors.sample()
        for key in ROWS:
            self.value_labels[key].setText(human(snap.get(key)))
        self._auto_check(snap)
        self.lbl_admin.setText(i18n.t("нужны права администратора — действия идут через хелпер"))

    def _tick(self):
        self._refresh()

    def _auto_check(self, snap):
        if not self.chk_auto.isChecked():
            self.lbl_auto.setText(i18n.t("Автоочистка выключена."))
            return
        standby_mb = (snap.get("Standby") or 0) / 1024 ** 2
        free_mb = (snap.get("Free") or 0) / 1024 ** 2
        need = standby_mb > self.sp_standby.value() and free_mb < self.sp_free.value()
        self.lbl_auto.setText(
            i18n.t("Standby {v0:.0f} МБ, свободно {v1:.0f} МБ — ", v0=standby_mb, v1=free_mb)
            + (i18n.t("условие выполнено, очищаю.") if need else i18n.t("условие не выполнено."))
        )
        if need:
            self._purge(auto=True)

    # -- действия ----------------------------------------------------------
    def _purge(self, auto: bool = False):
        # запуск хелпера (UAC) и сама операция долгие — уводим в фон, GUI не мерзнет
        if gd_bg.is_running(self._worker):
            return
        self._ensure()
        before = self._sensors.sample()
        if not auto:
            self.btn_purge.setEnabled(False)
            self._set_status(i18n.t("Идёт очистка…"), ok=True)

        self._worker = gd_bg.submit(
            lambda: self.client.call({"cmd": "purge_standby"}),
            lambda resp: self._purge_done(resp, before, auto),
            parent=self,
        )

    def _purge_done(self, resp, before, auto: bool):
        if not auto:
            self.btn_purge.setEnabled(True)
        if not resp.get("ok"):
            self._set_status(i18n.t("Очистка не выполнена: {v0}", v0=resp.get('error')), ok=False)
            # отказ — тоже событие: в журнале должна быть причина, иначе позже
            # по записи не понять, почему очистка не сработала
            gd_journal.record("memory_purge", "standby list", None, None,
                              why=i18n.t("очистка ОЗУ не удалась: {v0}", v0=resp.get('error')),
                              source=i18n.t("утилиты"), reversible=False)
            return
        data = resp.get("data") or {}
        b = data.get("before") or before
        a = data.get("after") or self._sensors.sample()
        self._show_delta(b, a)
        freed = ((a.get("Free") or 0) - (b.get("Free") or 0)) / 1024 ** 2
        who = i18n.t("правило автоочистки") if auto else i18n.t("вручную")
        self._set_status(
            i18n.t("Standby list очищен ({v0}). Свободная память выросла на {v1:+.0f} МБ. Кэш будет наполняться заново — это нормально.", v0=who, v1=freed),
            ok=True,
        )
        gd_journal.record("memory_purge", "standby list",
                          i18n.t("свободно {v0}", v0=human(b.get('Free'))), i18n.t("свободно {v0}", v0=human(a.get('Free'))),
                          why=i18n.t("очистка standby list"), source=i18n.t("правило") if auto else i18n.t("утилиты"),
                          reversible=False)
        if not auto:
            import gd_toast

            gd_toast.show_toast(i18n.t("toast.purge", mb=freed), "ok")

    def _pick_and_empty(self):
        if gd_bg.is_running(self._ws_worker):
            return
        dlg = ProcessPicker(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        pids = dlg.selected_pids()
        if not pids:
            self._set_status(i18n.t("Процессы не выбраны."), ok=False)
            return
        self._ensure()
        before = self._sensors.sample()
        self.btn_ws.setEnabled(False)
        self._set_status(i18n.t("Идёт сжатие…"), ok=True)

        self._ws_worker = gd_bg.submit(
            lambda: self.client.call({"cmd": "empty_working_sets", "pids": pids}),
            lambda resp: self._empty_done(resp, before, len(pids)),
            parent=self,
        )

    def _empty_done(self, resp, before, want: int):
        self.btn_ws.setEnabled(True)
        if not resp.get("ok"):
            self._set_status(i18n.t("Сжатие не выполнено: {v0}", v0=resp.get('error')), ok=False)
            gd_journal.record("memory_working_sets", i18n.t("процессов: {v0}", v0=want),
                              None, None,
                              why=i18n.t("сжатие не удалось: {v0}", v0=resp.get('error')),
                              source=i18n.t("утилиты"), reversible=False)
            return
        data = resp.get("data") or {}
        b = data.get("before") or before
        a = data.get("after") or self._sensors.sample()
        self._show_delta(b, a)
        self._set_status(
            i18n.t("Рабочие наборы сжаты: процессов {v0} из {v1}. Вернётся к обычному объёму по мере работы программ.", v0=data.get('count'), v1=want),
            ok=True,
        )
        gd_journal.record("memory_working_sets", i18n.t("процессов: {v0}", v0=data.get('count')),
                          human(b.get("In Use")), human(a.get("In Use")),
                          why=i18n.t("сжатие рабочих наборов"), source=i18n.t("утилиты"), reversible=False)
        import gd_toast

        gd_toast.show_toast(i18n.t("toast.working_sets", count=data.get("count")), "ok")

    def _show_delta(self, before, after):
        for key in ROWS:
            bv, av = before.get(key), after.get(key)
            self.value_labels[key].setText(human(av))
            if bv is None or av is None:
                self.delta_labels[key].setText("")
            else:
                d = (av - bv) / 1024 ** 2
                self.delta_labels[key].setText(i18n.t("{v0:+.0f} МБ", v0=d))

    def _set_status(self, text, ok=True):
        self.status.setStyleSheet(f"color: {th.MUTED if ok else th.WARN};")
        self.status.setText(text)

    # -- автозапуск с Windows ----------------------------------------------
    def _startup_enabled(self) -> bool:
        """Автозапуск включён: файл в «Автозагрузке» есть (спрашиваем окно)."""
        host = self._startup_host
        if host is None:
            return False
        try:
            return bool(host.start_with_windows())
        except Exception:  # noqa: BLE001
            return False

    def _sync_startup(self) -> None:
        """Галочка показывает факт — при открытии вкладки и после отказа."""
        if self._startup_host is None:
            return
        self.chk_startup.blockSignals(True)
        self.chk_startup.setChecked(self._startup_enabled())
        self.chk_startup.blockSignals(False)

    def _startup_changed(self, state) -> None:
        """Галочка переключена: файл автозагрузки + флаг в presets.json."""
        host = self._startup_host
        if host is None:
            return
        on = state == Qt.Checked
        try:
            err = host.set_start_with_windows(on)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        if err:
            self.lbl_startup.setStyleSheet(f"color: {th.WARN};")
            self.lbl_startup.setText(i18n.t("Автозапуск не изменён: {v0}", v0=err))
            self._sync_startup()      # галочка возвращается к факту, а не к нажатию
            return
        self.lbl_startup.setStyleSheet(f"color: {th.MUTED};")
        self.lbl_startup.setText(i18n.t("Автозапуск включён") if on
                                 else i18n.t("Автозапуск выключен"))

    # -- обновления: галочка и ручная проверка ------------------------------
    def _sync_updates(self) -> None:
        """Галочка — из конфига (факт), а не из памяти виджета."""
        host = self._updates_host
        if host is None:
            return
        try:
            enabled = bool((host.config.get("settings") or {}).get("check_updates", False))
        except Exception:  # noqa: BLE001
            enabled = False
        self.chk_updates.blockSignals(True)
        self.chk_updates.setChecked(enabled)
        self.chk_updates.blockSignals(False)

    def _updates_changed(self, state) -> None:
        """Галочка переключена: хранит её окно, проверка — при следующем запуске."""
        host = self._updates_host
        if host is None:
            return
        on = state == Qt.Checked
        try:
            host.set_check_updates(on)
        except Exception as exc:  # noqa: BLE001
            self.lbl_updates.setStyleSheet(f"color: {th.WARN};")
            self.lbl_updates.setText(i18n.t("Галочка не сохранена: {v0}",
                                            v0=f"{type(exc).__name__}: {exc}"))
            self._sync_updates()      # галочка возвращается к факту, а не к нажатию
            return
        self.lbl_updates.setStyleSheet(f"color: {th.MUTED};")
        self.lbl_updates.setText(i18n.t("Включено: проверю при следующем запуске.") if on
                                 else i18n.t("Выключено: приложение в сеть не ходит."))

    def _check_updates(self) -> None:
        """«Проверить сейчас»: сама проверка идёт в фоне, ответ придёт в подпись."""
        host = self._updates_host
        if host is None:
            return
        host.check_updates(silent=False)

    def set_update_status(self, text: str, ok: bool = True, url: str = "") -> None:
        """Строка результата: сюда пишут и ручная проверка, и стартовая (тихая)."""
        self.lbl_updates.setStyleSheet(f"color: {th.MUTED if ok else th.WARN};")
        if url:
            text = f'{text} <a href="{url}">{i18n.t("скачать")}</a>'
        self.lbl_updates.setText(text)

    # -- автоочистка: настройки ---------------------------------------------
    def _auto_path(self):
        import gd_rules

        return gd_rules._base_dir() / "autoclean.json"

    def _load_auto(self):
        import json

        try:
            data = json.loads(self._auto_path().read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = dict(gd_memory.AUTO_DEFAULTS, enabled=False)
        self.chk_auto.setChecked(bool(data.get("enabled")))
        self.sp_standby.setValue(int(data.get("standby_mb", 1024)))
        self.sp_free.setValue(int(data.get("free_mb", 1024)))

    def _save_auto(self):
        data = {
            "enabled": self.chk_auto.isChecked(),
            "standby_mb": self.sp_standby.value(),
            "free_mb": self.sp_free.value(),
        }
        if not gd_state.write_json_atomic(self._auto_path(), data):
            self._set_status(i18n.t(
                "Настройки автоочистки не сохранены: файл недоступен для записи."), ok=False)
