# -*- coding: utf-8 -*-
"""Вкладка «API агентов» (ТЗ, этап 8): сервер, белый список действий и аудит.

Экран сознательно не обещает «ИИ-оптимизацию»: это локальный API для
инструментов. Всё, что может агент, перечислено таблицей; всё изменяющее
выполняется только после подтверждения в диалоге и попадает в журнал аудита.
"""
from __future__ import annotations

import json
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
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_agent  # noqa: E402
import gd_i18n as i18n  # noqa: E402
import gd_theme as th  # noqa: E402
from gd_power_ui import make_card  # noqa: E402

CONFIRM_SECONDS = 60          # сколько ждём решения по изменяющему запросу

_DECISIONS = {
    "start": "сервер запущен",
    "stop": "сервер остановлен",
    "ok": "выполнено",
    "denied": "отклонено",
    "rejected": "отвергнуто (валидация)",
    "error": "ошибка",
}


def decision_label(decision: str) -> str:
    """Человеческая подпись решения аудита."""
    return i18n.t(_DECISIONS.get(decision, decision or "?"))


def audit_line(entry: dict) -> str:
    """Строка списка аудита: «12:04:31  gpu_limit · выполнено — 200 Вт»."""
    ts = str(entry.get("ts") or "")
    hhmm = ts[11:19] if len(ts) >= 19 else ts
    action = str(entry.get("action") or "?")
    label = decision_label(str(entry.get("decision") or ""))
    detail = str(entry.get("detail") or "")
    return f"{hhmm}  {action}  ·  {label}" + (f"  —  {detail}" if detail else "")


class AgentConfirmDialog(QDialog):
    """Подтверждение изменяющего запроса: «Разрешить» / «Отклонить».

    Диалог немодальный (`open()`): GUI-поток не блокируется, окно остаётся
    живым, а ожидание ответа идёт через сигнал `finished`. По истечении таймера
    запрос отклоняется автоматически — агент получает честный отказ.
    """

    def __init__(self, spec: gd_agent.Action, params: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("Запрос от локального агента"))
        self.setMinimumWidth(460)
        self._left = CONFIRM_SECONDS

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.DIALOG_MARGINS)
        root.setSpacing(th.DIALOG_SPACING)

        title = QLabel(i18n.t(spec.title))
        title.setObjectName("Title")
        title.setWordWrap(True)
        root.addWidget(title)

        mono = QLabel(gd_agent.describe(spec, params))
        mono.setObjectName("Hint")
        mono.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(mono)

        what = QLabel(i18n.t(
            "Изменение выполнится один раз и только после вашего решения. "
            "Если понадобится, Windows отдельно спросит права администратора."
        ))
        what.setObjectName("Hint")
        what.setWordWrap(True)
        root.addWidget(what)

        self.countdown = QLabel("")
        self.countdown.setObjectName("Hint")
        root.addWidget(self.countdown)

        row = QHBoxLayout()
        btn_no = QPushButton(i18n.t("Отклонить"))
        btn_no.setObjectName("Segment")
        btn_no.clicked.connect(self.reject)
        btn_yes = QPushButton(i18n.t("Разрешить"))
        btn_yes.setObjectName("PrimaryButton")
        btn_yes.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(btn_no)
        row.addWidget(btn_yes)
        root.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()

    def _tick(self):
        if self._left <= 0:
            self._timer.stop()
            self.reject()
            return
        self.countdown.setText(i18n.t("автоотклонение через {v0} с", v0=self._left))
        self._left -= 1

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)


