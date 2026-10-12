# -*- coding: utf-8 -*-
"""Общая палитра и стили для всех окон приложения."""

DARK = {
    "PAPER": "#000000",
    "SURFACE": "#0B0B0B",
    "RULE": "#1F1F1F",
    "MUTED": "#8A8A8A",
    "INK2": "#C4C4C4",
    "INK": "#F5F5F5",
    "ACCENT": "#FFFFFF",
    "NAVY": "#FFFFFF",
    "NAVY_HOVER": "#E6E6E6",
    "NAVY_PRESSED": "#D2D2D2",
    "ON_ACCENT": "#000000",
    "TRACK": "#2E2E2E",
    "GOOD": "#FFFFFF",
    "WARN": "#B0B0B0",
    "PRESSED": "#161616",
    "TEMP_WARM": "#5A5A5A",     # градиент обесцвечен (строгий монохром)
    "TEMP_COLD": "#E8E8E8",
    "DANGER": "#D6D6D6",
    "DANGER_BG": "#1A1A1A",
    "DANGER_BORDER": "#3A3A3A",
    "DANGER_BORDER2": "#5A5A5A",
    "DANGER_HOVER": "#222222",
    "MID": "#2A2A2A",
    "DEEP": "#070707",
}

# Светлая тема — тоже строгий монохром: чёрный текст на белом, активные — чёрной заливкой.
LIGHT = {
    "PAPER": "#FFFFFF",
    "SURFACE": "#F7F7F7",
    "RULE": "#D8D8D8",
    "MUTED": "#6E6E6E",
    "INK2": "#3A3A3A",
    "INK": "#111111",
    "ACCENT": "#111111",
    "NAVY": "#111111",
    "NAVY_HOVER": "#2C2C2C",
    "NAVY_PRESSED": "#000000",
    "ON_ACCENT": "#FFFFFF",
    "TRACK": "#D8D8D8",
    "GOOD": "#111111",
    "WARN": "#4A4A4A",
    "PRESSED": "#EAEAEA",
    "TEMP_WARM": "#8A8A8A",
    "TEMP_COLD": "#2A2A2A",
    "DANGER": "#4A4A4A",
    "DANGER_BG": "#F0F0F0",
    "DANGER_BORDER": "#C0C0C0",
    "DANGER_BORDER2": "#9A9A9A",
    "DANGER_HOVER": "#E4E4E4",
    "MID": "#C9C9C9",
    "DEEP": "#FBFBFB",
}

PALETTES = {"dark": DARK, "light": LIGHT}
_active = "dark"

# статические алиасы активной палитры: обновляются в apply() через globals().update
PAPER = DARK["PAPER"]
SURFACE = DARK["SURFACE"]
RULE = DARK["RULE"]
MUTED = DARK["MUTED"]
INK2 = DARK["INK2"]
INK = DARK["INK"]
ACCENT = DARK["ACCENT"]
NAVY = DARK["NAVY"]
NAVY_HOVER = DARK["NAVY_HOVER"]
NAVY_PRESSED = DARK["NAVY_PRESSED"]
TRACK = DARK["TRACK"]
GOOD = DARK["GOOD"]
WARN = DARK["WARN"]
PRESSED = DARK["PRESSED"]
TEMP_WARM = DARK["TEMP_WARM"]
TEMP_COLD = DARK["TEMP_COLD"]
DANGER = DARK["DANGER"]
DANGER_BG = DARK["DANGER_BG"]
DANGER_BORDER = DARK["DANGER_BORDER"]
DANGER_BORDER2 = DARK["DANGER_BORDER2"]
DANGER_HOVER = DARK["DANGER_HOVER"]
MID = DARK["MID"]
DEEP = DARK["DEEP"]
ON_ACCENT = DARK["ON_ACCENT"]

UI_FONT = '"Segoe UI Variable Text", "Segoe UI", Arial'
DIGIT_FONT = '"Bahnschrift", "Segoe UI", Arial'

# --- токены геометрии (ТЗ 7: «один закон» — сетка, а не россыпь чисел) ---
RADIUS = "8px"        # карточки, поля, кнопки, попапы, таблицы
RADIUS_SMALL = "4px"  # слайдеры, ползунки, скроллбары
GEOMETRY = {"RADIUS": RADIUS, "RADIUS_SMALL": RADIUS_SMALL}


def active_name() -> str:
    return _active


def active() -> dict:
    return PALETTES[_active]


