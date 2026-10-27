# -*- coding: utf-8 -*-
"""Главный экран: сетка настраиваемых плиток поверх подсистем Gammadrilon.

Экран — витрина приложения: десять плиток показывают то, что чаще всего нужно
увидеть сразу (загрузка, лимиты, питание, DNS, обход DPI), и по клику уводят в
полную вкладку. Своей логики у плитки нет: цифры берутся из общего снимка
`gd_metrics`, действия — у существующих экранов (`host._ensure_page(...)`).

Раскладку хранит `gd_dashboard` (строки по 12 колонок, высоты S/M/L); здесь
только отрисовка, режим «Правка» и запись изменений на диск.
"""
from __future__ import annotations

from typing import Any

from PyQt5.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import gd_dashboard as model
import gd_i18n as i18n
import gd_metrics
import gd_theme as th

#: куда ведёт плитка: полный экран подсистемы
TAB_FOR = {
    "w.cpu": "tab.cpu",
    "w.gpu": "tab.gpu",
    "w.ram": "tab.utils",
    "w.power": "tab.cpu",
    "w.gpu_limit": "tab.gpu",
    "w.gamma": "tab.gamma",
    "w.dns": "tab.dns",
    "w.zapret": "tab.zapret",
    "w.rules": "tab.rules",
    "w.journal": "tab.utils",
}

TILE_CLASSES: dict[str, type] = {}


def _dash() -> str:
    """Прочерк для «данных нет» — один на весь экран."""
    return "—"