class AgentWidget(QWidget):
    """Экран «API агентов»: сервер, действия, аудит.

    Само приложение не запускает/не останавливает сервер напрямую — это делает
    главное окно (`host.start_agent_api` / `host.stop_agent_api`), а виджет
    только показывает состояние и аудит. `standalone` — отдельное окно (скриншоты).
    """

    def __init__(self, standalone: bool = False, host=None):
        super().__init__()
        self.setObjectName("PanelRoot")
        self._standalone = standalone
        self._host = host
        self._running = False
        self._endpoint = ""
        self._sync_guard = False

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.PAGE_MARGINS)
        root.setSpacing(th.PAGE_SPACING)

        # --- СЕРВЕР ---
        card, lay = make_card(i18n.t("СЕРВЕР"))
        row = QHBoxLayout()
        self.lbl_state = QLabel(i18n.t("Выключен"))
        self.lbl_state.setObjectName("Title")
        self.lbl_state.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.lbl_state)
        row.addStretch(1)
        self.btn_toggle = QPushButton(i18n.t("Включить"))
        self.btn_toggle.setObjectName("PrimaryButton")
        self.btn_toggle.clicked.connect(self._toggle)
        row.addWidget(self.btn_toggle)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_copy_ep = QPushButton(i18n.t("Скопировать адрес"))
        self.btn_copy_ep.setObjectName("Segment")
        self.btn_copy_ep.clicked.connect(
            lambda: self._copy(self._endpoint, i18n.t("адрес скопирован")))
        self.btn_copy_token = QPushButton(i18n.t("Скопировать токен"))
        self.btn_copy_token.setObjectName("Segment")
        self.btn_copy_token.clicked.connect(
            lambda: self._copy(self._token(), i18n.t("токен скопирован")))
        self.btn_folder = QPushButton(i18n.t("Открыть папку"))
        self.btn_folder.setObjectName("Segment")
        self.btn_folder.clicked.connect(self._open_folder)
        row2.addWidget(self.btn_copy_ep)
        row2.addWidget(self.btn_copy_token)
        row2.addWidget(self.btn_folder)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.chk_autostart = QCheckBox(i18n.t("Запускать при старте Gammadrilon"))
        self.chk_autostart.toggled.connect(self._autostart_changed)
        lay.addWidget(self.chk_autostart)

        self.lbl_note = QLabel("")
        self.lbl_note.setObjectName("Hint")
        self.lbl_note.setVisible(False)
        lay.addWidget(self.lbl_note)

        self.lbl_warn = QLabel("")
        self.lbl_warn.setObjectName("Hint")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet(f"color: {th.WARN};")
        self.lbl_warn.setVisible(False)
        lay.addWidget(self.lbl_warn)

        hint = QLabel(i18n.t(
            "Локальный API для инструментов: сервер слушает только 127.0.0.1 и требует токен. "
            "Агенту доступны только действия из списка ниже — без реестра, shell и файлов. "
            "Изменения идут только после подтверждения в окне."
        ))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        root.addWidget(card)

        # --- ДЕЙСТВИЯ ---
        card2, lay2 = make_card(i18n.t("ДЕЙСТВИЯ"))
        self.table = QTableWidget(len(gd_agent.ACTIONS), 3)
        self.table.setHorizontalHeaderLabels(
            [i18n.t("Действие"), i18n.t("Тип"), i18n.t("Что делает")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)   # строки разделяют линии из QSS, а не сетка
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        for i, spec in enumerate(gd_agent.ACTIONS):
            self.table.setItem(i, 0, QTableWidgetItem(spec.name))
            kind = i18n.t("изменение") if spec.kind == "mutate" else i18n.t("чтение")
            self.table.setItem(i, 1, QTableWidgetItem(kind))
            self.table.setItem(i, 2, QTableWidgetItem(i18n.t(spec.title)))
        self.table.setMinimumHeight(230)
        lay2.addWidget(self.table)
        root.addWidget(card2)

        # --- АУДИТ ---
        card3, lay3 = make_card(i18n.t("АУДИТ"))
        self.audit = QListWidget()
        self.audit.setObjectName("JournalList")
        self.audit.setSelectionMode(QListWidget.NoSelection)
        self.audit.setMinimumHeight(150)
        lay3.addWidget(self.audit)
        self.audit_empty = QLabel(i18n.t("Пока пусто — вызовов не было."))
        self.audit_empty.setObjectName("Hint")
        lay3.addWidget(self.audit_empty)
        row3 = QHBoxLayout()
        btn_refresh = QPushButton(i18n.t("Обновить"))
        btn_refresh.setObjectName("Segment")
        btn_refresh.clicked.connect(self._reload_audit)
        btn_copy_log = QPushButton(i18n.t("Копировать"))
        btn_copy_log.setObjectName("Segment")
        btn_copy_log.clicked.connect(self._copy_audit)
        row3.addWidget(btn_refresh)
        row3.addWidget(btn_copy_log)
        row3.addStretch(1)
        lay3.addLayout(row3)
        hint3 = QLabel(i18n.t(
            "Журнал аудита только дописывается. "
            "Запись: время, действие, решение и параметры."
        ))
        hint3.setObjectName("Hint")
        hint3.setWordWrap(True)
        lay3.addWidget(hint3)
        root.addWidget(card3)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._refresh)
        self._reload_audit()

    # -- жизненный цикл вкладки --------------------------------------------
    def activate(self):
        """Открытие вкладки: состояние сервера, галка автостарта и аудит."""
        self._timer.start()
        self._refresh()

    def deactivate(self):
        self._timer.stop()

    # -- состояние ----------------------------------------------------------
    def _refresh(self):
        """Тик (или открытие вкладки): синхронизация с главным окном + аудит."""
        self._sync_from_host()
        self._reload_audit()

    def _sync_from_host(self):
        host = self._host
        if host is None:
            return
        try:
            st = host.agent_state()
            autostart = bool(host.agent_autostart())
        except Exception:  # noqa: BLE001
            return
        if st.get("running"):
            self.set_running(int(st.get("port") or 0), st.get("warn") or "")
        else:
            self.set_stopped()
        self._sync_guard = True
        self.chk_autostart.setChecked(autostart)
        self._sync_guard = False

    def set_running(self, port: int, warn: str = "") -> None:
        """Сервер работает: адрес, кнопка «Выключить», предупреждение icacls."""
        self._running = True
        self._endpoint = f"http://127.0.0.1:{port}"
        self.lbl_state.setText(i18n.t("Работает: {v0}", v0=self._endpoint))
        self.btn_toggle.setText(i18n.t("Выключить"))
        self._show_warn(warn)

    def set_stopped(self) -> None:
        self._running = False
        self._endpoint = ""
        self.lbl_state.setText(i18n.t("Выключен"))
        self.btn_toggle.setText(i18n.t("Включить"))
        self._show_warn("")

    def _show_warn(self, text: str) -> None:
        self.lbl_warn.setText(text or "")
        self.lbl_warn.setVisible(bool(text))

    def _note(self, text: str) -> None:
        self.lbl_note.setText(text or "")
        self.lbl_note.setVisible(bool(text))

    # -- действия -----------------------------------------------------------
    def _toggle(self):
        host = self._host
        if host is None:
            self._show_warn(i18n.t("доступно только в главном окне"))
            return
        if self._running:
            host.stop_agent_api()
            self.set_stopped()
            return
        ok, err = host.start_agent_api()
        if not ok:
            self._show_warn(err)
            return
        st = host.agent_state()
        self.set_running(int(st.get("port") or 0), st.get("warn") or "")

    def _autostart_changed(self, state: bool):
        if self._sync_guard or self._host is None:
            return
        try:
            self._host.set_agent_autostart(bool(state))
        except Exception as exc:  # noqa: BLE001
            self._show_warn(f"{type(exc).__name__}: {exc}")

    def _token(self) -> str:
        server = getattr(self._host, "_agent_server", None) if self._host else None
        if server is not None:
            try:
                return server.token()
            except Exception:  # noqa: BLE001
                return ""
        return ""

    def _copy(self, text: str, ok_msg: str):
        if not text:
            self._show_warn(i18n.t("нечего копировать — сервер выключен"))
            return
        QApplication.clipboard().setText(text)
        self._note(ok_msg)

    def _copy_audit(self):
        rows = [audit_line(e) for e in gd_agent.audit_tail(200)]
        if not rows:
            self._show_warn(i18n.t("журнал аудита пуст"))
            return
        QApplication.clipboard().setText("\n".join(rows))
        self._note(i18n.t("журнал скопирован"))

    def _open_folder(self):
        try:
            os.makedirs(gd_agent.base_dir(), exist_ok=True)
            os.startfile(gd_agent.base_dir())  # noqa: S606 — папка данных, не команда
        except Exception as exc:  # noqa: BLE001
            self._show_warn(i18n.t("не удалось открыть папку: {v0}", v0=exc))

    # -- аудит --------------------------------------------------------------
    def _reload_audit(self):
        entries = gd_agent.audit_tail(150)
        self.audit.clear()
        for e in entries:
            it = QListWidgetItem(audit_line(e))
            params = e.get("params") or {}
            it.setToolTip(i18n.t(
                "параметры: {v0}\nисточник: {v1}\nдлительность: {v2} мс",
                v0=json.dumps(params, ensure_ascii=False) if params else "—",
                v1=e.get("remote") or "—",
                v2=e.get("dur_ms") if e.get("dur_ms") is not None else "—",
            ))
            self.audit.addItem(it)
        self.audit_empty.setVisible(not entries)
        self.audit.setVisible(bool(entries))




