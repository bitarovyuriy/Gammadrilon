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
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import gd_i18n as i18n  # noqa: E402
import gd_journal  # noqa: E402
import gd_paths  # noqa: E402
import gd_theme as th
import gd_zapret  # noqa: E402
import gd_zapret_probe as probe  # noqa: E402
import gd_zapret_update as upd  # noqa: E402
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
        self._update = {"ok": False, "current": None, "latest": None, "newer": False,
                        "release": {}, "checked_at": None, "cached": False, "error": None}
        self._update_progress = ""
        self._gf_editing = False
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

        # --- обновления -----------------------------------------------------
        card_upd, lay_upd = make_card(
            i18n.t("ОБНОВЛЕНИЯ ZAPRET"),
            i18n.t("Последний релиз берётся с GitHub Flowseal. Перед "
                   "обновлением Gammadrilon сам останавливает обход, делает "
                   "резервную копию папки и сохраняет ваши списки и "
                   "стратегии *.user.bat."),
        )
        self.lbl_version = QLabel(i18n.t("—"))
        self.lbl_version.setObjectName("Value")
        lay_upd.addWidget(self.lbl_version)
        self.lbl_update_note = QLabel("")
        self.lbl_update_note.setObjectName("Hint")
        self.lbl_update_note.setWordWrap(True)
        lay_upd.addWidget(self.lbl_update_note)
        urow = QHBoxLayout()
        self.btn_upd_check = QPushButton(i18n.t("Проверить"))
        self.btn_upd_check.setObjectName("Segment")
        self.btn_upd_check.clicked.connect(self._update_check)
        urow.addWidget(self.btn_upd_check)
        self.btn_upd_apply = QPushButton(i18n.t("Обновить"))
        self.btn_upd_apply.setObjectName("PrimaryButton")
        self.btn_upd_apply.clicked.connect(self._update_apply)
        urow.addWidget(self.btn_upd_apply)
        self.btn_upd_rollback = QPushButton(i18n.t("Откатить"))
        self.btn_upd_rollback.setObjectName("Segment")
        self.btn_upd_rollback.clicked.connect(self._update_rollback)
        urow.addWidget(self.btn_upd_rollback)
        self.btn_upd_notes = QPushButton(i18n.t("Что нового"))
        self.btn_upd_notes.setObjectName("Segment")
        self.btn_upd_notes.clicked.connect(self._update_notes)
        urow.addWidget(self.btn_upd_notes)
        urow.addStretch(1)
        lay_upd.addLayout(urow)
        root.addWidget(card_upd)

        # --- служба ---------------------------------------------------------
        card_srv, lay_srv = make_card(
            i18n.t("СЛУЖБА ZAPRET"),
            i18n.t("Штатный автозапуск обхода без service.bat: служба Windows "
                   "поднимает winws.exe выбранной стратегией при входе в "
                   "систему. Управление требует прав администратора."),
        )
        self.lbl_service = QLabel(i18n.t("—"))
        self.lbl_service.setObjectName("Value")
        lay_srv.addWidget(self.lbl_service)
        self.lbl_service_note = QLabel("")
        self.lbl_service_note.setObjectName("Hint")
        self.lbl_service_note.setWordWrap(True)
        lay_srv.addWidget(self.lbl_service_note)
        srow = QHBoxLayout()
        self.btn_svc_install = QPushButton(i18n.t("Установить/обновить"))
        self.btn_svc_install.setObjectName("PrimaryButton")
        self.btn_svc_install.clicked.connect(lambda: self._service_action("install"))
        srow.addWidget(self.btn_svc_install)
        self.btn_svc_start = QPushButton(i18n.t("Запустить"))
        self.btn_svc_start.setObjectName("Segment")
        self.btn_svc_start.clicked.connect(lambda: self._service_action("start"))
        srow.addWidget(self.btn_svc_start)
        self.btn_svc_stop = QPushButton(i18n.t("Остановить"))
        self.btn_svc_stop.setObjectName("Segment")
        self.btn_svc_stop.clicked.connect(lambda: self._service_action("stop"))
        srow.addWidget(self.btn_svc_stop)
        self.btn_svc_restart = QPushButton(i18n.t("Перезапустить"))
        self.btn_svc_restart.setObjectName("Segment")
        self.btn_svc_restart.clicked.connect(lambda: self._service_action("restart"))
        srow.addWidget(self.btn_svc_restart)
        self.btn_svc_remove = QPushButton(i18n.t("Удалить"))
        self.btn_svc_remove.setObjectName("Segment")
        self.btn_svc_remove.clicked.connect(lambda: self._service_action("remove"))
        srow.addWidget(self.btn_svc_remove)
        srow.addStretch(1)
        lay_srv.addLayout(srow)
        root.addWidget(card_srv)

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
        self.btn_probe = QPushButton(i18n.t("Проверить доступ"))
        self.btn_probe.setObjectName("Segment")
        self.btn_probe.setToolTip(i18n.t(
            "Проверить Discord, YouTube, Google и Cloudflare через текущий обход. "
            "Ничего не меняет — только сеть."))
        self.btn_probe.clicked.connect(self._probe)
        brow.addWidget(self.btn_probe)
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

        portrow = QHBoxLayout()
        portrow.addWidget(QLabel(i18n.t("TCP-порты")))
        self.txt_gf_tcp = QLineEdit()
        self.txt_gf_tcp.setPlaceholderText(gd_zapret.GAME_FILTER_DEFAULT_TCP)
        self.txt_gf_tcp.setMaximumWidth(170)
        self.txt_gf_tcp.textEdited.connect(lambda *_: setattr(self, "_gf_editing", True))
        portrow.addWidget(self.txt_gf_tcp)
        portrow.addWidget(QLabel(i18n.t("UDP-порты")))
        self.txt_gf_udp = QLineEdit()
        self.txt_gf_udp.setPlaceholderText(gd_zapret.GAME_FILTER_DEFAULT_UDP)
        self.txt_gf_udp.setMaximumWidth(170)
        self.txt_gf_udp.textEdited.connect(lambda *_: setattr(self, "_gf_editing", True))
        portrow.addWidget(self.txt_gf_udp)
        self.btn_gf_apply = QPushButton(i18n.t("Применить порты"))
        self.btn_gf_apply.setObjectName("Segment")
        self.btn_gf_apply.clicked.connect(self._apply_gf_ports)
        portrow.addWidget(self.btn_gf_apply)
        portrow.addStretch(1)
        lay4.addLayout(portrow)

        self.lbl_lists = QLabel("")
        self.lbl_lists.setObjectName("Hint")
        self.lbl_lists.setWordWrap(True)
        lay4.addWidget(self.lbl_lists)

        lrow = QHBoxLayout()
        self.btn_ipset_update = QPushButton(i18n.t("Обновить IPSet"))
        self.btn_ipset_update.setObjectName("Segment")
        self.btn_ipset_update.clicked.connect(self._ipset_update)
        lrow.addWidget(self.btn_ipset_update)
        self.btn_hosts_check = QPushButton(i18n.t("Проверить hosts"))
        self.btn_hosts_check.setObjectName("Segment")
        self.btn_hosts_check.clicked.connect(self._hosts_check)
        lrow.addWidget(self.btn_hosts_check)
        self.btn_hosts_apply = QPushButton(i18n.t("Обновить hosts"))
        self.btn_hosts_apply.setObjectName("Segment")
        self.btn_hosts_apply.clicked.connect(self._hosts_apply)
        lrow.addWidget(self.btn_hosts_apply)
        lrow.addStretch(1)
        lay4.addLayout(lrow)
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
        self._maybe_check_update()

    def deactivate(self):
        self._poll.stop()

    # -- обновления, служба, списки: автоматизация вместо service.bat --------

    def _settings(self) -> dict:
        if self._standalone or self.host is None:
            return {}
        try:
            return self.host.config.setdefault("settings", {})
        except Exception:  # noqa: BLE001
            return {}

    def _get_setting(self, key: str, default=None):
        settings = self._settings()
        return settings.get(key, default)

    def _set_setting(self, key: str, value) -> None:
        settings = self._settings()
        if settings.get(key) == value:
            return
        settings[key] = value
        if not self._standalone and self.host is not None:
            try:
                from main import save_config

                save_config(self.host.config)
            except Exception:  # noqa: BLE001
                pass

    def _maybe_check_update(self) -> None:
        """Тихая проверка обновлений при входе на вкладку — не чаще суток."""
        if not self._get_setting("zapret_auto_update_check", True):
            return
        if self._standalone or self.host is None:
            return
        if gd_bg.is_running(self._worker):
            return
        last = self._get_setting("zapret_update_last_check", 0) or 0
        import time

        if time.time() - float(last) < 24 * 3600 and self._update.get("latest"):
            return
        self._update_check(force=False)

    def _update_check(self, force: bool = True) -> None:
        if gd_bg.is_running(self._worker):
            return
        path = self._path()
        self._set_status(i18n.t("Проверяю обновления zapret…"))
        self._worker = gd_bg.submit(lambda: upd.check_updates(path, force=force),
                                    self._update_checked, parent=self)

    def _update_checked(self, result) -> None:
        if isinstance(result, dict):
            self._update = result
            if result.get("ok"):
                import time

                self._set_setting("zapret_update_last_check", int(time.time()))
                if result.get("newer"):
                    self._set_status(i18n.t("Доступна новая версия zapret {v0}.",
                                            v0=result.get("latest")))
                else:
                    self._set_status(i18n.t("Обновлений zapret нет: {v0}.",
                                            v0=result.get("current") or "?"))
            else:
                self._set_status(i18n.t("Проверка обновлений: {v0}",
                                        v0=result.get("error") or "?"), ok=False)
        self._render()

    def _update_notes(self) -> None:
        release = self._update.get("release") or {}
        body = release.get("body") or i18n.t("Описание релиза пустое.")
        url = release.get("url") or upd.REPO_URL
        QMessageBox.information(self, i18n.t("Что нового: {v0}",
                                             v0=release.get("tag") or "?"),
                                f"{body}\n\n{url}")

    def _update_apply(self) -> None:
        if gd_bg.is_running(self._worker):
            return
        path = self._path()
        path_ok = bool(path) and not gd_zapret.validate(path).get("errors")
        if not path_ok:
            self._set_status(i18n.t("Сначала выберите рабочую папку zapret."), ok=False)
            return
        if not self._update.get("newer"):
            self._update_check(force=True)
            self._set_status(i18n.t("Новых версий zapret нет."))
            return
        answer = QMessageBox.question(
            self, i18n.t("Обновить zapret"),
            i18n.t("Обход будет остановлен на время обновления, папка сохранится "
                   "в архив. Продолжить?"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._update_progress = i18n.t("подготовка")
        self._set_status(i18n.t("Обновляю zapret…"))
        self._worker = gd_bg.submit(self._job_update, self._update_done, parent=self)
        self._render()

    def _job_update(self) -> dict:
        path = self._path()
        snap = gd_zapret.snapshot(path)
        ours = snap.get("ours") or {}
        our_pid = ours.get("pid")
        ours_external = bool(ours.get("external"))
        svc = snap.get("service_zapret") == "RUNNING"
        foreign = [p for p in (snap.get("foreign") or []) if p.get("pid") != our_pid]
        if foreign and not svc:
            return {"ok": False, "error": i18n.t(
                "Обход запущен чужим winws.exe. Возьмите его под управление "
                "или остановите, иначе обновление не сможет заменить папку.")}
        old_argv = list(ours.get("argv") or [])
        strategy = (snap.get("service_strategy") or gd_zapret.service_registry_strategy()
                    or ours.get("strategy") or self._remembered_strategy())

        client = self._ensure()
        if svc:
            client.call({"cmd": "zapret_service", "op": "stop"}, timeout=90)
        elif our_pid and not ours_external:
            client.call({"cmd": "zapret_stop", "pid": our_pid}, timeout=40)

        self._update_progress = i18n.t("проверка релиза")
        info = upd.check_updates(path, force=True)
        if not info.get("ok"):
            return {"ok": False, "error": info.get("error") or i18n.t("нет данных о релизе")}
        tag = info.get("latest") or "?"
        url = upd.release_zip_url(info.get("release") or {})
        if not url:
            return {"ok": False, "error": i18n.t("в релизе нет zip-архива")}
        zip_path = gd_paths.log_dir() / f"zapret-{tag}.zip"
        self._update_progress = i18n.t("загрузка {v0}", v0=tag)

        def progress(done, total):
            if total:
                self._update_progress = i18n.t("загрузка {v0}: {v1:.0f}%",
                                               v0=tag, v1=done * 100.0 / total)

        ok, msg = upd.download(url, zip_path, on_progress=progress)
        if not ok:
            return {"ok": False, "error": msg}
        self._update_progress = i18n.t("распаковка и замена")

        res = client.call({"cmd": "zapret_update", "op": "apply", "path": path,
                           "zip": str(zip_path), "version": tag}, timeout=600)
        if not res.get("ok"):
            # обход останавливали мы — возвращаем его в строй старыми средствами
            if svc:
                client.call({"cmd": "zapret_service", "op": "start"}, timeout=90)
            elif our_pid and old_argv and not ours_external:
                client.call({"cmd": "zapret_start", "path": path, "argv": old_argv,
                             "log": gd_zapret.log_path(), "note": strategy}, timeout=60)
            return {"ok": False, "error": res.get("error") or i18n.t("обновление не применилось")}

        restart_error = None
        restarted = False
        self._update_progress = i18n.t("перезапуск обхода")
        if svc and strategy:
            recon = client.call({"cmd": "zapret_recon", "path": path,
                                 "strategy": strategy}, timeout=120)
            if recon.get("ok"):
                inst = client.call({"cmd": "zapret_service", "op": "install",
                                    "path": path, "strategy": strategy,
                                    "argv": recon["data"]["argv"]}, timeout=180)
                restarted = bool(inst.get("ok"))
                if not restarted:
                    restart_error = inst.get("error")
            else:
                restart_error = recon.get("error")
            if not restarted:
                client.call({"cmd": "zapret_service", "op": "start"}, timeout=90)
        elif our_pid and strategy and not ours_external:
            recon = client.call({"cmd": "zapret_recon", "path": path,
                                 "strategy": strategy}, timeout=120)
            if recon.get("ok"):
                argv = recon["data"]["argv"]
                gd_zapret.cache_put(path, strategy, argv)
                started = client.call({"cmd": "zapret_start", "path": path, "argv": argv,
                                       "log": gd_zapret.log_path(), "note": strategy},
                                      timeout=60)
                if started.get("ok"):
                    gd_zapret.save_our_state(path, strategy, int(started["data"]["pid"]), argv)
                    restarted = True
                else:
                    restart_error = started.get("error")
            else:
                restart_error = recon.get("error")
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": True, "version": tag, "data": res.get("data") or {},
                "restarted": restarted, "restart_error": restart_error,
                "was_service": svc, "was_ours": bool(our_pid and not ours_external)}

    def _update_done(self, result) -> None:
        self._update_progress = ""
        if isinstance(result, dict) and result.get("ok"):
            note = i18n.t("Zapret обновлён до {v0}.", v0=result.get("version") or "?")
            if result.get("restart_error"):
                note += " " + i18n.t("Обход не перезапустился: {v0}",
                                     v0=result.get("restart_error"))
            self._set_status(note, ok=not result.get("restart_error"))
            gd_journal.record("zapret", i18n.t("Обновление"),
                              before=i18n.t("версия до обновления"),
                              after=result.get("version") or "?",
                              why=i18n.t("релиз Flowseal"))
            self._update_check(force=True)
        else:
            err = (result or {}).get("error") if isinstance(result, dict) else str(result)
            self._set_status(i18n.t("Обновление zapret: {v0}", v0=err), ok=False)
        self._poll.trigger()

    def _update_rollback(self) -> None:
        if gd_bg.is_running(self._worker):
            return
        manifest = upd.load_manifest()
        if not manifest:
            self._set_status(i18n.t("Откатывать нечего: резервной копии нет."), ok=False)
            return
        answer = QMessageBox.question(
            self, i18n.t("Откатить обновление"),
            i18n.t("Вернуть папку zapret из архива {v0}?",
                   v0=manifest.get("old_version") or "?"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._set_status(i18n.t("Откатываю zapret…"))
        self._worker = gd_bg.submit(self._job_rollback, self._rollback_done, parent=self)

    def _job_rollback(self) -> dict:
        path = self._path()
        snap = gd_zapret.snapshot(path)
        svc = snap.get("service_zapret") == "RUNNING"
        ours = snap.get("ours") or {}
        client = self._ensure()
        if svc:
            client.call({"cmd": "zapret_service", "op": "stop"}, timeout=90)
        elif ours.get("pid"):
            client.call({"cmd": "zapret_stop", "pid": ours["pid"]}, timeout=40)
        res = client.call({"cmd": "zapret_update", "op": "rollback"}, timeout=300)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error")}
        if svc:
            client.call({"cmd": "zapret_service", "op": "start"}, timeout=90)
        elif ours.get("pid") and ours.get("strategy") and not ours.get("external"):
            strategy = str(ours["strategy"])
            recon = client.call({"cmd": "zapret_recon", "path": path,
                                 "strategy": strategy}, timeout=120)
            if recon.get("ok"):
                argv = recon["data"]["argv"]
                gd_zapret.cache_put(path, strategy, argv)
                started = client.call({"cmd": "zapret_start", "path": path, "argv": argv,
                                       "log": gd_zapret.log_path(),
                                       "note": strategy}, timeout=60)
                if started.get("ok"):
                    gd_zapret.save_our_state(path, strategy,
                                             int(started["data"]["pid"]), argv)
        return {"ok": True, "data": res.get("data") or {}}

    def _rollback_done(self, result) -> None:
        if isinstance(result, dict) and result.get("ok"):
            data = result.get("data") or {}
            self._set_status(i18n.t("Вернул версию {v0}.", v0=data.get("version") or "?"))
            gd_journal.record("zapret", i18n.t("Откат обновления"),
                              before=i18n.t("новая версия"), after=data.get("version") or "?",
                              why=i18n.t("ручной откат"))
        else:
            err = (result or {}).get("error") if isinstance(result, dict) else str(result)
            self._set_status(i18n.t("Откат: {v0}", v0=err), ok=False)
        self._poll.trigger()

    # -- служба --------------------------------------------------------------

    def _service_action(self, op: str) -> None:
        if gd_bg.is_running(self._worker):
            return
        if op == "remove":
            answer = QMessageBox.question(
                self, i18n.t("Удалить службу zapret"),
                i18n.t("Удалить службу и драйвер WinDivert? Обход перестанет "
                       "запускаться с Windows."),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self._set_status(i18n.t("Служба zapret: {v0}…", v0=op))
        self._worker = gd_bg.submit(lambda: self._job_service(op), self._service_done,
                                    parent=self)
        self._render()

    def _job_service(self, op: str) -> dict:
        client = self._ensure()
        path = self._path()
        strategy = ""
        argv = None
        if op == "install":
            st = self._selected_strategy()
            if not st:
                return {"ok": False, "error": i18n.t("выберите стратегию")}
            snap = gd_zapret.snapshot(path)
            ours_now = (snap.get("ours") or {}).get("pid")
            foreign = [p for p in (snap.get("foreign") or []) if p.get("pid") != ours_now]
            if foreign and snap.get("service_zapret") != "RUNNING":
                return {"ok": False, "error": i18n.t(
                    "Посторонний winws.exe (PID {v0}) — сначала остановите его "
                    "или возьмите под управление.", v0=foreign[0].get("pid"))}
            strategy = st["id"]
            argv = gd_zapret.cache_get(path, strategy)
            if argv is None:
                recon = client.call({"cmd": "zapret_recon", "path": path,
                                     "strategy": strategy}, timeout=120)
                if not recon.get("ok"):
                    return {"ok": False, "error": recon.get("error")}
                argv = recon["data"]["argv"]
                gd_zapret.cache_put(path, strategy, argv)
            ours = gd_zapret.our_state()
            if ours and gd_zapret.pid_alive(ours.get("pid")):
                client.call({"cmd": "zapret_stop", "pid": ours["pid"]}, timeout=40)
                gd_zapret.clear_our_state()
        res = client.call({"cmd": "zapret_service", "op": op, "path": path,
                           "strategy": strategy, "argv": argv or []}, timeout=180)
        return {"ok": bool(res.get("ok")), "error": res.get("error"), "op": op,
                "strategy": strategy, "data": res.get("data")}

    def _service_done(self, result) -> None:
        if isinstance(result, dict) and result.get("ok"):
            op = result.get("op")
            texts = {"install": i18n.t("Служба установлена и запущена."),
                     "start": i18n.t("Служба запущена."),
                     "stop": i18n.t("Служба остановлена."),
                     "restart": i18n.t("Служба перезапущена."),
                     "remove": i18n.t("Служба удалена.")}
            self._set_status(texts.get(op, i18n.t("Готово.")))
            gd_journal.record("zapret", i18n.t("Служба zapret"),
                              before="", after=op, why=i18n.t("кнопка вкладки"))
        else:
            err = (result or {}).get("error") if isinstance(result, dict) else str(result)
            self._set_status(i18n.t("Служба zapret: {v0}", v0=err), ok=False)
        self._poll.trigger()

    # -- списки и hosts ------------------------------------------------------

    def _ipset_update(self) -> None:
        if gd_bg.is_running(self._worker):
            return
        self._set_status(i18n.t("Обновляю ipset-all.txt…"))
        self._worker = gd_bg.submit(self._job_ipset, self._ipset_done, parent=self)

    def _job_ipset(self) -> dict:
        path = self._path()
        res = upd.update_ipset(path)
        if not res.get("ok"):
            return res
        snap = gd_zapret.snapshot(path)
        ours = snap.get("ours") or {}
        client = self._ensure()
        if snap.get("service_zapret") == "RUNNING":
            client.call({"cmd": "zapret_service", "op": "restart"}, timeout=120)
        elif ours.get("pid") and not ours.get("external"):
            st = self._strategy_by_id(ours.get("strategy")) or self._selected_strategy()
            if st:
                client.call({"cmd": "zapret_stop", "pid": ours["pid"]}, timeout=30)
                gd_zapret.clear_our_state()
                argv = gd_zapret.cache_get(path, st["id"]) or list(ours.get("argv") or [])
                started = client.call({"cmd": "zapret_start", "path": path, "argv": argv,
                                       "log": gd_zapret.log_path(),
                                       "note": st["name"]}, timeout=60)
                if started.get("ok"):
                    gd_zapret.save_our_state(path, st["id"], int(started["data"]["pid"]), argv)
        return res

    def _ipset_done(self, result) -> None:
        if isinstance(result, dict) and result.get("ok"):
            self._set_status(i18n.t("IPSet обновлён: {v0} строк.",
                                    v0=result.get("lines") or 0))
            gd_journal.record("zapret", "IPSet", before="", after=i18n.t("список обновлён"),
                              why=i18n.t("кнопка вкладки"))
        else:
            err = (result or {}).get("error") if isinstance(result, dict) else str(result)
            self._set_status(i18n.t("IPSet: {v0}", v0=err), ok=False)
        self._poll.trigger()

    def _hosts_check(self) -> None:
        if gd_bg.is_running(self._worker):
            return
        self._set_status(i18n.t("Скачиваю hosts Flowseal…"))
        self._worker = gd_bg.submit(self._job_hosts_check, self._hosts_checked, parent=self)

    def _job_hosts_check(self) -> dict:
        ok, res = upd.fetch_hosts()
        if not ok:
            return {"ok": False, "error": res}
        status = upd.hosts_status(res)
        status["ok"] = True
        status["downloaded"] = res
        return status

    def _hosts_checked(self, result) -> None:
        if not (isinstance(result, dict) and result.get("ok")):
            err = (result or {}).get("error") if isinstance(result, dict) else str(result)
            self._set_status(i18n.t("hosts: {v0}", v0=err), ok=False)
            return
        if result.get("needs_update"):
            self._set_status(i18n.t(
                "hosts требует обновления. Нажмите «Обновить hosts» — "
                "файл будет сохранён с резервной копией."), ok=False)
        else:
            self._set_status(i18n.t("hosts в актуальном состоянии."))
        self._render()

    def _hosts_apply(self) -> None:
        if gd_bg.is_running(self._worker):
            return
        answer = QMessageBox.question(
            self, i18n.t("Обновить hosts"),
            i18n.t("Добавить или заменить управляемый блок Gammadrilon в "
                   "системном hosts? Соседние строки не трогаем, копия файла "
                   "сохраняется."),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._set_status(i18n.t("Обновляю hosts…"))
        self._worker = gd_bg.submit(self._job_hosts_apply, self._hosts_applied, parent=self)

    def _job_hosts_apply(self) -> dict:
        res = self._ensure().call({"cmd": "zapret_update", "op": "hosts"}, timeout=120)
        return {"ok": bool(res.get("ok")), "error": res.get("error"),
                "data": res.get("data")}

    def _hosts_applied(self, result) -> None:
        if isinstance(result, dict) and result.get("ok"):
            data = result.get("data") or {}
            self._set_status(i18n.t("hosts обновлён. Резервная копия: {v0}",
                                    v0=data.get("backup") or "?"))
            gd_journal.record("zapret", "hosts", before="", after=i18n.t("блок обновлён"),
                              why=i18n.t("кнопка вкладки"))
        else:
            err = (result or {}).get("error") if isinstance(result, dict) else str(result)
            self._set_status(i18n.t("hosts: {v0}", v0=err), ok=False)
        self._poll.trigger()

    def _apply_gf_ports(self) -> None:
        self._gf_editing = False
        self._on_toggle_gf()

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

        # обновления
        u = self._update or {}
        current = u.get("current") or upd.installed_version(path) or i18n.t("не определена")
        latest = u.get("latest")
        if u.get("ok") and u.get("newer"):
            self.lbl_version.setText(i18n.t("Установлено: {v0} · доступна {v1}",
                                            v0=current, v1=latest or "?"))
        elif u.get("ok"):
            self.lbl_version.setText(i18n.t("Установлено: {v0} · это последняя версия",
                                            v0=current))
        else:
            self.lbl_version.setText(i18n.t("Установлено: {v0}", v0=current))
        update_note = []
        if self._update_progress:
            update_note.append(self._update_progress)
        if u.get("checked_at"):
            update_note.append(i18n.t("проверено {v0}", v0=_fmt_date(u["checked_at"])))
        if u.get("error"):
            update_note.append(i18n.t("проверка: {v0}", v0=u["error"]))
        manifest = upd.load_manifest()
        if manifest:
            update_note.append(i18n.t("доступен откат к {v0}",
                                      v0=manifest.get("old_version") or "?"))
        self.lbl_update_note.setText("  ·  ".join(update_note))

        # служба
        svc = {"installed": bool(snap.get("service_zapret") or snap.get("service_strategy")),
               "state": snap.get("service_zapret"), "strategy": snap.get("service_strategy")}
        svc_state = svc.get("state") or ""
        svc_names = {
            "RUNNING": i18n.t("работает"),
            "STOPPED": i18n.t("остановлена"),
            "START_PENDING": i18n.t("запускается"),
            "STOP_PENDING": i18n.t("останавливается"),
        }
        if svc.get("installed"):
            self.lbl_service.setText(i18n.t("Служба zapret: {v0}",
                                            v0=svc_names.get(svc_state, svc_state or "?")))
            note = []
            if svc.get("strategy"):
                note.append(i18n.t("стратегия: {v0}", v0=svc["strategy"]))
            note.append(i18n.t("автозапуск при входе в Windows"))
            self.lbl_service_note.setText("  ·  ".join(note))
        else:
            self.lbl_service.setText(i18n.t("Служба не установлена."))
            self.lbl_service_note.setText(i18n.t(
                "Установите службу, чтобы обход поднимался автоматически без "
                "окна консоли."))

        # состояние
        import time as _time

        detail_parts = []
        service_running = snap.get("service_zapret") == "RUNNING"
        service_pid = snap.get("service_pid")
        if ours:
            name = ours.get("strategy") or "?"
            started = ours.get("started")
            up = _fmt_uptime(_time.time() - started) if started else "—"
            label = i18n.t("РАБОТАЕТ: {v0}", v0=name)
            if ours.get("external"):
                label = i18n.t("ПОД УПРАВЛЕНИЕМ: внешний winws.exe")
            self.lbl_state.setText(f"{label}  ·  PID {ours.get('pid')}  ·  {up}")
            self.lbl_state.setStyleSheet(f"color: {th.INK};")
        elif service_running:
            self.lbl_state.setText(i18n.t("СЛУЖБА ZAPRET: работает  ·  PID {v0}",
                                          v0=service_pid or "?"))
            self.lbl_state.setStyleSheet(f"color: {th.INK};")
        elif foreign:
            self.lbl_state.setText(
                i18n.t("ВНЕШНИЙ winws.exe (PID {v0})", v0=foreign[0]["pid"]))
            self.lbl_state.setStyleSheet(f"color: {th.WARN};")
        else:
            self.lbl_state.setText(i18n.t("ВЫКЛЮЧЕНО"))
            self.lbl_state.setStyleSheet(f"color: {th.MUTED};")

        if service_running:
            detail_parts.append(i18n.t("служба zapret: RUNNING (автозапуск)"))
        if snap.get("service_windivert") == "RUNNING" and not ours and not service_running:
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
        gf_state = snap.get("game_filter_state") or {}
        ipset = snap.get("ipset") or {}
        for chk, val in ((self.chk_tcp, gf in ("tcp", "all")),
                         (self.chk_udp, gf in ("udp", "all")),
                         (self.chk_ipset, ipset.get("mode") == "loaded")):
            chk.blockSignals(True)
            chk.setChecked(bool(val))
            chk.blockSignals(False)
        if not self.txt_gf_tcp.hasFocus():
            self.txt_gf_tcp.setText(gf_state.get("tcp") or "")
        if not self.txt_gf_udp.hasFocus():
            self.txt_gf_udp.setText(gf_state.get("udp") or "")
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
                               and not any_foreign and not service_running and not busy)
        self.btn_off.setEnabled(bool(ours) and not busy)
        self.btn_recon.setEnabled(path_ok and bool(sel) and not any_foreign
                                  and not service_running and not busy)
        self.btn_probe.setEnabled(path_ok and not busy)
        self.btn_adopt.setEnabled(not busy)
        self.btn_ignore.setEnabled(not busy)
        self.btn_upd_check.setEnabled(bool(path) and not busy)
        self.btn_upd_apply.setEnabled(bool(path) and path_ok and bool(self._update.get("newer"))
                                      and not busy)
        self.btn_upd_rollback.setEnabled(bool(manifest) and not busy)
        self.btn_upd_notes.setEnabled(bool((self._update.get("release") or {}).get("body"))
                                      and not busy)
        self.btn_svc_install.setEnabled(path_ok and bool(sel) and not busy)
        for btn in (self.btn_svc_start, self.btn_svc_stop, self.btn_svc_restart,
                    self.btn_svc_remove):
            btn.setEnabled(bool(svc.get("installed")) and not busy)
        self.btn_ipset_update.setEnabled(bool(path) and path_ok and not busy)
        self.btn_hosts_check.setEnabled(not busy)
        self.btn_hosts_apply.setEnabled(not busy)
        self.btn_gf_apply.setEnabled(bool(path) and path_ok and not busy)

        # журнал winws
        if ours:
            self.lbl_journal.setText(i18n.t("Обход: {v0} (PID {v1})",
                                            v0=ours.get("strategy") or "?",
                                            v1=ours.get("pid")))
        elif service_running:
            self.lbl_journal.setText(i18n.t("Служба zapret: PID {v0}; вывод службы "
                                            "пишется в журнал winws, если включён.",
                                            v0=service_pid or "?"))
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

    # -- проверка доступности целей -----------------------------------------

    def _probe(self):
        """Короткий тест: отвечают ли Discord/YouTube/Google через текущий обход."""
        if gd_bg.is_running(self._worker):
            return
        path = self._path()
        if not path or not os.path.isdir(path):
            self._set_status(i18n.t("Сначала выберите папку zapret."), ok=False)
            return
        self._set_status(i18n.t("Проверяю доступ…"))
        self._worker = gd_bg.submit(lambda: probe.probe_all(path, timeout=6.0),
                                    self._probe_done, parent=self)
        self._render()

    def _probe_done(self, result) -> None:
        if not (isinstance(result, dict) and result.get("total")):
            err = (result or {}).get("error") if isinstance(result, dict) else str(result)
            self._set_status(i18n.t("Проверка доступа: {v0}", v0=err or "?"), ok=False)
            self._poll.trigger()
            return
        text = probe.describe(result)
        self._set_status(text, ok=bool(result.get("ok")))
        gd_journal.record("zapret", i18n.t("Проверка доступа"), before="",
                          after=text, why=i18n.t("кнопка вкладки"))
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
        mode = "all" if (tcp and udp) else ("tcp" if tcp else ("udp" if udp else "off"))
        before = self._snap.get("game_filter") or "off"
        ok, msg = gd_zapret.set_game_filter_mode(
            path, mode, self.txt_gf_tcp.text().strip(), self.txt_gf_udp.text().strip())
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
        if self._snap.get("service_zapret") == "RUNNING" and not self._snap.get("ours"):
            self._set_status(i18n.t("GameFilter: {v0} — перезапускаю службу…", v0=msg))
            self._worker = gd_bg.submit(lambda: self._job_service("restart"),
                                        self._service_done, parent=self)
            self._poll.trigger()
            self._render()
            return
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