def _repolish(widget: QWidget) -> None:
    """Перечитать QSS после смены свойства (иначе [edit="true"] не применится)."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _chips(
    items: list[tuple[str, str, bool]],
    on_pick,
) -> tuple[QHBoxLayout, dict[str, QPushButton]]:
    """Ряд сегментов-кнопок: (ключ, подпись, выбрана) → (layout, кнопки)."""
    row = QHBoxLayout()
    row.setSpacing(th.SPACE_XS)
    row.setContentsMargins(0, 0, 0, 0)
    buttons: dict[str, QPushButton] = {}
    for key, label, checked in items:
        button = QPushButton(label)
        button.setObjectName("Segment")
        button.setCheckable(True)
        button.setChecked(checked)
        button.setFixedHeight(24)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.clicked.connect(lambda _=False, k=key: on_pick(k))
        row.addWidget(button, 1)
        buttons[key] = button
    return row, buttons


class RowWidget(QWidget):
    """Строка сетки: раскладывает плитки по 12 колонкам вручную.

    `QHBoxLayout` тянет плитки пропорционально stretch, но учитывает их
    minimumSizeHint: плитка с кнопкой внутри оказывается шире соседки с тем же
    весом, и сетка перестаёт быть сеткой. Ручная раскладка держит колонки честно.
    """

    def __init__(self, items, height: int, parent=None):
        super().__init__(parent)
        self.setObjectName("Root")
        self.items: list[list] = [[tile, int(cols)] for tile, cols in items]
        self.setFixedHeight(height)
        # плитка живёт в строке: геометрию ей задаёт раскладка строки, а не экран
        for tile, _cols in self.items:
            tile.setParent(self)
            tile.show()

    def set_widths(self, widths: list[int]) -> None:
        """Обновить ширины колонок без пересборки строки."""
        for index, width in enumerate(widths):
            if index < len(self.items):
                self.items[index][1] = int(width)
        self._layout_tiles()

    def resizeEvent(self, event):  # noqa: N802 — имя из Qt
        super().resizeEvent(event)
        self._layout_tiles()

    def _layout_tiles(self) -> None:
        if not self.items:
            return
        gap = th.PAGE_SPACING
        count = len(self.items)
        usable = max(self.width() - gap * (count - 1), 0)
        total = sum(max(int(cols), 1) for _tile, cols in self.items) or 1
        widths = [int(round(usable * max(int(cols), 1) / total)) for _tile, cols in self.items]
        widths[-1] += usable - sum(widths)      # остаток округления — последней
        x = 0
        for (tile, _cols), width in zip(self.items, widths):
            width = max(width, 1)
            tile.setGeometry(x, 0, width, self.height())
            x += width + gap


class ClickableLabel(QLabel):
    """Заголовок плитки: клик открывает полный раздел."""

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):  # noqa: N802 — имя из Qt
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ResizeHandle(QFrame):
    """Ручка у правого края плитки: тянет ширину, соседи отдают колонки."""

    def __init__(self, tile: "Tile"):
        super().__init__(tile)
        self.tile = tile
        self.setObjectName("TileResize")
        self.setCursor(Qt.SizeHorCursor)
        self.setToolTip(i18n.t("home.edit_hint"))
        self._start_x: int | None = None

    def mousePressEvent(self, event):  # noqa: N802 — имя из Qt
        if event.button() == Qt.LeftButton:
            self._start_x = event.globalPos().x()
            self.tile.board.begin_resize(self.tile)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 — имя из Qt
        if self._start_x is not None and event.buttons() & Qt.LeftButton:
            self.tile.board.resize_preview(self.tile, event.globalPos().x() - self._start_x)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 — имя из Qt
        if self._start_x is not None:
            self._start_x = None
            self.tile.board.end_resize()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class Tile(QFrame):
    """Плитка главного экрана: заголовок, значение, при нужде — управление."""

    #: имя класса в каталоге (заполняется декоратором ниже)
    tile_id = ""

    def __init__(self, tile_id: str, board: "DashboardWidget"):
        super().__init__(board)
        self.tile_id = tile_id
        self.board = board
        self.host = board.host
        self._editing = False
        self._press: QPoint | None = None

        self.setObjectName("Card")
        self.setMinimumWidth(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(th.SPACE_M, th.SPACE_M, th.SPACE_M, th.SPACE_M)
        outer.setSpacing(th.SPACE_XS)

        head = QHBoxLayout()
        head.setSpacing(th.SPACE_S)
        head.setContentsMargins(0, 0, 0, 0)
        self.title = ClickableLabel(i18n.t(tile_id))
        self.title.setObjectName("CardLabel")
        self.title.setCursor(Qt.PointingHandCursor)
        self.title.clicked.connect(self._activate)
        self.state = QLabel("")
        self.state.setObjectName("Hint")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.state)
        outer.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setSpacing(th.SPACE_XS)
        self.body.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.body, 1)

        self.build()

        self.handle = ResizeHandle(self)
        self.handle.setFixedWidth(5)
        self.handle.hide()

    # -- каркас ------------------------------------------------------------
    def build(self) -> None:
        """Содержимое плитки — у каждого виджета своё."""

    def update_metrics(self, snap: dict[str, Any]) -> None:
        """Живые данные из общего снимка метрик (раз в секунду)."""

    def refresh_slow(self) -> None:
        """Медленный источник: служба, файлы правил, журнал (раз в 5 секунд)."""

    # -- режим правки ------------------------------------------------------
    def set_edit(self, editing: bool) -> None:
        self._editing = editing
        self.setProperty("edit", "true" if editing else "false")
        _repolish(self)
        for button in self.findChildren(QPushButton):
            button.setEnabled(not editing)
        self.handle.setVisible(editing)
        self.setCursor(Qt.OpenHandCursor if editing else Qt.ArrowCursor)

    def resizeEvent(self, event):  # noqa: N802 — имя из Qt
        super().resizeEvent(event)
        self.handle.setGeometry(self.width() - 8, 6, 5, max(self.height() - 12, 10))

    def mousePressEvent(self, event):  # noqa: N802 — имя из Qt
        if self._editing and event.button() == Qt.LeftButton:
            self._press = event.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 — имя из Qt
        if self._editing and self._press is not None and event.buttons() & Qt.LeftButton:
            if (event.pos() - self._press).manhattanLength() > 6:
                self.board.drag_move(self, event.globalPos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 — имя из Qt
        if self._editing and self._press is not None:
            self._press = None
            self.board.drag_drop()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -- действия ----------------------------------------------------------
    def _activate(self) -> None:
        if self._editing:
            return
        key = TAB_FOR.get(self.tile_id)
        if key:
            self.board.open_tab(key)

    def _menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        key = TAB_FOR.get(self.tile_id)
        if key and not self._editing:
            menu.addAction(i18n.t("home.open"), lambda: self.board.open_tab(key))
        row_index = self.board.row_of(self.tile_id)
        if row_index is not None:
            height_menu = menu.addMenu(i18n.t("home.height"))
            current = self.board.row_height(row_index)
            for code in ("S", "M", "L"):
                action = height_menu.addAction(i18n.t(f"home.h_{code}"))
                action.setCheckable(True)
                action.setChecked(current == code)
                action.triggered.connect(
                    lambda _=False, r=row_index, c=code: self.board.set_row_height(r, c)
                )
        menu.addSeparator()
        menu.addAction(i18n.t("home.remove"), lambda: self.board.remove_tile(self.tile_id))
        menu.exec_(self.mapToGlobal(pos))


def _tile(tile_id: str):
    """Регистрация класса плитки в каталоге отрисовки."""

    def wrap(cls):
        TILE_CLASSES[tile_id] = cls
        cls.tile_id = tile_id
        return cls

    return wrap


@_tile("w.cpu")
class CpuTile(Tile):
    """Загрузка и частота процессора."""

    def build(self) -> None:
        self.value = QLabel(_dash())
        self.value.setObjectName("Value")
        self.line = QLabel(i18n.t("w.cpu.hint"))
        self.line.setObjectName("Hint")
        self.body.addWidget(self.value)
        self.body.addStretch(1)
        self.body.addWidget(self.line)

    def update_metrics(self, snap: dict[str, Any]) -> None:
        cpu = snap.get("cpu") or {}
        load = cpu.get("load_pct")
        self.value.setText(i18n.t("{v0:.0f} %", v0=load) if load is not None else _dash())
        parts = []
        if cpu.get("mhz"):
            parts.append(i18n.t("{v0:.2f} ГГц", v0=cpu["mhz"] / 1000))
        if cpu.get("temp_c"):
            parts.append(i18n.t("w.gpu.temp", v0=cpu["temp_c"]))
        self.line.setText(" · ".join(parts) or i18n.t("home.no_data"))


@_tile("w.gpu")
class GpuTile(Tile):
    """Загрузка, температура и лимит видеокарты."""

    def build(self) -> None:
        self.value = QLabel(_dash())
        self.value.setObjectName("Value")
        self.line = QLabel(i18n.t("w.gpu.hint"))
        self.line.setObjectName("Hint")
        self.body.addWidget(self.value)
        self.body.addStretch(1)
        self.body.addWidget(self.line)

    def update_metrics(self, snap: dict[str, Any]) -> None:
        gpu = snap.get("gpu")
        if not gpu:
            self.value.setText(_dash())
            self.line.setText(i18n.t("home.no_data"))
            return
        util = gpu.get("util")
        self.value.setText(i18n.t("{v0:.0f} %", v0=util) if util is not None else _dash())
        parts = []
        if gpu.get("temp") is not None:
            parts.append(i18n.t("w.gpu.temp", v0=gpu["temp"]))
        if gpu.get("pl") is not None:
            parts.append(i18n.t("w.gpu.limit", v0=gpu["pl"]))
        self.line.setText(" · ".join(parts) or i18n.t("home.no_data"))


@_tile("w.ram")
class RamTile(Tile):
    """Занятая память и переход к очистке."""

    def build(self) -> None:
        self.value = QLabel(_dash())
        self.value.setObjectName("Value")
        self.line = QLabel(i18n.t("w.ram.hint"))
        self.line.setObjectName("Hint")
        self.body.addWidget(self.value)
        self.body.addStretch(1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(th.SPACE_XS)
        row.addWidget(self.line, 1)
        self.clean = QPushButton(i18n.t("w.ram.clean"))
        self.clean.setObjectName("SmallButton")
        self.clean.setFixedHeight(24)
        self.clean.clicked.connect(lambda: self.board.open_tab("tab.utils"))
        row.addWidget(self.clean)
        self.body.addLayout(row)

    def update_metrics(self, snap: dict[str, Any]) -> None:
        percent = snap.get("ram")
        if percent is None:
            self.value.setText(_dash())
            self.line.setText(i18n.t("home.no_data"))
            return
        self.value.setText(i18n.t("{v0:.0f} %", v0=percent))
        try:
            import psutil

            mem = psutil.virtual_memory()
            self.line.setText(
                i18n.t("w.ram.used", v0=mem.used / 1024**3, v1=mem.total / 1024**3)
            )
        except Exception:  # noqa: BLE001 — без psutil показываем только процент
            self.line.setText(i18n.t("w.ram.hint"))


@_tile("w.power")
class PowerTile(Tile):
    """Профиль питания CPU: ЭКО / НОРМА / БУСТ."""

    PROFILES = (("eco", "ЭКО"), ("normal", "НОРМА"), ("boost", "БУСТ"))

    def build(self) -> None:
        self.value = QLabel(i18n.t("w.power.none"))
        self.value.setObjectName("Value")
        self.body.addWidget(self.value)
        self.body.addStretch(1)
        row, self.chips = _chips(
            [(pid, i18n.t(name), False) for pid, name in self.PROFILES], self._pick
        )
        self.body.addLayout(row)

    def _pick(self, profile: str) -> None:
        page = self.board.ensure_page("tab.cpu")
        if page is None:
            return
        page.apply_profile(profile)
        QTimer.singleShot(1500, self.refresh_slow)

    def refresh_slow(self) -> None:
        try:
            import gd_power

            snap = gd_power.state_snapshot()
        except Exception:  # noqa: BLE001 — powercfg не ответил
            self.value.setText(_dash())
            return
        profile = snap.get("profile")
        if profile:
            self.value.setText(i18n.t(str(profile).upper()))
            self.state.setText(i18n.t("w.power.hint"))
        else:
            self.value.setText(i18n.t("w.power.none"))
            self.state.setText("")
        for pid, button in self.chips.items():
            button.setChecked(pid == profile)


@_tile("w.gpu_limit")
class GpuLimitTile(Tile):
    """Лимит мощности GPU и быстрые профили."""

    PROFILES = (("quiet", "Тихий"), ("default", "Дефолт"), ("game", "Игровой"), ("max", "Макс"))

    def build(self) -> None:
        self.value = QLabel(_dash())
        self.value.setObjectName("Value")
        self.line = QLabel(i18n.t("w.gpu_limit.hint"))
        self.line.setObjectName("Hint")
        self.body.addWidget(self.value)
        self.body.addWidget(self.line)
        self.body.addStretch(1)
        row, self.chips = _chips(
            [(name, i18n.t(label), False) for name, label in self.PROFILES], self._pick
        )
        self.body.addLayout(row)

    def _pick(self, profile: str) -> None:
        page = self.board.ensure_page("tab.gpu")
        if page is not None:
            page.apply_profile(profile)

    def update_metrics(self, snap: dict[str, Any]) -> None:
        gpu = snap.get("gpu")
        limit = (gpu or {}).get("pl")
        self.value.setText(
            i18n.t("{v0:.0f} Вт", v0=limit) if limit is not None else i18n.t("home.no_data")
        )
        if gpu and gpu.get("temp") is not None:
            self.line.setText(i18n.t("w.gpu.temp", v0=gpu["temp"]))
        else:
            self.line.setText(i18n.t("w.gpu_limit.hint"))


@_tile("w.gamma")
class GammaTile(Tile):
    """Пресеты гаммы: один клик — и экран как надо."""

    def build(self) -> None:
        self.value = QLabel(_dash())
        self.value.setObjectName("Value")
        self.body.addWidget(self.value)
        self.body.addStretch(1)
        presets = self._presets()
        self._index: dict[str, int] = {}
        items = []
        for index, preset in enumerate(presets):
            key = str(index)
            self._index[key] = index
            items.append((key, str(preset.get("name") or "?"), False))
        if items:
            row, self.chips = _chips(items, self._pick)
            self.body.addLayout(row)
        else:
            self.chips = {}

    def _presets(self) -> list[dict]:
        host = self.host
        config = getattr(host, "config", None) or {}
        presets = config.get("presets") or []
        return [p for p in presets if isinstance(p, dict)]

    def _pick(self, key: str) -> None:
        index = self._index.get(key)
        host = self.host
        if index is None or host is None:
            return
        host.apply_preset_by_index(index)
        self._sync_checked(index)

    def _sync_checked(self, current: int) -> None:
        for key, button in self.chips.items():
            button.setChecked(self._index.get(key) == current)

    def update_metrics(self, snap: dict[str, Any]) -> None:
        host = self.host
        config = getattr(host, "config", None) or {}
        current = (config.get("settings") or {}).get("current") or {}
        brightness = current.get("brightness")
        if brightness is None:
            self.value.setText(_dash())
        else:
            self.value.setText(i18n.t("w.gamma.current", v0=float(brightness)))
        index = getattr(host, "current_preset_index", 0)
        self._sync_checked(index)


@_tile("w.dns")
class DnsTile(Tile):
    """Текущий резолвер, замер и переход на вкладку DNS."""

    def build(self) -> None:
        self.value = QLabel(_dash())
        self.value.setObjectName("Value")
        self.line = QLabel(i18n.t("w.dns.hint"))
        self.line.setObjectName("Hint")
        self.body.addWidget(self.value)
        self.body.addStretch(1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(th.SPACE_XS)
        row.addWidget(self.line, 1)
        self.probe = QPushButton(i18n.t("w.dns.measure"))
        self.probe.setObjectName("SmallButton")
        self.probe.setFixedHeight(24)
        self.probe.clicked.connect(self._measure)
        row.addWidget(self.probe)
        self.body.addLayout(row)

    def _measure(self) -> None:
        gd_metrics.request_dns()

    def update_metrics(self, snap: dict[str, Any]) -> None:
        dns = snap.get("dns")
        if not dns:
            self.value.setText(i18n.t("w.dns.none"))
            return
        servers = dns.get("servers") or []
        if isinstance(servers, str):
            servers = [servers]
        self.value.setText(", ".join(str(s) for s in servers) or _dash())
        mode = dns.get("mode")
        age = gd_metrics.dns_age()
        parts = [str(mode)] if mode else []
        if age is not None:
            parts.append(i18n.t("{v0:.0f} с", v0=age))
        self.line.setText(" · ".join(parts) or i18n.t("w.dns.hint"))


@_tile("w.zapret")
class ZapretTile(Tile):
    """Служба обхода DPI: работает или нет."""

    def build(self) -> None:
        self.value = QLabel(_dash())
        self.value.setObjectName("Value")
        self.line = QLabel(i18n.t("w.zapret.hint"))
        self.line.setObjectName("Hint")
        self.body.addWidget(self.value)
        self.body.addStretch(1)
        self.body.addWidget(self.line)

    def refresh_slow(self) -> None:
        try:
            import gd_zapret

            status = gd_zapret.service_status()
            procs = gd_zapret.processes()
        except Exception:  # noqa: BLE001 — sc query не ответил
            self.value.setText(_dash())
            return
        running = status.get("state") == "running" or bool(procs)
        self.value.setText(i18n.t("w.zapret.on") if running else i18n.t("w.zapret.off"))
        self.line.setText(i18n.t("w.zapret.processes", v0=len(procs)))


@_tile("w.rules")
class RulesTile(Tile):
    """Сколько правил заведено и когда последнее срабатывало."""

    def build(self) -> None:
        self.value = QLabel(_dash())
        self.value.setObjectName("Value")
        self.line = QLabel(i18n.t("w.rules.hint"))
        self.line.setObjectName("Hint")
        self.body.addWidget(self.value)
        self.body.addStretch(1)
        self.body.addWidget(self.line)

    def refresh_slow(self) -> None:
        try:
            import gd_rules

            rules = gd_rules.load_rules()
            fires = gd_rules.recent_fires(1)
        except Exception:  # noqa: BLE001 — файл правил не прочитан
            self.value.setText(_dash())
            return
        if not rules:
            self.value.setText(i18n.t("w.rules.empty"))
            self.line.setText("")
            return
        self.value.setText(str(len(rules)))
        stamp = str((fires[0] or {}).get("ts", "")) if fires else ""
        self.line.setText(
            i18n.t("w.rules.fires", v0=stamp.replace("T", " ")[:16]) if stamp else i18n.t("w.rules.hint")
        )


@_tile("w.journal")
class JournalTile(Tile):
    """Последние записи журнала изменений."""

    def build(self) -> None:
        self.value = QLabel(i18n.t("w.journal.empty"))
        self.value.setObjectName("Hint")
        self.value.setWordWrap(True)
        self.body.addWidget(self.value)
        self.body.addStretch(1)

    def refresh_slow(self) -> None:
        try:
            import gd_journal

            entries = gd_journal.recent(3)
        except Exception:  # noqa: BLE001 — журнал не прочитан
            self.value.setText(i18n.t("home.no_data"))
            return
        if not entries:
            self.value.setText(i18n.t("w.journal.empty"))
            return
        self.value.setText("\n".join(gd_journal.human(entry) for entry in entries))


class CatalogDialog(QDialog):
    """Каталог виджетов: галочками выбирается, что стоит на главном экране."""

    def __init__(self, board: "DashboardWidget"):
        super().__init__(board)
        self.board = board
        self.setWindowTitle(i18n.t("home.catalog_title"))
        self.setMinimumWidth(360)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(th.SPACE_L, th.SPACE_L, th.SPACE_L, th.SPACE_L)
        outer.setSpacing(th.SPACE_S)

        title = QLabel(i18n.t("home.catalog_title"))
        title.setObjectName("DialogTitle")
        hint = QLabel(i18n.t("home.catalog_hint"))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(hint)

        current = set(model.tile_ids(board.layout_model))
        self.boxes: dict[str, QCheckBox] = {}
        for tile_id in model.CATALOG:
            present = tile_id in current
            box = QCheckBox(i18n.t(tile_id))
            box.setChecked(present)
            box.setToolTip(i18n.t(f"{tile_id}.hint"))
            self.boxes[tile_id] = box
            outer.addWidget(box)

        buttons = QHBoxLayout()
        buttons.setSpacing(th.SPACE_S)
        buttons.addStretch(1)
        cancel = QPushButton(i18n.t("Отмена"))
        cancel.setObjectName("GhostButton")
        cancel.clicked.connect(self.reject)
        apply_button = QPushButton(i18n.t("home.done"))
        apply_button.setObjectName("PrimaryButton")
        apply_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(apply_button)
        outer.addLayout(buttons)

    def checked_ids(self) -> list[str]:
        return [tile_id for tile_id, box in self.boxes.items() if box.isChecked()]


class DashboardWidget(QWidget):
    """Главный экран целиком: строка кнопок, сетка плиток, режим правки."""

    def __init__(self, host=None, parent=None):
        super().__init__(parent)
        self.host = host
        self.layout_model: dict[str, Any] = model.load_layout()
        self.editing = False
        self._tiles: dict[str, Tile] = {}
        self._row_widgets: list[QWidget] = []
        self._tick = 0
        self._drag_tile: Tile | None = None
        self._drop_line: QFrame | None = None
        self._drop_target: tuple[int, int] | None = None
        self._resize_tile: Tile | None = None
        self._resize_start = 0

        self._build_frame()
        self.reload()
        self.set_edit(False)     # в обычном режиме видны только «Правка» и подсказка

        gd_metrics.start()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    # -- каркас ------------------------------------------------------------
    def _build_frame(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*th.PAGE_MARGINS)
        outer.setSpacing(th.PAGE_SPACING)

        bar = QHBoxLayout()
        bar.setSpacing(th.SPACE_S)
        self.hint = QLabel(i18n.t("home.edit_hint"))
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)
        self.hint.setVisible(False)
        bar.addWidget(self.hint)
        bar.addStretch(1)

        self.btn_edit = QPushButton(i18n.t("home.edit"))
        self.btn_edit.setObjectName("GhostButton")
        self.btn_edit.setFixedHeight(28)
        self.btn_edit.setCheckable(True)
        self.btn_edit.clicked.connect(self._toggle_edit)
        self.btn_add = QPushButton(i18n.t("home.add"))
        self.btn_add.setObjectName("SmallButton")
        self.btn_add.setFixedHeight(28)
        self.btn_add.clicked.connect(self.open_catalog)
        self.btn_reset = QPushButton(i18n.t("home.reset"))
        self.btn_reset.setObjectName("GhostButton")
        self.btn_reset.setFixedHeight(28)
        self.btn_reset.clicked.connect(self.reset_layout)
        bar.addWidget(self.btn_add)
        bar.addWidget(self.btn_reset)
        bar.addWidget(self.btn_edit)
        outer.addLayout(bar)

        self.rows_host = QWidget()
        self.rows_host.setObjectName("Root")
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(th.PAGE_SPACING)
        self.rows_layout.addStretch(1)
        outer.addWidget(self.rows_host, 1)

    # -- отрисовка ---------------------------------------------------------
    def reload(self) -> None:
        """Собрать сетку заново из модели раскладки."""
        self._clear_rows()
        self._tiles.clear()
        for row in self.layout_model.get("rows", []):
            items = []
            for tile_spec in row.get("tiles", []):
                tile = self._make_tile(str(tile_spec.get("id")))
                if tile is None:
                    continue
                items.append((tile, int(tile_spec.get("w", 4))))
                self._tiles[tile.tile_id] = tile
            row_widget = RowWidget(items, model.ROW_HEIGHTS.get(row.get("h"), 140))
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row_widget)
            self._row_widgets.append(row_widget)
        self._retranslate_empty()

    def _clear_rows(self) -> None:
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._row_widgets = []
        if self._drop_line is not None:
            self._drop_line.deleteLater()
            self._drop_line = None

    def _make_tile(self, tile_id: str) -> Tile | None:
        cls = TILE_CLASSES.get(tile_id)
        if cls is None:
            return None
        tile = cls(tile_id, self)
        tile.set_edit(self.editing)
        return tile

    def _retranslate_empty(self) -> None:
        if self._tiles:
            self.hint.setVisible(self.editing)
            return
        self.hint.setText(i18n.t("home.none"))
        self.hint.setVisible(True)

    # -- метрики -----------------------------------------------------------
    def showEvent(self, event):  # noqa: N802 — имя из Qt
        super().showEvent(event)
        if hasattr(self, "_timer"):
            self._timer.start()
            self._on_tick()

    def hideEvent(self, event):  # noqa: N802 — имя из Qt
        super().hideEvent(event)
        if hasattr(self, "_timer"):
            self._timer.stop()

    def _on_tick(self) -> None:
        self._tick += 1
        snap = gd_metrics.snapshot()
        for tile in self._tiles.values():
            try:
                tile.update_metrics(snap)
            except Exception:  # noqa: BLE001 — одна плитка не роняет весь экран
                continue
        if self._tick % 5 == 0 and self.isVisible():
            for tile in self._tiles.values():
                try:
                    tile.refresh_slow()
                except Exception:  # noqa: BLE001
                    continue

    # -- режим правки ------------------------------------------------------
    def _toggle_edit(self) -> None:
        self.set_edit(self.btn_edit.isChecked())

    def set_edit(self, editing: bool) -> None:
        self.editing = editing
        self.btn_edit.setChecked(editing)
        self.btn_edit.setText(i18n.t("home.done") if editing else i18n.t("home.edit"))
        self.btn_add.setVisible(editing)
        self.btn_reset.setVisible(editing)
        self.hint.setVisible(editing or not self._tiles)
        if self._tiles:
            self.hint.setText(i18n.t("home.edit_hint"))
        for tile in self._tiles.values():
            tile.set_edit(editing)
        if not editing:
            self._hide_drop_line()

    # -- перетаскивание ----------------------------------------------------
    def drag_move(self, tile: Tile, global_pos: QPoint) -> None:
        self._drag_tile = tile
        target = self._target_for(global_pos)
        if target is None:
            self._hide_drop_line()
            return
        row_index, position = target
        self._drop_target = target
        self._show_drop_line(row_index, position)

    def drag_drop(self) -> None:
        self._hide_drop_line()
        tile = self._drag_tile
        target = self._drop_target
        self._drag_tile = None
        self._drop_target = None
        if tile is None or target is None:
            return
        row_index, position = target
        source_row = self.row_of(tile.tile_id)
        source_pos = self._index_of(tile.tile_id)
        if source_row == row_index and source_pos is not None and source_pos < position:
            position -= 1
        changed = model.move_tile(self.layout_model, tile.tile_id, row_index, position)
        if changed != self.layout_model:
            self.layout_model = changed
            self._persist()
            self.reload()

    def _target_for(self, global_pos: QPoint) -> tuple[int, int] | None:
        if not self._row_widgets:
            return None
        point = self.rows_host.mapFromGlobal(global_pos)
        best_row, best_distance = 0, None
        for index, row_widget in enumerate(self._row_widgets):
            rect = row_widget.geometry()
            if rect.top() <= point.y() <= rect.bottom():
                distance = 0
            else:
                distance = min(abs(point.y() - rect.top()), abs(point.y() - rect.bottom()))
            if best_distance is None or distance < best_distance:
                best_row, best_distance = index, distance
        row_widget = self._row_widgets[best_row]
        local_x = row_widget.mapFromGlobal(global_pos).x()
        position = 0
        for tile, _cols in row_widget.items:
            if local_x > tile.geometry().center().x():
                position += 1
        return best_row, position

    def _show_drop_line(self, row_index: int, position: int) -> None:
        row_widget = self._row_widgets[row_index]
        if self._drop_line is None or self._drop_line.parentWidget() is not row_widget:
            if self._drop_line is not None:
                self._drop_line.deleteLater()
            self._drop_line = QFrame(row_widget)
            self._drop_line.setObjectName("DropLine")
            self._drop_line.setFixedWidth(2)
        line = self._drop_line
        if line is None:  # только что создали; страховка для читателя
            return
        if position <= 0:
            x = 0
        elif position >= len(row_widget.items):
            x = max(row_widget.width() - 2, 0)
        else:
            x = max(row_widget.items[position][0].geometry().x() - th.PAGE_SPACING // 2, 0)
        line.setGeometry(x, 4, 2, max(row_widget.height() - 8, 10))
        line.show()
        line.raise_()

    def _hide_drop_line(self) -> None:
        if self._drop_line is None:
            return
        self._drop_line.hide()

    # -- ширина ------------------------------------------------------------
    def begin_resize(self, tile: Tile) -> None:
        self._resize_tile = tile
        self._resize_start = self._width_of(tile.tile_id)

    def resize_preview(self, tile: Tile, dx_pixels: int) -> None:
        if self._resize_tile is not tile:
            self.begin_resize(tile)
        row_widget = tile.parentWidget()
        total = max(row_widget.width(), 1)
        columns = int(round(self._resize_start + dx_pixels / total * model.COLUMNS))
        columns = max(1, min(model.COLUMNS, columns))
        changed = model.set_width(self.layout_model, tile.tile_id, columns)
        if changed != self.layout_model:
            self.layout_model = changed
            self._apply_widths()

    def end_resize(self) -> None:
        if self._resize_tile is None:
            return
        self._resize_tile = None
        self._persist()
        self.reload()

    def _apply_widths(self) -> None:
        """Раздать строкам новые ширины без пересборки: тянем ручку живьём."""
        for index, row in enumerate(self.layout_model.get("rows", [])):
            if index >= len(self._row_widgets):
                break
            self._row_widgets[index].set_widths(
                [int(tile.get("w", 4)) for tile in row.get("tiles", [])]
            )

    # -- правки раскладки --------------------------------------------------
    def remove_tile(self, tile_id: str) -> None:
        changed = model.remove_tile(self.layout_model, tile_id)
        if changed != self.layout_model:
            self.layout_model = changed
            self._persist()
            self.reload()

    def set_row_height(self, row_index: int, height: str) -> None:
        changed = model.set_row_height(self.layout_model, row_index, height)
        if changed != self.layout_model:
            self.layout_model = changed
            self._persist()
            self.reload()

    def open_catalog(self) -> None:
        dialog = CatalogDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        chosen = dialog.checked_ids()
        current = model.tile_ids(self.layout_model)
        changed = self.layout_model
        for tile_id in model.CATALOG:
            if tile_id in chosen and tile_id not in current:
                changed = model.add_tile(changed, tile_id)
            elif tile_id not in chosen and tile_id in current:
                changed = model.remove_tile(changed, tile_id)
        if changed != self.layout_model:
            self.layout_model = changed
            self._persist()
            self.reload()

    def reset_layout(self) -> None:
        from gd_dialog import confirm

        if not confirm(
            self,
            i18n.t("home.reset"),
            i18n.t("home.reset_text"),
            confirm_text=i18n.t("home.reset"),
        ):
            return
        self.layout_model = model.default_layout()
        self._persist()
        self.reload()

    def _persist(self) -> None:
        model.save_layout(self.layout_model)

    # -- сведения о раскладке ----------------------------------------------
    def _width_of(self, tile_id: str) -> int:
        for row in self.layout_model.get("rows", []):
            for tile_spec in row.get("tiles", []):
                if tile_spec.get("id") == tile_id:
                    return int(tile_spec.get("w", 4))
        return 4

    def _index_of(self, tile_id: str) -> int | None:
        row_index = self.row_of(tile_id)
        if row_index is None:
            return None
        for index, tile_spec in enumerate(self.layout_model["rows"][row_index].get("tiles", [])):
            if tile_spec.get("id") == tile_id:
                return index
        return None

    def row_of(self, tile_id: str) -> int | None:
        for index, row in enumerate(self.layout_model.get("rows", [])):
            for tile_spec in row.get("tiles", []):
                if tile_spec.get("id") == tile_id:
                    return index
        return None

    def row_height(self, row_index: int) -> str:
        rows = self.layout_model.get("rows", [])
        if 0 <= row_index < len(rows):
            return str(rows[row_index].get("h", "M"))
        return "M"

    # -- переходы ----------------------------------------------------------
    def open_tab(self, key: str) -> None:
        self.ensure_page(key)
        host = self.host
        handler = getattr(host, "_on_nav_click", None)
        if handler is not None:
            handler(key)

    def ensure_page(self, key: str):
        handler = getattr(self.host, "_ensure_page", None)
        if handler is None:
            return None
        try:
            return handler(key)
        except Exception:  # noqa: BLE001 — экран не собрался, плитка не должна падать
            return None