def build_stylesheet() -> str:
    """Собрать QSS из активной палитры."""
    _p = PALETTES[_active]
    (PAPER, SURFACE, RULE, MUTED, INK2, INK, ACCENT, NAVY, NAVY_HOVER,
     NAVY_PRESSED, TRACK, PRESSED, ON_ACCENT) = (
        _p["PAPER"], _p["SURFACE"], _p["RULE"], _p["MUTED"], _p["INK2"], _p["INK"],
        _p["ACCENT"], _p["NAVY"], _p["NAVY_HOVER"], _p["NAVY_PRESSED"], _p["TRACK"],
        _p["PRESSED"], _p["ON_ACCENT"])
    R8, R4 = RADIUS, RADIUS_SMALL
    return f"""
* {{
    font-family: {UI_FONT};
    outline: none;
}}

QWidget {{
    color: {INK2};
    font-size: 13px;
}}

QWidget#PanelRoot,
QDialog {{
    background: {PAPER};
}}

QWidget#Card {{
    background: {SURFACE};
}}

QLabel {{
    background: transparent;
}}

QLabel#Title {{
    color: {INK};
    font-family: {DIGIT_FONT};
    font-size: 20px;
    font-weight: 600;
}}

QLabel#CardLabel {{
    color: {MUTED};
    font-size: 12px;
}}

QLabel#Hint {{
    color: {MUTED};
    font-size: 11px;
}}

/* «i» в кружке у заголовка карточки: подсказка без зависимости от глифа ⓘ */
QLabel#CardInfo {{
    color: {MUTED};
    border: 1px solid {RULE};
    border-radius: {R8};
    min-width: 14px;
    max-width: 14px;
    min-height: 14px;
    max-height: 14px;
    font-family: {UI_FONT};
    font-size: 10px;
}}

QLabel#Value {{
    color: {INK};
    font-family: {DIGIT_FONT};
    font-size: 16px;
}}

QFrame#Card {{
    background: {SURFACE};
    border: 1px solid {RULE};
    border-radius: {R8};
}}

QPushButton {{
    background: {SURFACE};
    border: 1px solid {RULE};
    border-radius: 8px;
    color: {INK2};
    padding: 7px 12px;
    font-size: 13px;
}}

QPushButton:hover:enabled {{
    background: {PRESSED};
    border-color: {ACCENT};
    color: {INK};
}}

QPushButton:pressed {{
    background: {PRESSED};
}}

/* выключенная кнопка обязана выглядеть выключенной: иначе интерфейс обещает
   действие, которого нет (ТЗ 5/7 — честность состояния, а не «серая красота») */
QPushButton:disabled {{
    background: {SURFACE};
    border-color: {RULE};
    color: {MUTED};
}}

QPushButton#Segment {{
    padding: 7px 10px;
}}

QPushButton#Segment:checked {{
    background: {NAVY};
    border-color: {NAVY};
    color: {ON_ACCENT};
}}

QPushButton#Segment:hover:enabled:!checked {{
    border-color: {MUTED};
}}

QPushButton#PrimaryButton {{
    background: {NAVY};
    border-color: {NAVY};
    color: {ON_ACCENT};
    font-weight: 600;
}}

QPushButton#PrimaryButton:hover:enabled {{
    background: {NAVY_HOVER};
    border-color: {NAVY_HOVER};
}}

QPushButton#PrimaryButton:pressed {{
    background: {NAVY_PRESSED};
}}

QPushButton#PrimaryButton:disabled {{
    background: {TRACK};
    border-color: {RULE};
    color: {MUTED};
}}

QPushButton#GhostButton {{
    border-color: transparent;
    color: {MUTED};
}}

QPushButton#GhostButton:hover {{
    color: {INK2};
}}

QSlider::groove:horizontal {{
    height: 6px;
    border-radius: 3px;
    background: {TRACK};
}}

QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 3px;
}}

QSlider::add-page:horizontal {{
    background: {TRACK};
    border-radius: 3px;
}}

QSlider::handle:horizontal {{
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: {INK};
}}

QComboBox {{
    background: {SURFACE};
    border: 1px solid {RULE};
    border-radius: 8px;
    padding: 6px 10px;
    color: {INK};
}}

QLineEdit,
QSpinBox,
QDoubleSpinBox {{
    background: {SURFACE};
    border: 1px solid {RULE};
    border-radius: 8px;
    padding: 6px 10px;
    color: {INK};
}}

QComboBox:hover,
QLineEdit:hover,
QSpinBox:hover,
QDoubleSpinBox:hover {{
    border-color: {MUTED};
}}

/* спиновые кнопки: без них системный стиль рисует светлые стрелки в тёмной теме */
QSpinBox::up-button,
QDoubleSpinBox::up-button,
QSpinBox::down-button,
QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    width: 18px;
    background: {SURFACE};
    border: none;
    border-left: 1px solid {RULE};
}}

QSpinBox::up-button,
QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
    border-top-right-radius: {R8};
}}

QSpinBox::down-button,
QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: {R8};
}}

QSpinBox::up-button:hover,
QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover,
QDoubleSpinBox::down-button:hover {{
    background: {PRESSED};
}}

QSpinBox::up-arrow,
QDoubleSpinBox::up-arrow {{
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {MUTED};
}}

QSpinBox::down-arrow,
QDoubleSpinBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {MUTED};
}}

QComboBox QAbstractItemView {{
    background: {SURFACE};
    color: {INK2};
    border: 1px solid {RULE};
    border-radius: {R8};
    selection-background-color: {NAVY};
    selection-color: {INK};
}}

QTableWidget,
QListWidget,
QPlainTextEdit,
QTextEdit {{
    background: {DEEP};
    color: {INK2};
    border: 1px solid {RULE};
    border-radius: 8px;
    gridline-color: {RULE};
    selection-background-color: {NAVY};
    selection-color: {INK};
}}

/* фон хедера: без него пустая часть строки заголовка остаётся системно-светлой */
QHeaderView {{
    background: {SURFACE};
    border: none;
}}

QHeaderView::section {{
    background: {SURFACE};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {RULE};
    padding: 7px 8px;
}}

QTableWidget::item {{
    padding: 5px 7px;
    border-bottom: 1px solid {RULE};
}}

/* отметки в списках (выбор процессов и т.п.) — своим стилем, иначе их не видно */
QListWidget::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {MUTED};
    border-radius: {R4};
    background: {SURFACE};
}}

QListWidget::indicator:hover {{
    border-color: {ACCENT};
}}

QListWidget::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

QScrollArea {{
    background: {PAPER};
    border: none;
}}

QScrollArea::viewport {{
    background: {PAPER};
}}

QScrollBar:vertical {{
    width: 10px;
    background: transparent;
    margin: 4px 2px;
}}

QScrollBar::handle:vertical {{
    min-height: 28px;
    border-radius: {R4};
    background: {TRACK};
}}

QScrollBar::handle:vertical:hover {{
    background: {MUTED};
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    height: 0;
    background: transparent;
}}

/* горизонтальная полоса: нужна таблицам и спискам при узком окне */
QScrollBar:horizontal {{
    height: 10px;
    background: transparent;
    margin: 2px 4px;
}}

QScrollBar::handle:horizontal {{
    min-width: 28px;
    border-radius: {R4};
    background: {TRACK};
}}

QScrollBar::handle:horizontal:hover {{
    background: {MUTED};
}}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    width: 0;
    background: transparent;
}}

QTableWidget:alternate {{
    background: {SURFACE};
}}

QToolTip {{
    background: {SURFACE};
    color: {INK2};
    border: 1px solid {RULE};
    padding: 6px 8px;
    font-size: 12px;
}}

/* --- доступность: видимый фокус (ТЗ 5) --- */
QPushButton:focus {{
    border: 1px solid {ACCENT};
}}

QPushButton#PrimaryButton:focus,
QPushButton#DangerButton:focus {{
    border: 2px solid {ACCENT};
}}

QPushButton#Segment:focus {{
    border: 1px solid {ACCENT};
}}

QCheckBox:focus {{
    color: {INK};
}}

QCheckBox:focus::indicator {{
    border-color: {ACCENT};
}}

QComboBox:focus {{
    border-color: {ACCENT};
}}

QLineEdit:focus {{
    border-color: {ACCENT};
}}

QSlider:focus {{
    border: 1px solid {ACCENT};
    border-radius: 4px;
}}

QTableWidget:focus,
QListWidget:focus {{
    border: 1px solid {ACCENT};
}}

QTabBar::tab:focus {{
    color: {INK};
    border-bottom: 2px solid {ACCENT};
}}
"""


def apply(name: str) -> str:
    """Переключить тему: обновляет глобальные константы (PAPER, INK, ...) и STYLESHEET."""
    global _active, STYLESHEET
    _active = name if name in PALETTES else "dark"
    globals().update(PALETTES[_active])
    STYLESHEET = build_stylesheet()
    return _active


# начальное состояние
globals().update(DARK)
STYLESHEET = build_stylesheet()
