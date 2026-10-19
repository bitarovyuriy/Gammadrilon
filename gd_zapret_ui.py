# -*- coding: utf-8 -*-
"""Вкладка «Обход DPI» — контроллер zapret-discord-youtube (ТЗ 8, уровень A).

Gammadrilon не форкает и не встраивает zapret: папка остаётся внешней и
обновляется автором. Мы «осваиваем» стратегии (запуск general*.bat → захват
argv winws.exe → кэш) и дальше включаем/выключаем winws.exe напрямую —
без cmd.exe, с контролем статуса и stdout, точным убийством по PID.

Привилегированные операции идут через общий elevated-хелпер
(`gd_gpu_service.py`, команды zapret_*); чтение состояния — без прав.
"""
from __future__ import annotations

import os

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

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import gd_i18n as i18n  # noqa: E402
import gd_journal  # noqa: E402
import gd_theme as th
import gd_zapret  # noqa: E402
from gd_power_ui import make_card  # noqa: E402


def _fmt_uptime(seconds) -> str:
    try:
        sec = max(0, int(seconds))
    except (TypeError, ValueError):
        return "—"
    if sec < 90:
        return i18n.t("{v0} с", v0=sec)
    if sec < 3600:
        return i18n.t("{v0} мин", v0=sec // 60)
    return i18n.t("{v0} ч {v1} мин", v0=sec // 3600, v1=(sec % 3600) // 60)


def _fmt_size(size: int) -> str:
    if size >= 1024 * 1024:
        return i18n.t("{v0:.1f} МБ", v0=size / (1024 * 1024))
    if size >= 1024:
        return i18n.t("{v0:.0f} КБ", v0=size / 1024)
    return i18n.t("{v0} Б", v0=size)


def _fmt_date(ts) -> str:
    if not ts:
        return "—"
    import time

    return time.strftime("%d.%m.%Y", time.localtime(ts))


class ZapretWidget(QWidget):
    """Экран «Обход DPI»: путь, статус, стратегии, тумблеры, журнал winws."""

    def __init__(self, standalone: bool = False, host=None):
        super().__init__()
        self.setObjectName("PanelRoot")
        self._standalone = standalone
        self.host = host

        self.client = None
        self._snap = {}
        self._path_manual = ""
        self._ignored = set()          # «оставить как есть» — до конца сессии
        self._worker = None            # фоновое действие (включить/выключить/…)
        self._gen = 0                  # поколение состояния: см. _snap_job/_on_snap
        # прошлый запуск приложения мог закрыться вместе с winws — это не «он
        # умер сам»: снимаем наследство молча, см. gd_zapret.forget_previous_run
        gd_zapret.forget_previous_run()

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.PAGE_MARGINS)
        root.setSpacing(th.PAGE_SPACING)

        head = QHBoxLayout()
        title = QLabel(i18n.t("Обход DPI"))
        title.setObjectName("Title")
        title.setVisible(self._standalone)
        head.addWidget(title)
        head.addStretch(1)
        root.addLayout(head)

        # --- папка zapret ---------------------------------------------------
        card, lay = make_card(
            i18n.t("ПАПКА ZAPRET"),
            i18n.t("Папка комплекта zapret-discord-youtube. Комплект внешний: "
                   "Gammadrilon — контроллер и UI, обновления приходят "
                   "от автора zapret. Путь — без кириллицы."),
        )
        prow = QHBoxLayout()
        self.lbl_path = QLabel(i18n.t("не выбрана"))
        self.lbl_path.setObjectName("Value")
        self.lbl_path.setToolTip("")
        prow.addWidget(self.lbl_path, 1)
        self.btn_pick = QPushButton(i18n.t("Выбрать папку…"))
        self.btn_pick.setObjectName("Segment")
        self.btn_pick.clicked.connect(self._pick_path)
        prow.addWidget(self.btn_pick)
        self.btn_open = QPushButton(i18n.t("Открыть папку"))
        self.btn_open.setObjectName("Segment")
        self.btn_open.clicked.connect(self._open_folder)
        prow.addWidget(self.btn_open)
        lay.addLayout(prow)
        self.lbl_path_note = QLabel("")
        self.lbl_path_note.setObjectName("Hint")
        self.lbl_path_note.setWordWrap(True)
        lay.addWidget(self.lbl_path_note)
        root.addWidget(card)

        # --- состояние ------------------------------------------------------
        card2, lay2 = make_card(
            i18n.t("СОСТОЯНИЕ"),
            i18n.t("ВЫКЛЮЧЕНО / РАБОТАЕТ (наша стратегия, PID и аптайм) / "
                   "внешний winws.exe, который Gammadrilon не трогает "
                   "без решения."),
        )
        self.lbl_state = QLabel(i18n.t("—"))
        self.lbl_state.setObjectName("Value")
        lay2.addWidget(self.lbl_state)
        self.lbl_detail = QLabel("")
        self.lbl_detail.setObjectName("Hint")
        self.lbl_detail.setWordWrap(True)
        lay2.addWidget(self.lbl_detail)

        self.lbl_conflict = QLabel("")
        self.lbl_conflict.setObjectName("Hint")
        self.lbl_conflict.setWordWrap(True)
        self.lbl_conflict.setVisible(False)
        lay2.addWidget(self.lbl_conflict)
        crow = QHBoxLayout()
        self.btn_adopt = QPushButton(i18n.t("Взять под управление"))
        self.btn_adopt.setObjectName("Segment")
        self.btn_adopt.clicked.connect(self._adopt)
        self.btn_adopt.setVisible(False)
        crow.addWidget(self.btn_adopt)
        self.btn_ignore = QPushButton(i18n.t("Оставить как есть"))
        self.btn_ignore.setObjectName("Segment")
        self.btn_ignore.clicked.connect(self._ignore_foreign)
        self.btn_ignore.setVisible(False)
        crow.addWidget(self.btn_ignore)
        crow.addStretch(1)
        lay2.addLayout(crow)

        brow = QHBoxLayout()
        self.btn_on = QPushButton(i18n.t("Включить"))
        self.btn_on.setObjectName("PrimaryButton")
        self.btn_on.clicked.connect(self._on_click)
        brow.addWidget(self.btn_on)
        self.btn_off = QPushButton(i18n.t("Выключить"))
        self.btn_off.setObjectName("Segment")
        self.btn_off.clicked.connect(self._off)
        brow.addWidget(self.btn_off)
        self.btn_recon = QPushButton(i18n.t("Переосвоить"))
        self.btn_recon.setObjectName("Segment")
        self.btn_recon.clicked.connect(self._recon)
        brow.addWidget(self.btn_recon)
        self.btn_svc = QPushButton(i18n.t("service.bat…"))
        self.btn_svc.setObjectName("Segment")
        self.btn_svc.setToolTip(i18n.t(
            "Видимый запуск service.bat: служба zapret, обновление списков, "
            "диагностика — меню откроется в своём окне."))
        self.btn_svc.clicked.connect(self._svc_launch)
        brow.addWidget(self.btn_svc)
        brow.addStretch(1)
        lay2.addLayout(brow)

        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        lay2.addWidget(self.status)
        root.addWidget(card2)

        # --- стратегии ------------------------------------------------------
        card3, lay3 = make_card(
            i18n.t("СТРАТЕГИИ"),
            i18n.t("Список — файлы general*.bat в корне папки. «Освоение» "
                   "один раз ловит аргументы winws.exe; дальше запуск "
                   "идёт напрямую — без cmd.exe, с контролем статуса и вывода."),
        )
        self.list = QListWidget()
        self.list.setFixedHeight(210)
        self.list.currentItemChanged.connect(lambda *_: self._on_sel())
        lay3.addWidget(self.list)
        self.lbl_sel = QLabel("")
        self.lbl_sel.setObjectName("Hint")
        self.lbl_sel.setWordWrap(True)
        lay3.addWidget(self.lbl_sel)
        root.addWidget(card3)

        # --- фильтры --------------------------------------------------------
        card4, lay4 = make_card(
            i18n.t("ФИЛЬТРЫ"),
            i18n.t("GameFilter — порты для игр: батник читает их при "
                   "запуске, поэтому тумблер запускает повторное "
                   "освоение. IPSet — список IP из lists\\ipset-all.txt: winws "
                   "читает его при старте, поэтому при работающем обходе "
                   "список перезапускается."),
        )
        frow = QHBoxLayout()
        self.chk_tcp = QCheckBox(i18n.t("GameFilter TCP"))
        self.chk_tcp.clicked.connect(self._on_toggle_gf)
        frow.addWidget(self.chk_tcp)
        self.chk_udp = QCheckBox(i18n.t("GameFilter UDP"))
        self.chk_udp.clicked.connect(self._on_toggle_gf)
        frow.addWidget(self.chk_udp)
        frow.addSpacing(18)
        self.chk_ipset = QCheckBox(i18n.t("IPSet Filter"))
        self.chk_ipset.clicked.connect(self._on_toggle_ipset)
        frow.addWidget(self.chk_ipset)
        frow.addStretch(1)
        lay4.addLayout(frow)
        self.lbl_lists = QLabel("")
        self.lbl_lists.setObjectName("Hint")
        self.lbl_lists.setWordWrap(True)
        lay4.addWidget(self.lbl_lists)
        root.addWidget(card4)

        # --- журнал winws ---------------------------------------------------
        card5, lay5 = make_card(
            i18n.t("ЖУРНАЛ WINWS"),
            i18n.t("Последние строки вывода winws.exe — при прямом "
                   "запуске. WinDivert и стратегия пишут сюда же."),
        )
        self.lbl_journal = QLabel("")
        self.lbl_journal.setObjectName("Hint")
        lay5.addWidget(self.lbl_journal)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(400)
        self.txt_log.setFixedHeight(150)
        lay5.addWidget(self.txt_log)
        jrow = QHBoxLayout()
        self.btn_copy = QPushButton(i18n.t("Копировать"))
        self.btn_copy.setObjectName("Segment")
        self.btn_copy.clicked.connect(self._copy_log)
        jrow.addWidget(self.btn_copy)
        jrow.addStretch(1)
        lay5.addLayout(jrow)
        root.addWidget(card5)
        root.addStretch(1)

        self._poll = gd_bg.Periodic(self, 2000, self._snap_job, self._on_snap)
        if self._standalone:
            self.activate()


    # -- жизненный цикл вкладки ---------------------------------------------

    def activate(self):
        """Открытие вкладки — включаем опрос (psutil + файлы состояния)."""
        self._poll.start(2000)
        self._poll.trigger()

    def deactivate(self):
        self._poll.stop()

    @property
    def busy(self) -> bool:
        """Идёт ли фоновое действие (включить/выключить/переосвоить).

        Пока оно идёт, состояние процесса распоряжается само действие — внешние
        проверки («не умер ли наш winws») должны молчать, иначе собственный
        перезапуск выглядел бы смертью со стороны.
        """
        return gd_bg.is_running(self._worker)

    def check_vanished(self) -> None:
        """Дешёвый внешний вопрос: не исчез ли наш winws? (зовёт главное окно).

        Опрос вкладки (2 с, перебор процессов через psutil) живёт только на
        открытой вкладке, а обход включают и сворачивают окно. Поэтому смерть
        процесса проверяется ещё и тиком статусбара — файл состояния и один
        процесс вместо полного снимка.
        """
        if self.busy:
            return                 # действие само знает про процесс, см. busy
        state = gd_zapret.take_vanished()
        if state is not None:
            self._report_vanished(state)

    def _report_vanished(self, state: dict) -> None:
        """Сказать, что наш winws исчез: строка вкладки, журнал и тост.

        Тост — потому что окно может быть свёрнуто: тихая правка строки на
        закрытой вкладке — это молчание, а не отчёт.
        """
        pid = state.get("pid")
        self._set_status(i18n.t("winws.exe завершился вне Gammadrilon."), ok=False)
        gd_journal.record("zapret", state.get("strategy") or i18n.t("обход"),
                          before=i18n.t("работает (PID {v0})", v0=pid),
                          after=i18n.t("завершился вне приложения"),
                          why=i18n.t("посторонняя остановка"),
                          reversible=False)
        import gd_toast

        gd_toast.show_toast(i18n.t("toast.zapret_gone"), "warn")

    # -- путь и конфиг -------------------------------------------------------

    def current_path(self) -> str:
        """Выбранная папка zapret (для трея, локального API и статусбара)."""
        return self._path()

    def _path(self) -> str:
        if self._standalone or self.host is None:
            return self._path_manual
        try:
            return str((self.host.config.get("settings") or {}).get("zapret_path") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _set_path(self, path: str) -> None:
        if self._standalone or self.host is None:
            self._path_manual = path
            return
        try:
            self.host.config.setdefault("settings", {})["zapret_path"] = path
            from main import save_config

            save_config(self.host.config)
        except Exception:  # noqa: BLE001
            pass

    def _save_strategy(self, strategy_id: str) -> None:
        """Запомнить выбранную стратегию. Файл пишем только при изменении:
        выбор в списке вызывается и при загрузке вкладки, а открытие вкладки
        не должно переписывать presets.json."""
        if self._standalone or self.host is None:
            return
        try:
            settings = self.host.config.setdefault("settings", {})
            if settings.get("zapret_strategy") == strategy_id:
                return
            settings["zapret_strategy"] = strategy_id
            from main import save_config

            save_config(self.host.config)
        except Exception:  # noqa: BLE001
            pass

    def _remembered_strategy(self) -> str:
        if self._standalone or self.host is None:
            return ""
        try:
            return str((self.host.config.get("settings") or {}).get("zapret_strategy") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _ensure(self):
        if self.client is None:
            import gd_gpu

            self.client = gd_gpu.GpuClient()
        return self.client

    # -- снимок состояния ----------------------------------------------------

    def _snap_job(self):
        """Снимок в фоне. Штампуется поколением: если за время снятия состояние
        изменилось локально (тумблер/путь), результат устаревает и отбрасывается
        в :meth:`_on_snap` — иначе поздний снапшот откатил бы свежие галочки."""
        gen = self._gen
        snap = gd_zapret.snapshot(self._path())
        snap["_gen"] = gen
        return snap

    def _on_snap(self, snap):
        if not isinstance(snap, dict):
            return
        if int(snap.get("_gen", -1)) < self._gen:
            return                     # снят до последней локальной записи — устарел
        self._snap = snap
        if snap.get("our_stale"):
            # наш winws завершился вне Gammadrilon — состояние устарело.
            # Уведомление одноразовое (gd_zapret.take_vanished): если о смерти уже
            # сказал тик главного окна, здесь остаётся только строка вкладки.
            state = gd_zapret.take_vanished()
            if state is not None:
                self._report_vanished(state)
            else:
                self._set_status(i18n.t("winws.exe завершился вне Gammadrilon."), ok=False)
            snap["our_stale"] = False
        if self.list.count() == 0:
            self._fill_strategies()
        self._render()

    def _set_status(self, text: str, ok: bool = True):
        self.status.setStyleSheet(f"color: {th.MUTED if ok else th.WARN};")
        self.status.setText(text)

    def _remembered_note(self, result) -> tuple[str, bool]:
        """Приписка к статусу: удалось ли запомнить запущенный процесс.

        Не запомнили — после перезапуска приложение не узнает свой winws и не
        сможет остановить его из окна: молчать об этом нельзя.
        """
        remembered = bool(result.get("remembered", True)) if isinstance(result, dict) else True
        if remembered:
            return "", True
        return " " + i18n.t("Внимание: приложение не смогло запомнить процесс — "
                            "после перезапуска остановить его из окна не получится."), False


    # -- отрисовка -----------------------------------------------------------

    def _render(self):
        snap = self._snap
        path = self._path()
        ours = snap.get("ours")
        foreign = [p for p in snap.get("foreign") or [] if p["pid"] not in self._ignored]

        # путь
        self.lbl_path.setText(path or i18n.t("не выбрана"))
        self.lbl_path.setToolTip(path or "")
        problems = snap.get("problems") or {}
        errors = problems.get("errors") or []
        warnings = problems.get("warnings") or []
        notes = []
        if errors:
            notes.append(i18n.t("Проблемы: {v0}", v0="; ".join(errors)))
        if warnings:
            notes.append(i18n.t("Предупреждение: {v0}", v0="; ".join(warnings)))
        self.lbl_path_note.setText("\n".join(notes))
        self.lbl_path_note.setStyleSheet(f"color: {th.WARN if errors else th.MUTED};")
        self.btn_open.setEnabled(bool(path) and os.path.isdir(path))
        self.btn_svc.setEnabled(bool(path)
                                and os.path.isfile(os.path.join(path, "service.bat")))

        # состояние
        import time as _time

        detail_parts = []
        if ours:
            name = ours.get("strategy") or "?"
            started = ours.get("started")
            up = _fmt_uptime(_time.time() - started) if started else "—"
            label = i18n.t("РАБОТАЕТ: {v0}", v0=name)
            if ours.get("external"):
                label = i18n.t("ПОД УПРАВЛЕНИЕМ: внешний winws.exe")
            self.lbl_state.setText(f"{label}  ·  PID {ours.get('pid')}  ·  {up}")
            self.lbl_state.setStyleSheet(f"color: {th.INK};")
        elif foreign:
            self.lbl_state.setText(
                i18n.t("ВНЕШНИЙ winws.exe (PID {v0})", v0=foreign[0]["pid"]))
            self.lbl_state.setStyleSheet(f"color: {th.WARN};")
        else:
            self.lbl_state.setText(i18n.t("ВЫКЛЮЧЕНО"))
            self.lbl_state.setStyleSheet(f"color: {th.MUTED};")

        if snap.get("service_zapret") == "RUNNING":
            detail_parts.append(i18n.t("служба zapret: RUNNING (внешняя, не управляется)"))
        if snap.get("service_windivert") == "RUNNING" and not ours:
            detail_parts.append(i18n.t(
                "драйвер WinDivert: RUNNING — им пользуется другая программа"))
        for other in snap.get("others") or []:
            detail_parts.append(i18n.t("посторонний обходчик: {v0} (PID {v1})",
                                       v0=other["name"], v1=other["pid"]))
        self.lbl_detail.setText("  ·  ".join(detail_parts))

        # конфликт с внешним winws: блок виден, пока не нажали «оставить как есть»;
        # но включение недоступно, пока ЛЮБОЙ чужой winws работает — WinDivert
        # одна инстанция на фильтр, второй экземпляр просто не заработает (ТЗ 8.4)
        any_foreign = bool(snap.get("foreign"))
        show_conflict = bool(foreign)
        self.lbl_conflict.setVisible(show_conflict)
        self.btn_adopt.setVisible(show_conflict)
        self.btn_ignore.setVisible(show_conflict)
        if show_conflict:
            self.lbl_conflict.setText(i18n.t(
                "Посторонний winws.exe (PID {v0}) — вероятно, запущен "
                "вручную. Gammadrilon молча его не трогает: его можно взять "
                "под управление (прочитаю аргументы и смогу останавливать) "
                "или оставить как есть.", v0=foreign[0]["pid"]))
            self.lbl_conflict.setStyleSheet(f"color: {th.WARN};")

        # тумблеры — из родных файлов zapret, без сигналов
        gf = snap.get("game_filter") or "off"
        ipset = snap.get("ipset") or {}
        for chk, val in ((self.chk_tcp, gf in ("tcp", "all")),
                         (self.chk_udp, gf in ("udp", "all")),
                         (self.chk_ipset, ipset.get("mode") == "loaded")):
            chk.blockSignals(True)
            chk.setChecked(bool(val))
            chk.blockSignals(False)
        mode_names = {
            "loaded": i18n.t("загружен"),
            "none": i18n.t("отключён (заглушка)"),
            "any": i18n.t("пуст — фильтруются любые IP"),
            "missing": i18n.t("нет файла"),
        }
        self.lbl_lists.setText(i18n.t(
            "ipset-all.txt: {v0} строк · {v1} · {v2}  ·  {v3}",
            v0=ipset.get("lines", 0), v1=_fmt_size(ipset.get("size", 0)),
            v2=_fmt_date(ipset.get("mtime")),
            v3=mode_names.get(ipset.get("mode"), "?")))

        # кнопки
        busy = gd_bg.is_running(self._worker)
        path_ok = bool(path) and not errors
        sel = self._selected_strategy()
        self.btn_on.setEnabled(path_ok and bool(sel) and not ours
                               and not any_foreign and not busy)
        self.btn_off.setEnabled(bool(ours) and not busy)
        self.btn_recon.setEnabled(path_ok and bool(sel) and not any_foreign and not busy)
        self.btn_adopt.setEnabled(not busy)
        self.btn_ignore.setEnabled(not busy)

        # журнал winws
        if ours:
            self.lbl_journal.setText(i18n.t("Обход: {v0} (PID {v1})",
                                            v0=ours.get("strategy") or "?",
                                            v1=ours.get("pid")))
        else:
            self.lbl_journal.setText(i18n.t("Обход не запущен — журнал пуст, пока не включите."))
        tail = gd_zapret.log_tail(200)
        text = "\n".join(tail)
        if text != self.txt_log.toPlainText():
            self.txt_log.setPlainText(text)
            self.txt_log.verticalScrollBar().setValue(
                self.txt_log.verticalScrollBar().maximum())


    # -- выбор стратегии -----------------------------------------------------

    def _fill_strategies(self):
        """Список стратегий: группы — заголовками, элементы — с id в данных."""
        self.list.blockSignals(True)
        self.list.clear()
        current_group = None
        for st in self._snap.get("strategies") or []:
            if st["group"] != current_group:
                current_group = st["group"]
                header = QListWidgetItem(i18n.t(current_group))
                header.setFlags(Qt.NoItemFlags)
                self.list.addItem(header)
            suffix = ""
            if gd_zapret.cache_get(self._path(), st["id"]) is not None:
                suffix = "  ·  " + i18n.t("освоена")
            item = QListWidgetItem(f"{st['name']}{suffix}")
            item.setData(Qt.UserRole, st["id"])
            item.setToolTip(st["file"])
            self.list.addItem(item)
        if not (self._snap.get("strategies") or []):
            # папка выбрана, а general*.bat в ней нет — говорим об этом прямо
            empty = QListWidgetItem(i18n.t("Стратегий не нашлось — в корне папки нет general*.bat"))
            empty.setFlags(Qt.NoItemFlags)
            self.list.addItem(empty)
        self.list.blockSignals(False)
        want = self._remembered_strategy() or self._selected_strategy()
        if want:
            for i in range(self.list.count()):
                it = self.list.item(i)
                if it.data(Qt.UserRole) == want:
                    self.list.setCurrentItem(it)
                    break
        self._on_sel()

    def _selected_strategy(self) -> dict | None:
        item = self.list.currentItem()
        if item is None:
            return None
        sid = item.data(Qt.UserRole)
        if not sid:
            return None
        for st in self._snap.get("strategies") or []:
            if st["id"] == sid:
                return st
        return None

    def _on_sel(self):
        st = self._selected_strategy()
        if st is None:
            self.lbl_sel.setText(i18n.t("Стратегия не выбрана."))
            return
        self._save_strategy(st["id"])
        cached = gd_zapret.cache_get(self._path(), st["id"]) is not None
        self.lbl_sel.setText(i18n.t(
            "выбрано: {v0}  ·  файл: {v1}{v2}",
            v0=st["name"], v1=st["file"],
            v2="" if cached else "  ·  " + i18n.t("освоение будет выполнено при включении")))
        self._render()


    # -- действия ------------------------------------------------------------

    def _pick_path(self):
        start = self._path() or gd_zapret.guess_path() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(
            self, i18n.t("Папка zapret-discord-youtube"), start)
        if not path:
            return
        path = os.path.normpath(path)
        self._set_path(path)
        self._ignored.clear()
        self._gen += 1             # снапшоты старого пути устаревают
        self.list.clear()
        self._poll.trigger()
        problems = gd_zapret.validate(path)
        if problems.get("errors"):
            self._set_status(i18n.t("Проблемы: {v0}", v0="; ".join(problems["errors"])),
                             ok=False)
        elif problems.get("warnings"):
            self._set_status(i18n.t("Предупреждение: {v0}", v0="; ".join(problems["warnings"])),
                             ok=False)
        else:
            self._set_status(i18n.t("Папка принята: {v0}", v0=path))
        gd_journal.record("zapret", i18n.t("папка"), after=path,
                          why=i18n.t("выбор пользователя"))

    def _open_folder(self):
        path = self._path()
        if path and os.path.isdir(path):
            try:
                os.startfile(path)  # noqa: S606 — папку открывает сам пользователь
            except OSError as exc:
                self._set_status(str(exc), ok=False)

    def _svc_launch(self):
        """Видимый запуск service.bat (меню интерактивное — свой stdin не даём)."""
        path = self._path()
        bat = os.path.join(path, "service.bat")
        if not os.path.isfile(bat):
            self._set_status(i18n.t("service.bat не найден."), ok=False)
            return
        try:
            import subprocess

            subprocess.Popen(["cmd.exe", "/c", "start", "", bat], cwd=path)
        except OSError as exc:
            self._set_status(str(exc), ok=False)
            return
        self._set_status(i18n.t("service.bat открыт в своём окне."))

    # -- включение -----------------------------------------------------------

    def _on_click(self):
        if gd_bg.is_running(self._worker):
            return
        path = self._path()
        st = self._selected_strategy()
        if not path or gd_zapret.validate(path).get("errors"):
            self._set_status(i18n.t("Сначала выберите корректную папку zapret."), ok=False)
            return
        if not st:
            self._set_status(i18n.t("Выберите стратегию в списке."), ok=False)
            return
        argv = gd_zapret.cache_get(path, st["id"])
        self._set_status(i18n.t("Включаю: {v0}{v1}…", v0=st["name"],
                                v1="" if argv else " (" + i18n.t("сначала освоение") + ")"))
        self._worker = gd_bg.submit(
            lambda: self._job_start(path, st, argv), self._start_done, parent=self)
        self._render()

    def _job_start(self, path, st, argv):
        client = self._ensure()
        recon = False
        if argv is None:
            res = client.call({"v": 1, "cmd": "zapret_recon",
                               "path": path, "strategy": st["id"]}, timeout=60)
            if not res.get("ok"):
                return {"ok": False, "stage": i18n.t("освоение"), "error": res.get("error")}
            argv = res["data"]["argv"]
            gd_zapret.cache_put(path, st["id"], argv)
            recon = True
        res = client.call({"v": 1, "cmd": "zapret_start", "path": path, "argv": argv,
                           "log": gd_zapret.log_path(), "note": st["name"]}, timeout=40)
        if not res.get("ok"):
            gd_zapret.cache_drop(path, st["id"])     # «при падении запуска» (ТЗ 8.2, п.5)
            return {"ok": False, "stage": i18n.t("запуск"), "error": res.get("error")}
        pid = int(res["data"]["pid"])
        remembered = gd_zapret.save_our_state(path, st["id"], pid, argv)
        return {"ok": True, "pid": pid, "name": st["name"], "recon": recon,
                "remembered": remembered}

    def _start_done(self, result):
        if not isinstance(result, dict):
            self._set_status(i18n.t("Фоновая операция не вернула результат."), ok=False)
        elif result.get("ok"):
            gd_journal.record("zapret", result["name"], before=i18n.t("выключено"),
                              after=i18n.t("работает (PID {v0})", v0=result["pid"]),
                              why=i18n.t("включение из вкладки"))
            note, ok = self._remembered_note(result)
            self._set_status(i18n.t("Включено: {v0} (PID {v1}).",
                                    v0=result["name"], v1=result["pid"]) + note, ok=ok)
        else:
            self._set_status(i18n.t("{v0} не удалось: {v1}",
                                    v0=result.get("stage"), v1=result.get("error")), ok=False)
        self.list.clear()          # обновить отметки «освоена»
        self._poll.trigger()


    # -- выключение ----------------------------------------------------------

    def _off(self):
        if gd_bg.is_running(self._worker):
            return
        ours = gd_zapret.our_state()
        pid = (ours or {}).get("pid")
        if not pid:
            self._set_status(i18n.t("Нечего останавливать."))
            return
        self._set_status(i18n.t("Останавливаю winws.exe (PID {v0})…", v0=pid))
        self._worker = gd_bg.submit(lambda: self._job_stop(pid), self._off_done, parent=self)
        self._render()

    def _job_stop(self, pid):
        res = self._ensure().call({"v": 1, "cmd": "zapret_stop", "pid": pid}, timeout=30)
        if res.get("ok"):
            gd_zapret.clear_our_state()
        return res

    def _off_done(self, res):
        if isinstance(res, dict) and res.get("ok"):
            gd_journal.record("zapret", i18n.t("обход"), before=i18n.t("работает"),
                              after=i18n.t("выключено"), why=i18n.t("остановка из вкладки"))
            self._set_status(i18n.t("Обход остановлен."))
        else:
            err = (res or {}).get("error") if isinstance(res, dict) else str(res)
            self._set_status(i18n.t("Остановить не удалось: {v0}", v0=err), ok=False)
        self._poll.trigger()

    # -- переосвоение --------------------------------------------------------

    def _strategy_by_id(self, sid):
        for st in self._snap.get("strategies") or []:
            if st["id"] == sid:
                return st
        return None

    def _recon(self):
        """Переосвоить выбранную стратегию; если наш winws работает — перезапустить."""
        if gd_bg.is_running(self._worker):
            return
        path = self._path()
        st = self._selected_strategy()
        if not path or not st or gd_zapret.validate(path).get("errors"):
            self._set_status(i18n.t("Сначала выберите корректную папку и стратегию."), ok=False)
            return
        self._set_status(i18n.t("Переосваиваю {v0}…", v0=st["name"]))
        self._worker = gd_bg.submit(lambda: self._job_recon(path, st), self._recon_done, parent=self)
        self._render()


    def _job_recon(self, path, st):
        """Остановить наш winws (если был), поймать argv заново, поднять снова."""
        client = self._ensure()
        ours = gd_zapret.our_state()
        was_running = bool(ours and gd_zapret.pid_alive(ours.get("pid")))
        if was_running:
            res = client.call({"v": 1, "cmd": "zapret_stop", "pid": ours["pid"]}, timeout=30)
            if not res.get("ok"):
                return {"ok": False, "error": i18n.t("остановка: {v0}", v0=res.get("error"))}
            gd_zapret.clear_our_state()
        res = client.call({"v": 1, "cmd": "zapret_recon",
                           "path": path, "strategy": st["id"]}, timeout=60)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error")}
        argv = res["data"]["argv"]
        gd_zapret.cache_put(path, st["id"], argv)
        pid = None
        remembered = True
        if was_running:
            res2 = client.call({"v": 1, "cmd": "zapret_start", "path": path, "argv": argv,
                                "log": gd_zapret.log_path(), "note": st["name"]}, timeout=40)
            if not res2.get("ok"):
                return {"ok": False, "error": res2.get("error")}
            pid = int(res2["data"]["pid"])
            remembered = gd_zapret.save_our_state(path, st["id"], pid, argv)
        return {"ok": True, "name": st["name"], "pid": pid,
                "was": was_running, "argv": argv, "remembered": remembered}

    def _recon_done(self, result):
        if isinstance(result, dict) and result.get("ok"):
            note, ok = self._remembered_note(result)
            if result.get("pid"):
                self._set_status(i18n.t("Переосвоено и перезапущено: {v0} (PID {v1}).",
                                        v0=result["name"], v1=result["pid"]) + note, ok=ok)
            else:
                self._set_status(i18n.t("Переосвоено: {v0} (аргументов: {v1}).",
                                        v0=result["name"], v1=len(result.get("argv") or [])) + note, ok=ok)
            gd_journal.record("zapret", result["name"], before=i18n.t("освоение"),
                              after=i18n.t("argv обновлён ({v0} арг.)",
                                           v0=len(result.get("argv") or [])),
                              why=i18n.t("переосвоение"))
        else:
            err = (result or {}).get("error") if isinstance(result, dict) else str(result)
            self._set_status(i18n.t("Переосвоение не удалось: {v0}", v0=err), ok=False)
        self.list.clear()
        self._poll.trigger()

    # -- внешний winws: взять под управление / оставить как есть -------------

    def _adopt(self):
        if gd_bg.is_running(self._worker):
            return
        foreign = [p for p in (self._snap.get("foreign") or [])
                   if p["pid"] not in self._ignored]
        if not foreign:
            return
        pid = foreign[0]["pid"]
        self._set_status(i18n.t("Читаю аргументы winws.exe (PID {v0})…", v0=pid))
        self._worker = gd_bg.submit(lambda: self._job_adopt(pid), self._adopt_done, parent=self)
        self._render()

    def _job_adopt(self, pid):
        res = self._ensure().call({"v": 1, "cmd": "zapret_adopt", "pid": pid}, timeout=30)
        if res.get("ok"):
            argv = (res.get("data") or {}).get("argv") or []
            remembered = gd_zapret.save_our_state(self._path(), i18n.t("(внешний)"),
                                                  pid, argv, external=True)
            res["remembered"] = remembered
        return res

    def _adopt_done(self, res):
        if isinstance(res, dict) and res.get("ok"):
            gd_journal.record("zapret", i18n.t("внешний winws.exe"),
                              before=i18n.t("вне управления"),
                              after=i18n.t("под управлением"),
                              why=i18n.t("решение пользователя"))
            note, ok = self._remembered_note(res)
            self._set_status(i18n.t(
                "Внешний winws.exe взят под управление — его можно остановить "
                "кнопкой «Выключить».") + note, ok=ok)
        else:
            err = (res or {}).get("error") if isinstance(res, dict) else str(res)
            self._set_status(i18n.t("Не удалось взять под управление: {v0}", v0=err), ok=False)
        self._poll.trigger()

    def _ignore_foreign(self):
        for p in self._snap.get("foreign") or []:
            self._ignored.add(p["pid"])
        self._set_status(i18n.t("Оставил как есть — Gammadrilon не трогает этот процесс."))
        self._render()


    # -- тумблеры GameFilter / IPSet -----------------------------------------

    def _on_toggle_gf(self):
        """GameFilter живёт в файле, который батник читает при запуске.

        Смена тумблера при работающем winws обязана вызвать повторное освоение
        (argv содержит раскрытые диапазоны портов) — иначе тумблер соврёт о
        состоянии (ТЗ 8.3).
        """
        if gd_bg.is_running(self._worker):
            self._render()
            return
        path = self._path()
        tcp = self.chk_tcp.isChecked()
        udp = self.chk_udp.isChecked()
        before = self._snap.get("game_filter") or "off"
        ok, msg = gd_zapret.set_game_filter(path, tcp, udp)
        if not ok:
            self._set_status(i18n.t("GameFilter: {v0}", v0=msg), ok=False)
            self._poll.trigger()
            return
        gd_journal.record("zapret", "GameFilter", before=before, after=msg,
                          why=i18n.t("тумблер вкладки"))
        # снапшот мог быть снят до записи файла: без этого _render вернул бы
        # галочки к старым значениям, и следующий клик посчитал бы режим неверно
        self._snap["game_filter"] = msg
        self._gen += 1
        ours = self._snap.get("ours")
        if ours and not ours.get("external"):
            st = self._strategy_by_id(ours.get("strategy")) or self._selected_strategy()
            if st:
                self._set_status(i18n.t(
                    "GameFilter: {v0} — стратегия переосваивается, winws перезапускается…",
                    v0=msg))
                self._worker = gd_bg.submit(lambda: self._job_recon(path, st),
                                            self._recon_done, parent=self)
        else:
            self._set_status(i18n.t("GameFilter: {v0}.", v0=msg))
        self._poll.trigger()
        self._render()

    def _on_toggle_ipset(self):
        """IPSet — файл списка; winws читает его при старте (argv не меняется).

        Поэтому при работающем обходе достаточно перезапуска, без переосвоения.
        """
        if gd_bg.is_running(self._worker):
            self._render()
            return
        path = self._path()
        want = self.chk_ipset.isChecked()
        before = (self._snap.get("ipset") or {}).get("mode")
        ok, msg = gd_zapret.set_ipset(path, want)
        if not ok:
            self._set_status(i18n.t("IPSet: {v0}", v0=msg), ok=False)
            self._poll.trigger()
            return
        gd_journal.record("zapret", "IPSet", before=before, after=msg,
                          why=i18n.t("тумблер вкладки"))
        # то же, что у GameFilter: галочки обязаны отражать файл сразу
        self._snap["ipset"] = gd_zapret.ipset_info(path)
        self._gen += 1
        ours = self._snap.get("ours")
        if ours and not ours.get("external"):
            st = self._strategy_by_id(ours.get("strategy"))
            if st:
                self._set_status(i18n.t("IPSet: {v0} — winws перезапускается…", v0=msg))
                self._worker = gd_bg.submit(lambda: self._job_restart(path, st),
                                            self._restart_done, parent=self)
        else:
            self._set_status(i18n.t("IPSet: {v0}.", v0=msg))
        self._poll.trigger()
        self._render()

    def _job_restart(self, path, st):
        """Перезапуск winws без переосвоения: argv не менялся."""
        client = self._ensure()
        ours = gd_zapret.our_state() or {}
        argv = gd_zapret.cache_get(path, st["id"]) or list(ours.get("argv") or [])
        if not argv:
            return {"ok": False, "error": i18n.t(
                "нет освоенного argv — нажмите «Переосвоить»")}
        pid = ours.get("pid")
        if pid:
            res = client.call({"v": 1, "cmd": "zapret_stop", "pid": pid}, timeout=30)
            if not res.get("ok"):
                return {"ok": False, "error": i18n.t("остановка: {v0}", v0=res.get("error"))}
            gd_zapret.clear_our_state()
        res = client.call({"v": 1, "cmd": "zapret_start", "path": path, "argv": argv,
                           "log": gd_zapret.log_path(), "note": st["name"]}, timeout=40)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error")}
        new_pid = int(res["data"]["pid"])
        remembered = gd_zapret.save_our_state(path, st["id"], new_pid, argv)
        return {"ok": True, "pid": new_pid, "name": st["name"],
                "remembered": remembered}

    def _restart_done(self, result):
        if isinstance(result, dict) and result.get("ok"):
            note, ok = self._remembered_note(result)
            self._set_status(i18n.t("Перезапущено: {v0} (PID {v1}).",
                                    v0=result["name"], v1=result["pid"]) + note, ok=ok)
        else:
            err = (result or {}).get("error") if isinstance(result, dict) else str(result)
            self._set_status(i18n.t("Перезапуск не удался: {v0}", v0=err), ok=False)
        self._poll.trigger()

    # -- журнал --------------------------------------------------------------

    def _copy_log(self):
        QApplication.clipboard().setText(self.txt_log.toPlainText())
        self._set_status(i18n.t("Журнал скопирован в буфер обмена."))

