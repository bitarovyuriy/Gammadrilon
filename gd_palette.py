# -*- coding: utf-8 -*-
"""Палитра команд (ТЗ 5.2): Ctrl+Alt+G, нечёткий поиск, `>` — быстрый параметр.

«Команда без параметра — вежливая просьба. Команда с параметром — приказ.»
Ввод, начинающийся с `>`, трактуется как быстрый параметр: `>200` — лимит GPU 200 Вт,
`>50` — предел частоты CPU 50 %.
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
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gd_theme as th  # noqa: E402
import gd_i18n as i18n  # noqa: E402


def fuzzy(query: str, text: str) -> tuple[bool, int]:
    """Нечёткое совпадение по подпоследовательности. (подходит, вес)."""
    q, t = query.lower(), text.lower()
    if not q:
        return True, 0
    i = 0
    score = 0
    run = 0
    for ch in t:
        if i < len(q) and ch == q[i]:
            i += 1
            run += 1
            score += run
        else:
            run = 0
    return i == len(q), score


class CommandPalette(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.mw = main_window
        self.setWindowTitle(i18n.t("Палитра команд"))
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint | Qt.Popup)
        self.resize(560, 380)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.input = QLineEdit()
        self.input.setObjectName("PaletteInput")
        self.input.setPlaceholderText(i18n.t("Что сделать?  ·  >200 — быстрый параметр  ·  Esc — отмена"))
        self.input.textChanged.connect(self._refilter)
        self.input.returnPressed.connect(self._run_current)
        root.addWidget(self.input)

        self.list = QListWidget()
        self.list.setObjectName("PaletteList")
        self.list.itemActivated.connect(lambda _: self._run_current())
        self.list.itemClicked.connect(lambda _: self._run_current())
        root.addWidget(self.list, 1)

        self.hint = QLabel(i18n.t("Enter — выполнить  ·  ↑↓ — выбор  ·  Esc — закрыть"))
        self.hint.setObjectName("Hint")
        root.addWidget(self.hint)

        self._commands = self._collect()
        self._refilter("")

    # -- сборка команд -----------------------------------------------------
    def _collect(self):
        mw = self.mw
        # i18n: skip-start — поисковые ключи палитры: не отображаются, содержат слова
        # обоих языков (RU/EN), чтобы фильтр находил команды на любом языке ввода.
        cmds = [
            (i18n.t("pal.cpu_eco"), "cpu power eco энергосбережение тихий профиль",
             lambda: mw.on_cpu_profile("eco")),
            (i18n.t("pal.cpu_normal"), "cpu power normal обычный базовый профиль",
             lambda: mw.on_cpu_profile("normal")),
            (i18n.t("pal.cpu_boost"), "cpu power boost максимум производительность профиль",
             lambda: mw.on_cpu_profile("boost")),
            (i18n.t("pal.cpu_restore"), "cpu power restore откат сброс схема",
             lambda: mw.on_cpu_profile("restore")),
            (i18n.t("pal.gpu_quiet"), "gpu quiet тихий 150 лимит профиль",
             lambda: mw.on_gpu_profile("quiet")),
            (i18n.t("pal.gpu_default"), "gpu default дефолт номинал профиль",
             lambda: mw.on_gpu_profile("default")),
            (i18n.t("pal.gpu_game"), "gpu game игровой профиль",
             lambda: mw.on_gpu_profile("game")),
            (i18n.t("pal.gpu_max"), "gpu max максимум профиль",
             lambda: mw.on_gpu_profile("max")),
            (i18n.t("pal.gpu_restore"), "gpu restore откат лимит сброс",
             lambda: mw.on_gpu_profile("restore")),
            (i18n.t("pal.perf_toggle"), "performance toggle boost eco ограничение производительность переключить",
             lambda: mw._on_perf_toggle()),
            (i18n.t("pal.open_gamma"), "open tab gamma гамма цвет окно",
             lambda: self._open_tab(0)),
            (i18n.t("pal.open_cpu"), "open tab cpu питание процессор",
             lambda: self._open_tab(1)),
            (i18n.t("pal.open_gpu"), "open tab gpu питание видеокарта",
             lambda: self._open_tab(2)),
            (i18n.t("pal.open_rules"), "open tab rules правила",
             lambda: self._open_tab(3)),
            (i18n.t("pal.open_utils"), "open tab utils утилиты очистка память",
             lambda: self._open_tab(4)),
            (i18n.t("pal.open_dns"), "open tab dns резолвер латентность",
             lambda: self._open_tab(5)),
            (i18n.t("pal.open_zapret"), "open tab zapret dpi обход discord youtube winws",
             lambda: self._open_tab(6)),
            (i18n.t("pal.open_agent"), "open tab agent api агент локальный инструменты шлюз",
             lambda: self._open_tab(7)),
            (i18n.t("pal.theme"), "theme тема светлая тёмная оформление",
             lambda: self.mw.toggle_theme()),
            (i18n.t("pal.language"), "language язык english русский",
             lambda: self.mw.toggle_language()),
            (i18n.t("pal.onboarding"), "onboarding первый запуск знакомство",
             lambda: self.mw.show_first_run()),
            (i18n.t("pal.purge"), "memory standby ram память очистка озу кэш",
             lambda: self._purge_memory()),
            (i18n.t("pal.journal"), "journal log журнал история что произошло",
             lambda: mw.open_journal()),
            (i18n.t("pal.reset_gamma"), "reset gamma сброс гамма",
             lambda: mw.reset_all()),
            (i18n.t("pal.stop_crane"), "stop crane reset everything сброс стоп кран вернуть всё как было откат",
             lambda: mw.stop_crane()),
            # поиск по настройкам: команда открывает вкладку и ставит фокус в поле
            (i18n.t("pal.goto_brightness"), "setting настройка яркость brightness гамма",
             lambda: self._goto("tab.gamma", "brightness_slider")),
            (i18n.t("pal.goto_contrast"), "setting настройка контраст contrast гамма",
             lambda: self._goto("tab.gamma", "contrast_slider")),
            (i18n.t("pal.goto_temperature"), "setting настройка температура цвет color temperature гамма",
             lambda: self._goto("tab.gamma", "temperature_slider")),
            (i18n.t("pal.goto_preset"), "setting настройка пресет preset гамма",
             lambda: self._goto("tab.gamma", "preset_combo")),
            (i18n.t("pal.goto_cpu_max"), "setting настройка предел частота cpu частота percent",
             lambda: self._goto("tab.cpu", "sl_max")),
            (i18n.t("pal.goto_gpu_pl"), "setting настройка лимит мощность gpu watts ватт",
             lambda: self._goto("tab.gpu", "sl_pl")),
            (i18n.t("pal.goto_dns"), "setting настройка dns серверы резолвер таблица",
             lambda: self._goto("tab.dns", "table")),
            (i18n.t("pal.goto_autoclean"), "setting настройка автоочистка озу память autoclean standby",
             lambda: self._goto("tab.utils", "chk_auto")),
            (i18n.t("pal.goto_startup"), "setting настройка автозапуск windows startup",
             lambda: self._goto("tab.utils", "chk_startup")),
            (i18n.t("pal.goto_updates"), "setting настройка обновления github updates",
             lambda: self._goto("tab.utils", "chk_updates")),
            (i18n.t("pal.quit"), "quit exit выход закрыть",
             lambda: mw.quit_app()),
        ]
        # i18n: skip-end
        return cmds

    def _param_commands(self, value: int):
        cmds = [
            (i18n.t("pal.gpu_limit", value=value), f"gpu limit power watts {value}",
             lambda: self._set_gpu_watts(value)),
        ]
        if value <= 100:
            cmds.append(
                (i18n.t("pal.cpu_limit", value=value), f"cpu limit percent {value}",
                 lambda: self._set_cpu_percent(value))
            )
        return cmds

    # -- параметр `>` ------------------------------------------------------
    def _is_param(self, text: str):
        t = text.strip()
        if not t.startswith(">"):
            return None
        digits = t[1:].strip().split(" ")[0]
        if not digits.isdigit():
            return None
        return int(digits)

    def _purge_memory(self):
        """Очистка standby из палитры: ленивая вкладка «Утилиты» собирается под вызов."""
        self.mw._ensure_page("tab.utils")
        page = getattr(self.mw, "_utils_page", None)
        if page is not None:
            page._purge()

    def _set_gpu_watts(self, watts: int):
        g = self.mw._ensure_page("tab.gpu")
        if g is None:
            return
        lo = int(g.sl_pl.minimum())
        hi = int(g.sl_pl.maximum())
        value = max(lo, min(hi, watts))
        g.sl_pl.setValue(value)
        g._source = i18n.t("палитра команд")
        g._apply({"pl": value})

    def _set_cpu_percent(self, pct: int):
        p = self.mw._ensure_page("tab.cpu")
        if p is None:
            return
        value = max(5, min(100, pct))
        try:
            p.sl_max.setValue(value)
        except AttributeError:
            pass
        p.apply_custom(source=i18n.t("палитра команд"))

    def _open_tab(self, index: int):
        self.mw.tabs.setCurrentIndex(index)
        self.mw.show_window()

    def _goto(self, page_key: str, attr: str = ""):
        """Поиск по настройкам: открыть страницу и поставить фокус в поле.

        Так палитра отвечает на «где яркость» делом, а не только открытием
        вкладки: человек сразу видит, что крутить. Страница собирается лениво
        тем же ``_ensure_page``, что и при клике по пункту меню.
        """
        mw = self.mw
        idx = mw._key_to_index.get(page_key)
        if idx is None:
            return
        mw.tabs.setCurrentIndex(idx)
        mw.show_window()
        if not attr:
            return
        page = mw._ensure_page(page_key)
        target = getattr(page, attr, None) if page is not None else None
        if target is None and isinstance(page, QScrollArea):
            # «Утилиты» живут внутри своей обёртки прокрутки: поле — у виджета,
            # а не у прокрутки снаружи
            target = getattr(page.widget(), attr, None)
        if target is None:
            # слайдеры гаммы живут в самом окне, а не на странице-виджете
            target = getattr(mw, attr, None)
        if target is not None:
            target.setFocus(Qt.OtherFocusReason)

    # -- фильтр ------------------------------------------------------------
    def _refilter(self, text):
        self.list.clear()
        param = self._is_param(text)
        if param is not None:
            entries = self._param_commands(param)
            self.hint.setText(i18n.t("Быстрый параметр: {v0} — Enter выполнить", v0=param))
        else:
            q = text.strip()
            scored = []
            for title, keywords, action in self._commands:
                ok, score = fuzzy(q, title + " " + keywords)
                if ok:
                    scored.append((score, title, action))
            scored.sort(key=lambda x: -x[0])
            entries = [(t, "", a) for _, t, a in scored[:40]]
            self.hint.setText(i18n.t("Enter — выполнить  ·  >число — быстрый параметр"))
        for title, _kw, action in entries:
            it = QListWidgetItem(title)
            it.setData(Qt.UserRole, action)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            # пустое состояние честное: палитра не молчит, а говорит, почему пусто
            empty = QListWidgetItem(i18n.t("Ничего не найдено — измени запрос"))
            empty.setFlags(Qt.NoItemFlags)
            empty.setForeground(QBrush(QColor(th.MUTED)))
            self.list.addItem(empty)
            self.hint.setText(i18n.t("Ничего не найдено — измени запрос"))

    def _run_current(self):
        it = self.list.currentItem()
        if it is None or it.data(Qt.UserRole) is None:   # строка «ничего не найдено»
            return
        action = it.data(Qt.UserRole)
        self.accept()
        if callable(action):
            action()
