# -*- coding: utf-8 -*-
"""Вкладка «Правила» (ТЗ 5.4): таблица Триггер / Условие / Действие / Вкл + лог.

Редактор правил; применение выполняет главное окно (движок gd_rules), здесь —
список, флажки включения и лог последних срабатываний.
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

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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

import gd_rules  # noqa: E402
import gd_theme as th
import gd_i18n as i18n  # noqa: E402


class RuleDialog(QDialog):
    """Добавление/правка одного правила."""

    def __init__(self, rule: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("Правило"))
        self.setStyleSheet(th.STYLESHEET)
        self.resize(460, 300)
        self.rule = dict(rule) if rule else gd_rules.new_rule()
        trig = self.rule.get("trigger") or {}
        cond = self.rule.get("condition") or {}
        act = self.rule.get("action") or {}

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        self.name = QLineEdit(self.rule.get("name") or i18n.t("Новое правило"))
        form.addRow(i18n.t("Название"), self.name)

        self.trig_type = QComboBox()
        for key, label in gd_rules.TRIGGER_TYPES.items():
            self.trig_type.addItem(label, key)
        idx = self.trig_type.findData(trig.get("type"))
        if idx >= 0:
            self.trig_type.setCurrentIndex(idx)
        form.addRow(i18n.t("Триггер"), self.trig_type)

        self.trig_param = QLineEdit()
        if trig.get("type") == "schedule":
            self.trig_param.setText(trig.get("at") or "")
        else:
            self.trig_param.setText(trig.get("process") or "")
        form.addRow(i18n.t("Параметр триггера"), self.trig_param)
        self.trig_hint = QLabel()
        self.trig_hint.setObjectName("Hint")
        self.trig_hint.setWordWrap(True)
        form.addRow("", self.trig_hint)

        self.condition = QLineEdit(
            "" if cond.get("load_below") is None else str(cond.get("load_below"))
        )
        self.condition.setPlaceholderText(i18n.t("только если CPU-загрузка ниже, % (пусто — без условия)"))
        form.addRow(i18n.t("Условие"), self.condition)

        self.act_kind = QComboBox()
        for key, label in gd_rules.ACTIONS.items():
            self.act_kind.addItem(label, key)
        idx = self.act_kind.findData(act.get("kind"))
        if idx >= 0:
            self.act_kind.setCurrentIndex(idx)
        form.addRow(i18n.t("Действие"), self.act_kind)

        self.act_profile = QComboBox()
        form.addRow(i18n.t("Профиль"), self.act_profile)

        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(i18n.t("Сохранить"))
        buttons.button(QDialogButtonBox.Cancel).setText(i18n.t("Отмена"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.trig_type.currentIndexChanged.connect(self._sync)
        self.act_kind.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self):
        ttype = self.trig_type.currentData()
        if ttype == "process_start":
            self.trig_param.setEnabled(True)
            self.trig_param.setPlaceholderText(i18n.t("имя процесса, например deadlock.exe"))
            self.trig_hint.setText(i18n.t("Сработает, когда процесс появится в системе."))
        elif ttype == "on_battery":
            self.trig_param.setEnabled(False)
            self.trig_param.setPlaceholderText("")
            self.trig_hint.setText(
                i18n.t("Сработает при переходе с сети на батарею. На этой машине батарея не обнаружена — правило будет ждать.")
            )
        else:
            self.trig_param.setEnabled(True)
            self.trig_param.setPlaceholderText(i18n.t("ЧЧ:ММ, например 23:30"))
            self.trig_hint.setText(i18n.t("Сработает один раз в сутки в указанное время."))

        kind = self.act_kind.currentData()
        self.act_profile.clear()
        if kind == "cpu_profile":
            for key, label in gd_rules.CPU_PROFILES:
                self.act_profile.addItem(label, key)
        elif kind == "gpu_profile":
            for key, label in gd_rules.GPU_PROFILES:
                self.act_profile.addItem(label, key)
        self.act_profile.setEnabled(kind in ("cpu_profile", "gpu_profile"))
        if kind in gd_rules.UNAVAILABLE_ACTIONS:
            self.trig_hint.setText(
                self.trig_hint.text() + i18n.t("  Действие появится вместе с разделом «Утилиты».")
            )


class RulesWidget(QWidget):
    def __init__(self, standalone: bool = False):
        super().__init__()
        self.setObjectName("PanelRoot")
        self._standalone = standalone
        self.setStyleSheet(th.STYLESHEET)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel(i18n.t("Правила"))
        title.setObjectName("Title")
        title.setVisible(self._standalone)
        head.addWidget(title)
        head.addStretch(1)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("Hint")
        head.addWidget(self.lbl_count)
        root.addLayout(head)

        hint = QLabel(
            i18n.t("Триггер → условие → действие. Правило применяет профиль само и пишет об этом в журнал изменений (источник — «правило»).")
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([i18n.t("Триггер"), i18n.t("Условие"), i18n.t("Действие"), i18n.t("Вкл")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        root.addWidget(self.table, 3)

        self.empty_hint = QLabel(i18n.t("Правил пока нет — нажми «Добавить…»"))
        self.empty_hint.setObjectName("Hint")
        root.addWidget(self.empty_hint)

        brow = QHBoxLayout()
        self.btn_add = QPushButton(i18n.t("Добавить…"))
        self.btn_add.setObjectName("PrimaryButton")
        self.btn_add.clicked.connect(self._add)
        self.btn_edit = QPushButton(i18n.t("Изменить…"))
        self.btn_edit.setObjectName("Segment")
        self.btn_edit.clicked.connect(self._edit)
        self.btn_del = QPushButton(i18n.t("Удалить"))
        self.btn_del.setObjectName("Segment")
        self.btn_del.clicked.connect(self._delete)
        brow.addWidget(self.btn_add)
        brow.addWidget(self.btn_edit)
        brow.addWidget(self.btn_del)
        brow.addStretch(1)
        root.addLayout(brow)

        log_hint = QLabel(i18n.t("Лог сработавших правил (последние 100, с временем)"))
        log_hint.setObjectName("Hint")
        root.addWidget(log_hint)
        self.log = QListWidget()
        self.log.setSelectionMode(QListWidget.NoSelection)
        root.addWidget(self.log, 2)

        self._rules: list[dict] = []
        self.reload()

    # -- данные ------------------------------------------------------------
    def reload(self):
        self._rules = gd_rules.load_rules()
        self.table.setRowCount(len(self._rules))
        for row, rule in enumerate(self._rules):
            trig, cond, act = gd_rules.describe(rule)
            for col, text in ((0, trig), (1, cond), (2, act)):
                it = QTableWidgetItem(text)
                it.setToolTip(text)
                self.table.setItem(row, col, it)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if rule.get("enabled") else Qt.Unchecked)
            self.table.setItem(row, 3, chk)

        self.table.itemChanged.connect(self._on_item_changed) \
            if not getattr(self, "_hooked", False) else None
        self._hooked = True

        self.lbl_count.setText(i18n.t("правил: {v0}", v0=len(self._rules)))
        self.empty_hint.setVisible(not self._rules)
        self._reload_log()

    def _reload_log(self):
        self.log.clear()
        fires = gd_rules.recent_fires()
        for f in fires:
            ts = (f.get("ts") or "")[11:16]
            self.log.addItem(QListWidgetItem(
                f"{ts}  {f.get('rule')} → {f.get('action')}  ({f.get('result')})"
            ))
        if not fires:
            self.log.addItem(QListWidgetItem(i18n.t("Пока ничего не срабатывало.")))

    def _on_item_changed(self, item):
        if item.column() != 3:
            return
        row = item.row()
        if row >= len(self._rules):
            return
        enabled = item.checkState() == Qt.Checked
        if self._rules[row].get("enabled") != enabled:
            self._rules[row]["enabled"] = enabled
            gd_rules.save_rules(self._rules)

    # -- действия ----------------------------------------------------------
    def _selected(self) -> int | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        return min(rows) if rows else None

    def _add(self):
        dlg = RuleDialog(parent=self)
        if dlg.exec_() == QDialog.Accepted:
            rule = dlg.rule
            rule["name"] = dlg.name.text().strip() or rule["name"]
            ttype = dlg.trig_type.currentData()
            rule["trigger"] = {"type": ttype}
            if ttype == "schedule":
                rule["trigger"]["at"] = dlg.trig_param.text().strip()
            elif ttype == "process_start":
                rule["trigger"]["process"] = dlg.trig_param.text().strip()
            txt = dlg.condition.text().strip()
            rule["condition"] = {"load_below": int(txt) if txt.isdigit() else None}
            kind = dlg.act_kind.currentData()
            rule["action"] = {"kind": kind}
            if kind in ("cpu_profile", "gpu_profile"):
                rule["action"]["profile"] = dlg.act_profile.currentData()
            self._rules.append(rule)
            gd_rules.save_rules(self._rules)
            self.reload()

    def _edit(self):
        row = self._selected()
        if row is None:
            return
        dlg = RuleDialog(self._rules[row], parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return
        rule = dlg.rule
        rule["name"] = dlg.name.text().strip() or rule["name"]
        ttype = dlg.trig_type.currentData()
        rule["trigger"] = {"type": ttype}
        if ttype == "schedule":
            rule["trigger"]["at"] = dlg.trig_param.text().strip()
        elif ttype == "process_start":
            rule["trigger"]["process"] = dlg.trig_param.text().strip()
        txt = dlg.condition.text().strip()
        rule["condition"] = {"load_below": int(txt) if txt.isdigit() else None}
        kind = dlg.act_kind.currentData()
        rule["action"] = {"kind": kind}
        if kind in ("cpu_profile", "gpu_profile"):
            rule["action"]["profile"] = dlg.act_profile.currentData()
        self._rules[row] = rule
        gd_rules.save_rules(self._rules)
        self.reload()

    def _delete(self):
        row = self._selected()
        if row is None:
            return
        del self._rules[row]
        gd_rules.save_rules(self._rules)
        self.reload()
