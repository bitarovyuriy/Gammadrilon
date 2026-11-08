# -*- coding: utf-8 -*-
"""Gammadrilon v3 — вкладка «Оптимизация CS2»: один рычаг и честный отчёт.

Своей логики у экрана нет: всё меняет `gd_cs2` (реестр + схема питания), а
экран показывает состояние, даёт снять лишние пункты и поднять приоритет уже
запущенной игре. Настройки — в общем конфиге приложения: `settings.cs2_on` и
`settings.cs2_items` (какие пункты входят в рычаг).

Набор пунктов правится только при выключенном рычаге: иначе снятая галочка
оставляла бы в системе уже применённую правку, и состояние врало бы само себе.
"""
from __future__ import annotations

import os
import sys

# --- PyQt5 и не-ASCII путь (как в main.py) --------------------------------
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
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_cs2  # noqa: E402
import gd_bg  # noqa: E402
import gd_helper  # noqa: E402
import gd_i18n as i18n  # noqa: E402
import gd_theme as th  # noqa: E402
from gd_power_ui import make_card  # noqa: E402


def _level_color(level: str) -> str:
    """Цвет строки отчёта: замечание — ярче, норма — тише."""
    return th.INK if level == "warn" else th.MUTED


class Cs2Widget(QWidget):
    """Экран «Оптимизация CS2»: рычаг, набор пунктов, игра и диагностика."""

    def __init__(self, standalone: bool = False, host=None, config=None, save_fn=None):
        super().__init__()
        self.setObjectName("PanelRoot")
        self._standalone = standalone
        self._host = host
        self._config = config if isinstance(config, dict) else {}
        self._save_fn = save_fn
        self._on = bool(self._settings().get("cs2_on", False))
        self._exe = ""
        self._busy = False
        self._reload_worker = None
        self._reload_again = False
        self._ready = False           # первый снимок получен — можно действовать
        self._want_toggle = False     # клик до готовности: выполним после reload
        self._client = gd_helper.HelperClient()

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.PAGE_MARGINS)
        root.setSpacing(th.PAGE_SPACING)
        # страница длиннее окна: без этого QScrollArea сжимает карточки до
        # нечитаемых полос вместо прокрутки (строки диагностики наезжают друг
        # на друга). Здесь это обязательно — ряды отчётов добавляются на лету.
        root.setSizeConstraint(QLayout.SetMinAndMaxSize)

        # --- РЫЧАГ ---------------------------------------------------------
        card, lay = make_card(i18n.t("РЫЧАГ"),
                              tip=i18n.t("Включить всё сразу и так же разом вернуть прежние значения"))
        row = QHBoxLayout()
        self.lbl_state = QLabel(i18n.t("Выключен"))
        self.lbl_state.setObjectName("Title")
        row.addWidget(self.lbl_state)
        row.addStretch(1)
        self.btn_toggle = QPushButton(i18n.t("Включить"))
        self.btn_toggle.setObjectName("PrimaryButton")
        self.btn_toggle.setMinimumWidth(160)
        self.btn_toggle.clicked.connect(self._toggle)
        row.addWidget(self.btn_toggle)
        lay.addLayout(row)

        self.lbl_counts = QLabel("")
        self.lbl_counts.setObjectName("Hint")
        self.lbl_counts.setWordWrap(True)
        lay.addWidget(self.lbl_counts)

        self.lbl_note = QLabel("")
        self.lbl_note.setObjectName("Hint")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setVisible(False)
        lay.addWidget(self.lbl_note)

        hint = QLabel(i18n.t(
            "Рычаг меняет только пользовательские настройки Windows: права администратора "
            "не нужны, перезагрузка не нужна. Перед первой правкой сохраняются прежние "
            "значения, и «Выключить» возвращает именно их."
        ))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        root.addWidget(card)

        # --- ЧТО ВХОДИТ ----------------------------------------------------
        card2, lay2 = make_card(i18n.t("ЧТО ВХОДИТ"),
                                tip=i18n.t("Снять лишнее можно при выключенном рычаге"))
        self.checkboxes: dict[str, QCheckBox] = {}
        saved = gd_cs2.selected_ids(self._settings().get("cs2_items"))
        chosen = set(saved) if saved else {spec["id"] for spec in gd_cs2.TWEAKS}
        for spec in gd_cs2.TWEAKS:
            box = QCheckBox(i18n.t(spec["title"]))
            box.setToolTip(i18n.t(spec["why"]))
            box.setChecked(spec["id"] in chosen)
            box.toggled.connect(self._items_changed)
            self.checkboxes[spec["id"]] = box
            lay2.addWidget(box)
        self.lbl_exe = QLabel("")
        self.lbl_exe.setObjectName("Hint")
        self.lbl_exe.setWordWrap(True)
        self.lbl_exe.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay2.addWidget(self.lbl_exe)
        root.addWidget(card2)

        # --- ИГРА ----------------------------------------------------------
        card3, lay3 = make_card(i18n.t("ИГРА"),
                                tip=i18n.t("Приоритет High — то же, что даёт -high, но без launch options"))
        row3 = QHBoxLayout()
        self.lbl_game = QLabel(i18n.t("Проверяю…"))
        self.lbl_game.setObjectName("RowValue")
        self.lbl_game.setWordWrap(True)
        row3.addWidget(self.lbl_game, 1)
        self.btn_prio = QPushButton(i18n.t("Поднять приоритет"))
        self.btn_prio.setObjectName("Segment")
        self.btn_prio.clicked.connect(self._raise_priority)
        row3.addWidget(self.btn_prio)
        lay3.addLayout(row3)

        row4 = QHBoxLayout()
        self.btn_cfg = QPushButton(i18n.t("Открыть папку конфига"))
        self.btn_cfg.setObjectName("Segment")
        self.btn_cfg.clicked.connect(self._open_cfg)
        row4.addWidget(self.btn_cfg)
        row4.addStretch(1)
        lay3.addLayout(row4)
        root.addWidget(card3)

        # --- ДИАГНОСТИКА ---------------------------------------------------
        card4, self.lay_diag = make_card(i18n.t("ДИАГНОСТИКА СИСТЕМЫ"),
                                         tip=i18n.t("Меняется только руками и перезагрузкой — рычаг это не трогает"))
        self.lbl_diag_note = QLabel("")
        self.lbl_diag_note.setObjectName("Hint")
        self.lbl_diag_note.setWordWrap(True)
        self.lay_diag.addWidget(self.lbl_diag_note)
        self._diag_rows: list[QWidget] = []
        root.addWidget(card4)

        # --- НАСТРОЙКИ В ИГРЕ ----------------------------------------------
        card5, self.lay_video = make_card(i18n.t("НАСТРОЙКИ В САМОЙ ИГРЕ"),
                                          tip=i18n.t("Читаю cs2_video.txt: то, что выставлено в меню CS2"))
        self.lbl_video_note = QLabel("")
        self.lbl_video_note.setObjectName("Hint")
        self.lbl_video_note.setWordWrap(True)
        self.lay_video.addWidget(self.lbl_video_note)
        self._video_rows: list[QWidget] = []
        root.addWidget(card5)

        # --- LAUNCH OPTIONS -------------------------------------------------
        card6, lay6 = make_card(i18n.t("ПАРАМЕТРЫ ЗАПУСКА"),
                                tip=i18n.t("Launch options в Steam: свойства игры → Общие"))
        advice = QLabel(gd_cs2.launch_advice())
        advice.setObjectName("Hint")
        advice.setWordWrap(True)
        lay6.addWidget(advice)
        root.addWidget(card6)

        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._refresh_game)

        self.reload()

    # -- настройки ----------------------------------------------------------
    def _settings(self) -> dict:
        settings = self._config.get("settings")
        if not isinstance(settings, dict):
            settings = {}
            self._config["settings"] = settings
        return settings

    def _save(self) -> None:
        if self._save_fn is None:
            return
        try:
            if self._save_fn() is False:
                self._note(i18n.t("Не удалось сохранить настройки CS2."))
        except Exception as exc:  # noqa: BLE001
            self._note(i18n.t("Не удалось сохранить настройки CS2: {v0}", v0=exc))

    def _chosen(self) -> list[str]:
        return [spec["id"] for spec in gd_cs2.TWEAKS
                if self.checkboxes[spec["id"]].isChecked()]

    # -- жизненный цикл вкладки --------------------------------------------
    def activate(self) -> None:
        self._timer.start()
        self.reload()

    def deactivate(self) -> None:
        self._timer.stop()

    # -- состояние ----------------------------------------------------------
    def reload(self) -> None:
        """Полное чтение: рычаг, игра, диагностика, настройки игры."""
        if gd_bg.is_running(self._reload_worker):
            self._reload_again = True
            return

        def work():
            return {"snapshot": gd_cs2.snapshot(), "diagnostics": gd_cs2.diagnostics(),
                    "video": gd_cs2.video_notes(), "video_path": gd_cs2.video_file()}

        self._reload_worker = gd_bg.submit(work, self._reload_done, parent=self)

    def _reload_done(self, result):
        self._reload_worker = None
        snap = result.get("snapshot")
        if not isinstance(snap, dict):
            self._note(str(result.get("error") or i18n.t("Состояние CS2 недоступно.")))
            return
        self._exe = snap.get("exe") or ""
        self._ready = True
        self._render_state(snap)
        self._refresh_game()
        self._render_diagnostics(result.get("diagnostics") or [])
        self._render_video(result.get("video") or [], result.get("video_path"))
        if self._want_toggle:
            # клик с главного экрана ждал этого снимка
            self._want_toggle = False
            self._toggle()
        if self._reload_again:
            self._reload_again = False
            self.reload()

    def _render_state(self, snap: dict) -> None:
        items = snap.get("items") or {}
        on_count = sum(1 for value in items.values() if value)
        # состояние рычага — факт системы, а не то, что мы помним про себя:
        # значения могли включить руками или другим приложением
        self._on = bool(snap.get("can_revert"))
        self._settings()["cs2_on"] = self._on
        label = (i18n.t("Включён") if items and all(items.values()) else
                 i18n.t("Частично включён") if on_count else
                 i18n.t("Есть изменения для отката") if self._on else i18n.t("Выключен"))
        self.lbl_state.setText(label)
        self.btn_toggle.setText(i18n.t("Выключить") if self._on else i18n.t("Включить"))
        for box in self.checkboxes.values():
            box.setEnabled(not self._on)
        if snap.get("found"):
            self.lbl_exe.setText(i18n.t("cs2.exe: {v0}", v0=snap.get("exe")))
            self.lbl_exe.setStyleSheet("")
        else:
            self.lbl_exe.setText(i18n.t(
                "cs2.exe не найдена: пункты, привязанные к игре, работать не будут. "
                "Проверьте, что Steam видит Counter-Strike 2."
            ))
            self.lbl_exe.setStyleSheet(f"color: {th.WARN};")
        counts = i18n.t("Пунктов включено в системе: {v0} из {v1}.", v0=on_count, v1=len(items))
        if not self._on and on_count:
            counts += " " + i18n.t(
                "Часть уже стояла в системе до рычага — «Включить» допишет остальное.")
        self.lbl_counts.setText(counts)
        self._sync_checks(items)

    def _sync_checks(self, items: dict) -> None:
        """Галочки показывают факт: что реально включено в системе прямо сейчас."""
        for spec in gd_cs2.TWEAKS:
            box = self.checkboxes[spec["id"]]
            box.blockSignals(True)
            if self._on:
                box.setChecked(bool(items.get(spec["id"])))
            else:
                chosen = set(gd_cs2.selected_ids(self._settings().get("cs2_items")))
                box.setChecked(spec["id"] in (chosen or {s["id"] for s in gd_cs2.TWEAKS}))
            box.blockSignals(False)

    def _refresh_game(self) -> None:
        pids = gd_cs2.cs2_pids()
        show_prio = False
        if not pids:
            self.lbl_game.setText(i18n.t("CS2 не запущена."))
        else:
            parts = []
            for pid in pids[:3]:
                prio = gd_cs2.priority_of(pid) or i18n.t("?")
                parts.append(i18n.t("pid {v0}, приоритет {v1}", v0=pid, v1=prio))
            self.lbl_game.setText(i18n.t("CS2 запущена: {v0}", v0=" · ".join(parts)))
            show_prio = True
        self.btn_prio.setVisible(show_prio)

    # -- действия -----------------------------------------------------------
    def toggle(self) -> bool:
        """Публичная точка входа для плитки главного экрана.

        Плитка зовёт это сразу после создания страницы, а её первый снимок ещё
        в работе: путь к игре (`_exe`) пуст, часть пунктов пропускается, а
        ``_on`` взят из конфига. Поэтому до готовности действие запоминается.
        """
        if not self._ready:
            self._want_toggle = True
            self._note(i18n.t("Читаю состояние игры — действие выполнится через секунду."))
            return True
        self._toggle()
        return True

    def _toggle(self) -> None:
        if self._busy:
            return
        chosen = self._chosen()
        if not self._on and not chosen:
            self._note(i18n.t("Отметьте хотя бы один пункт — иначе рычагу нечего делать."))
            return
        self._busy = True
        self.btn_toggle.setEnabled(False)
        request = ({"cmd": "cs2_revert"} if self._on else
                   {"cmd": "cs2_apply", "items": list(chosen), "exe": self._exe})
        client = self._client
        self._worker = gd_bg.submit(lambda: client.call(request, timeout=120),
                                    self._toggle_done, parent=self)

    def _toggle_done(self, response):
        self._busy = False
        self.btn_toggle.setEnabled(True)
        report = response.get("data") or {}
        if not response.get("ok"):
            self._note(i18n.t("Не всё выполнено: {v0}", v0=response.get("error") or ""))
        elif "restored" in report:
            self._note(i18n.t("Вернул прежние значения: {v0} записей.", v0=report["restored"]))
        else:
            self._note(i18n.t("Включено: {v0}.", v0=", ".join(report.get("applied") or [])))
        self._settings()["cs2_items"] = self._chosen()
        self._save()
        self.reload()

    def _items_changed(self, _checked: bool) -> None:
        """Галочку двигают только при выключенном рычаге: правка уходит в настройки."""
        if self._on:
            return
        self._settings()["cs2_items"] = self._chosen()
        self._save()

    def _raise_priority(self) -> None:
        report = gd_cs2.raise_priority()
        if report.get("ok"):
            self._note(i18n.t("Приоритет High поднят: процессов {v0}.", v0=report.get("raised")))
        else:
            self._note(i18n.t("Не вышло: {v0}", v0="; ".join(report.get("errors") or [])))
        self._refresh_game()

    def _open_cfg(self) -> None:
        path = gd_cs2.video_file()
        if path is None:
            self._note(i18n.t("Файл настроек игры не найден: CS2 ни разу не запускалась."))
            return
        try:
            os.startfile(str(path.parent))  # noqa: S606 — папка конфига, не команда
        except Exception as exc:  # noqa: BLE001
            self._note(i18n.t("Не удалось открыть папку: {v0}", v0=exc))

    def _note(self, text: str) -> None:
        self.lbl_note.setText(text or "")
        self.lbl_note.setVisible(bool(text))

    # -- отчёты -------------------------------------------------------------
    def _clear(self, rows: list[QWidget]) -> None:
        while rows:
            widget = rows.pop()
            widget.setParent(None)
            widget.deleteLater()

    def _add_row(self, layout, rows: list[QWidget], title: str, value: str,
                 note: str = "", level: str = "info") -> None:
        box = QWidget()
        box_lay = QVBoxLayout(box)
        box_lay.setContentsMargins(0, 0, 0, 0)
        box_lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        name = QLabel(title)
        name.setObjectName("Hint")
        shown = QLabel(value)
        shown.setObjectName("RowValue")
        shown.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        shown.setStyleSheet(f"color: {_level_color(level)};")
        head.addWidget(name)
        head.addStretch(1)
        head.addWidget(shown)
        box_lay.addLayout(head)
        if note:
            hint = QLabel(note)
            hint.setObjectName("Hint")
            hint.setWordWrap(True)
            box_lay.addWidget(hint)
        layout.addWidget(box)
        rows.append(box)

    def _render_diagnostics(self, items) -> None:
        self._clear(self._diag_rows)
        self.lbl_diag_note.setText("")
        for item in items:
            self._add_row(self.lay_diag, self._diag_rows, item["title"], item["value"],
                          item.get("note", ""), item.get("level", "info"))

    def _render_video(self, rows, path) -> None:
        self._clear(self._video_rows)
        if not rows:
            self.lbl_video_note.setText(i18n.t(
                "Не нашёл cs2_video.txt: настройки игры появятся здесь после первого запуска CS2."))
            return
        self.lbl_video_note.setText(i18n.t("Файл: {v0}", v0=str(path)))
        for item in rows:
            self._add_row(self.lay_video, self._video_rows, item["title"], item["value"],
                          item.get("note", ""), item.get("level", "info"))


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyleSheet(th.STYLESHEET)
    page = Cs2Widget(standalone=True)
    page.resize(760, 900)
    page.show()
    sys.exit(app.exec_())
