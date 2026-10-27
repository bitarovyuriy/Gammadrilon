# -*- coding: utf-8 -*-
"""Общая палитра, токены и единственный стиль приложения (монохром «Пульт»).

Один источник стиля: и главное окно, и страницы, и диалоги используют
`STYLESHEET` из этого модуля. Раньше существовали два похожих QSS (в `main.py`
и здесь), и правила в них расходились: чекбоксы 16 и 15 px, слайдеры с разным
вылетом ползунка, таблицы на разных фонах. Теперь шаблон один, а цвета, шкала
шрифтов, радиусы и отступы подставляются из токенов ниже.

Сетка: шаг 4 px (отступы и размеры — 4/8/12/16/24).
Шкала шрифтов: заголовок 20 / раздел 15 / основной 13 / подпись 12 / подсказка 11.
Моноширинные (Bahnschrift для чисел, Cascadia Mono для путей и версий) — только
значения, версии и пути; весь остальной текст — один шрифт Segoe UI.
"""

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
# MUTED подобран по контрасту: #6E6E6E на PRESSED #EAEAEA давал 4.23:1 (ниже нормы 4.5),
# #666666 даёт 4.75:1 и остаётся «тихим» серым на белом (5.7:1).
LIGHT = {
    "PAPER": "#FFFFFF",
    "SURFACE": "#F7F7F7",
    "RULE": "#D8D8D8",
    "MUTED": "#666666",
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

#: пары «текст на фоне», обязанные держать контраст ≥ 4.5:1 в обеих темах
#: (проверка — `_tmp_contrast.py`; disabled-состояния нормой не покрываются)
CONTRAST_PAIRS = (
    ("MUTED", "PAPER"), ("MUTED", "SURFACE"), ("MUTED", "DEEP"),
    ("MUTED", "PRESSED"),
    ("INK2", "PAPER"), ("INK2", "SURFACE"), ("INK2", "DEEP"),
    ("INK", "PAPER"), ("INK", "SURFACE"),
    ("ON_ACCENT", "ACCENT"), ("ON_ACCENT", "NAVY"), ("ON_ACCENT", "NAVY_HOVER"),
    ("WARN", "PAPER"), ("WARN", "SURFACE"), ("WARN", "DEEP"),
    ("DANGER", "DANGER_BG"), ("GOOD", "PAPER"),
)

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

# --- шрифты -----------------------------------------------------------------
UI_FONT = '"Segoe UI Variable Text", "Segoe UI", Arial'
DIGIT_FONT = '"Bahnschrift", "Segoe UI", Arial'          # значения и метрики: ровные цифры
MONO_FONT = '"Cascadia Mono", "Consolas", monospace'      # пути и версии

# --- токены геометрии (ТЗ 7: «один закон» — сетка, а не россыпь чисел) ------
SPACE_XS = 4     # отступ внутри тесно связанных пар (значение ↔ подпись)
SPACE_S = 8      # зазор между контролами в ряду
SPACE_M = 12     # поля карточки по вертикали, шаг между карточками
SPACE_L = 16     # поля карточки по горизонтали, поля страницы
SPACE_XL = 24    # поля диалогов-приветствий и «воздух» вокруг главного

CARD_MARGINS = (SPACE_L, SPACE_M, SPACE_L, SPACE_M)  # карточка: 16 по бокам, 12 сверху/снизу
CARD_SPACING = SPACE_S                               # шаг между блоками внутри карточки
# страница: 16 по бокам и снизу, 12 сверху — верхний зазор продолжает шапку страницы,
# поэтому шапка снизу полей не добавляет (шапка и карточки стоят по одной левой линии)
PAGE_MARGINS = (SPACE_L, SPACE_M, SPACE_L, SPACE_L)
PAGE_SPACING = SPACE_M                               # шаг между карточками страницы
HEAD_MARGINS = (SPACE_L, SPACE_L, SPACE_L, 0)        # шапка страницы: заголовок + подпись
HEAD_SPACING = SPACE_XS                              # зазор «заголовок ↔ подпись»
DIALOG_MARGINS = (SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)   # окно-диалог: воздух со всех сторон
DIALOG_SPACING = SPACE_L                             # шаг между блоками диалога

RADIUS = "8px"        # карточки, поля, кнопки, попапы, таблицы
RADIUS_SMALL = "4px"  # слайдеры, ползунки, скроллбары
CONTROL_HEIGHT = "32px"   # единая высота кнопок, полей и комбобоксов

# шкала шрифтов: числа храним отдельно — их же используют холсты (тики графика,
# подписи кривой), где текст рисуется QPainter'ом, а не стилем
H1_PX = 20            # заголовок страницы
H2_PX = 15            # заголовок диалога / раздела
BODY_PX = 13          # основной текст и контролы
SMALL_PX = 12         # подпись (таблицы, бейджи, статусбар)
TINY_PX = 11          # подсказка
BRAND_PX = 13         # бренд в титлбаре
VALUE_PX = 26         # крупное значение
VALUE_UNIT_PX = 12    # его единица измерения
VALUE_S_PX = 17       # значение в строке
VALUE_S_UNIT_PX = 11  # единица значения в строке

FONT_H1 = f"{H1_PX}px"
FONT_H2 = f"{H2_PX}px"
FONT_BODY = f"{BODY_PX}px"
FONT_SMALL = f"{SMALL_PX}px"
FONT_TINY = f"{TINY_PX}px"
FONT_BRAND = f"{BRAND_PX}px"
FONT_VALUE = f"{VALUE_PX}px"
FONT_VALUE_S = f"{VALUE_S_PX}px"

GEOMETRY = {
    "RADIUS": RADIUS,
    "RADIUS_SMALL": RADIUS_SMALL,
    "CONTROL_HEIGHT": CONTROL_HEIGHT,
    "FONT_H1": FONT_H1,
    "FONT_H2": FONT_H2,
    "FONT_BODY": FONT_BODY,
    "FONT_SMALL": FONT_SMALL,
    "FONT_TINY": FONT_TINY,
    "FONT_BRAND": FONT_BRAND,
    "FONT_VALUE": FONT_VALUE,
    "FONT_VALUE_S": FONT_VALUE_S,
    "UI_FONT": UI_FONT,
    "DIGIT_FONT": DIGIT_FONT,
    "MONO_FONT": MONO_FONT,
}


def active_name() -> str:
    return _active


def active() -> dict:
    return PALETTES[_active]


_QSS = """
/* === базовое ============================================================== */
* {
    font-family: @UI_FONT@;
    outline: none;
}

QWidget {
    color: @INK2@;
    font-size: @FONT_BODY@;
}

QWidget#PanelRoot,
QWidget#Central,
QStackedWidget#Pages,
QWidget#Root,
QScrollArea,
QScrollArea > QWidget > QWidget,
QDialog {
    background: @PAPER@;
}

QScrollArea::viewport,
QScrollArea#PageScroll::viewport,
QAbstractScrollArea::viewport {
    background: @PAPER@;
}

QWidget#PageHead,
QWidget#PageShell {
    background: @PAPER@;
}

/* === карточки ============================================================= */
QFrame#Card {
    background: @SURFACE@;
    border: 1px solid @RULE@;
    border-radius: @RADIUS@;
}

/* === типографика ========================================================== */
QLabel {
    background: transparent;
    color: @INK2@;
}

QLabel#PageTitle {
    color: @INK@;
    font-size: @FONT_H1@;
    font-weight: 600;
}

QLabel#Title {                       /* одиночное окно-страница (GPU, CPU) */
    color: @INK@;
    font-size: @FONT_H1@;
    font-weight: 600;
}

QLabel#DialogTitle {
    color: @INK@;
    font-size: @FONT_H2@;
    font-weight: 600;
}

QLabel#SectionTitle {
    color: @INK@;
    font-size: @FONT_H2@;
    font-weight: 600;
}

QLabel#CardLabel {                   /* подпись карточки: капс, тихая, над содержанием */
    color: @MUTED@;
    font-size: @FONT_TINY@;
    font-weight: 600;
}

QLabel#Metric {
    font-family: @DIGIT_FONT@;
    font-size: @FONT_VALUE_S@;
    font-weight: 600;
    color: @INK@;
}

QLabel#Value,
QLabel#BigValue {
    font-family: @DIGIT_FONT@;
    font-size: @FONT_VALUE@;
    font-weight: 600;
    color: @INK@;
}

QLabel#RowValue {
    font-family: @DIGIT_FONT@;
    font-size: @FONT_VALUE_S@;
    color: @INK@;
}

QLabel#Hint {
    color: @MUTED@;
    font-size: @FONT_TINY@;
}

QLabel#PageSub {
    color: @MUTED@;
    font-size: @FONT_SMALL@;
}

QLabel#Version {
    color: @MUTED@;
    font-family: @MONO_FONT@;
    font-size: @FONT_TINY@;
}

/* «i» в кружке у заголовка карточки: подсказка без зависимости от глифа ⓘ */
QLabel#CardInfo {
    color: @MUTED@;
    border: 1px solid @RULE@;
    border-radius: @RADIUS@;
    min-width: 14px;
    max-width: 14px;
    min-height: 14px;
    max-height: 14px;
    font-size: 10px;
}

QLabel#AppTitle {
    color: @INK@;
    font-size: @FONT_BRAND@;
    font-weight: 600;
}

QLabel#FallbackIcon {
    color: @INK@;
    font-size: 22px;
}

QLabel#Status {
    color: @INK@;
    font-size: @FONT_TINY@;
    background: @SURFACE@;
    border: 1px solid @RULE@;
    border-radius: @RADIUS@;
    padding: 4px 8px;
}

QLabel#StatusDot {
    color: @ACCENT@;
    font-size: 10px;
}

/* === главный экран: плитки ================================================ */
QFrame#Card[edit="true"] {
    border: 1px dashed @INK2@;
}

QFrame#Card[edit="true"]:hover {
    border-color: @INK@;
}

/* ручка ширины: полоса у правого края плитки, видна только в правке */
QFrame#TileResize {
    background: @TRACK@;
    border: none;
    border-radius: @RADIUS_SMALL@;
}

QFrame#TileResize:hover {
    background: @INK2@;
}

/* место вставки при перетаскивании: вертикальная линия в строке */
QFrame#DropLine {
    background: @INK@;
    border: none;
}

/* === титлбар ============================================================== */
QWidget#TitleBar {
    background: @SURFACE@;
    border-bottom: 1px solid @RULE@;
}

QFrame#BrandDot {
    background: @ACCENT@;
    border-radius: 4px;
}

QLabel#BrandLogo {
    background: transparent;
}

QLabel#Brand {
    color: @INK@;
    font-size: @FONT_BRAND@;
    font-weight: 600;
}

QLabel#BrandVer {
    color: @MUTED@;
    font-family: @MONO_FONT@;
    font-size: @FONT_TINY@;
}

QPushButton#ModeChip {
    background: @PRESSED@;
    border: 1px solid @TRACK@;
    border-radius: @RADIUS@;
    padding: 0 10px;
    color: @INK2@;
    font-size: @FONT_TINY@;
    min-height: 26px;
    max-height: 26px;
}

QPushButton#ModeChip:hover {
    border-color: @MUTED@;
    color: @INK@;
}

QPushButton#ModeChip:focus {
    border-color: @ACCENT@;
}

QPushButton#TitleBtn {
    background: transparent;
    border: 1px solid transparent;
    border-radius: @RADIUS_SMALL@;
    min-height: 0;
}

QPushButton#TitleBtn:hover {
    background: @PRESSED@;
    border-color: @RULE@;
}

QPushButton#TitleBtn:focus {
    border-color: @ACCENT@;
}

QPushButton#TitleBtn:pressed {
    background: @MID@;
}

QPushButton#WinMin,
QPushButton#WinMax,
QPushButton#WinClose {
    background: transparent;
    border: none;
    border-radius: 0;
    color: @MUTED@;
    font-size: @FONT_SMALL@;
    min-height: 0;
}

QPushButton#WinMin:hover,
QPushButton#WinMax:hover {
    background: @PRESSED@;
    color: @INK@;
}

QPushButton#WinMin:pressed,
QPushButton#WinMax:pressed {
    background: @MID@;
}

QPushButton#WinClose:hover {
    background: @ACCENT@;
    color: @ON_ACCENT@;
}

QPushButton#WinClose:pressed {
    background: @NAVY_PRESSED@;
    color: @ON_ACCENT@;
}

/* === сайдбар ============================================================== */
QWidget#SidebarWrap,
QWidget#Sidebar {
    background: @SURFACE@;
}

QWidget#SidebarWrap {
    border-right: 1px solid @RULE@;
}

QLabel#SideGroup {
    color: @MUTED@;
    font-size: @FONT_TINY@;
    font-weight: 600;
    padding: 12px 6px 4px 6px;
}

QWidget#NavItem {
    border-radius: @RADIUS@;
    border: 1px solid transparent;
}

QWidget#NavItem:hover {
    background: @PRESSED@;
}

QWidget#NavItem:focus {
    background: @PRESSED@;
    border: 1px solid @ACCENT@;
}

QWidget#NavItem[active="true"] {
    background: @PRESSED@;
    border: 1px solid @RULE@;
    border-left: 3px solid @ACCENT@;
}

QLabel#NavText {
    color: @INK2@;
    font-size: @FONT_BODY@;
    background: transparent;
}

QWidget#NavItem[active="true"] QLabel#NavText {
    color: @INK@;
}

QLabel#NavBadge {
    color: @MUTED@;
    font-size: @FONT_TINY@;
    border: 1px solid @RULE@;
    border-radius: 5px;
    padding: 1px 6px;
    background: transparent;
}

QWidget#SideTools,
QWidget#SideFoot {
    background: @SURFACE@;
    border-top: 1px solid @RULE@;
}

/* заголовок блока компактнее обычной группы: сайдбар должен влезать в минимальное
   окно (720×560) вместе с пунктами меню и полосой службы питания */
QLabel#SideToolsHead {
    color: @MUTED@;
    font-size: @FONT_TINY@;
    font-weight: 600;
    padding: 4px 6px 0 6px;
}

QPushButton#SideToolBtn {
    background: transparent;
    border: 1px solid transparent;
    border-radius: @RADIUS@;
    color: @INK2@;
    font-size: @FONT_SMALL@;
    text-align: left;
    padding: 0 10px;
    min-height: 0;
}

QPushButton#SideToolBtn:hover {
    background: @PRESSED@;
    border-color: @RULE@;
    color: @INK@;
}

QPushButton#SideToolBtn:pressed {
    background: @MID@;
}

QPushButton#SideToolBtn:focus {
    border-color: @ACCENT@;
}

QFrame#FootLed {
    background: @ACCENT@;
    border-radius: 3px;
}

/* компаньон питания не поднят: LED не горит — состояние честное (см. _sync_foot) */
QFrame#FootLed[state="off"] {
    background: @MUTED@;
}

QLabel#FootText {
    color: @MUTED@;
    font-size: @FONT_TINY@;
}

/* === статусбар ============================================================ */
QWidget#StatusBar {
    background: @SURFACE@;
    border-top: 1px solid @RULE@;
}

QLabel#StatusItem {
    color: @MUTED@;
    font-size: @FONT_SMALL@;
}

QLabel#StatusHint {
    color: @MUTED@;
    font-size: @FONT_TINY@;
}

QFrame#StatusSep {
    color: @RULE@;
    background: @RULE@;
    border: none;
    max-width: 1px;
}

/* === кнопки ============================================================== */
QPushButton {
    background: @SURFACE@;
    border: 1px solid @RULE@;
    border-radius: @RADIUS@;
    color: @INK2@;
    padding: 0 12px;
    font-size: @FONT_BODY@;
    min-height: @CONTROL_HEIGHT@;
}

QPushButton:hover:enabled {
    background: @PRESSED@;
    border-color: @MUTED@;
    color: @INK@;
}

QPushButton:pressed:enabled {
    background: @MID@;
    border-color: @ACCENT@;
    color: @INK@;
}

QPushButton:focus {
    border-color: @ACCENT@;
}

/* выключенная кнопка обязана выглядеть выключенной: иначе интерфейс обещает
   действие, которого нет (ТЗ 5/7 — честность состояния, а не «серая красота») */
QPushButton:disabled {
    background: @PRESSED@;
    border-color: @RULE@;
    color: @MUTED@;
}

QPushButton#PrimaryButton {
    background: @NAVY@;
    border-color: @NAVY@;
    color: @ON_ACCENT@;
    font-weight: 600;
}

QPushButton#PrimaryButton:hover:enabled {
    background: @NAVY_HOVER@;
    border-color: @NAVY_HOVER@;
}

QPushButton#PrimaryButton:pressed:enabled {
    background: @NAVY_PRESSED@;
    border-color: @NAVY_PRESSED@;
}

QPushButton#PrimaryButton:focus {
    border: 2px solid @ACCENT@;
}

QPushButton#PrimaryButton:disabled {
    background: @TRACK@;
    border-color: @RULE@;
    color: @MUTED@;
}

QPushButton#Segment {
    padding: 0 10px;
}

QPushButton#Segment:hover:enabled {
    border-color: @MUTED@;
}

QPushButton#Segment:checked {
    background: @NAVY@;
    border-color: @NAVY@;
    color: @ON_ACCENT@;
    font-weight: 600;
}

QPushButton#Segment:checked:hover:enabled {
    background: @NAVY_HOVER@;
    border-color: @NAVY_HOVER@;
}

QPushButton#Segment:checked:pressed:enabled {
    background: @NAVY_PRESSED@;
}

QPushButton#Segment:disabled {
    border-color: @RULE@;
    color: @MUTED@;
}

QPushButton#Segment:checked:disabled {
    background: @TRACK@;
    border-color: @TRACK@;
    color: @MUTED@;
}

QPushButton#Segment:focus {
    border-color: @ACCENT@;
}

QPushButton#GhostButton {
    background: transparent;
    border-color: transparent;
    color: @MUTED@;
}

QPushButton#GhostButton:hover:enabled {
    background: @PRESSED@;
    border-color: @RULE@;
    color: @INK@;
}

QPushButton#GhostButton:pressed:enabled {
    background: @MID@;
    color: @INK@;
}

QPushButton#GhostButton:disabled {
    background: transparent;
    border-color: transparent;
    color: @MUTED@;
}

QPushButton#ToggleButton {
    color: @ACCENT@;
    border-color: @TRACK@;
}

QPushButton#ToggleButton:hover:enabled {
    background: @PRESSED@;
    border-color: @MUTED@;
}

/* активное состояние — инверсия, как и остальные «включено» в «Пульте» */
QPushButton#ToggleButton:checked {
    background: @ACCENT@;
    border-color: @ACCENT@;
    color: @ON_ACCENT@;
    font-weight: 600;
}

QPushButton#ToggleButton:checked:hover:enabled {
    background: @NAVY_HOVER@;
    border-color: @NAVY_HOVER@;
}

QPushButton#ToggleButton:focus {
    border-color: @ACCENT@;
}

QPushButton#ToggleButton:disabled {
    border-color: @RULE@;
    color: @MUTED@;
}

/* destructive: пунктирная рамка вместо красного (строгий монохром) */
QPushButton#DangerButton {
    background: @DANGER_BG@;
    color: @DANGER@;
    border: 1px dashed @DANGER_BORDER2@;
    padding: 0 14px;
    font-size: @FONT_SMALL@;
    font-weight: 600;
}

QPushButton#DangerButton:hover:enabled {
    background: @DANGER_HOVER@;
    border-color: @ACCENT@;
    color: @INK@;
}

QPushButton#DangerButton:pressed:enabled {
    background: @MID@;
    border-color: @ACCENT@;
}

QPushButton#DangerButton:focus {
    border: 2px dashed @ACCENT@;
}

QPushButton#DangerButton:disabled {
    background: @DANGER_BG@;
    border-color: @DANGER_BORDER@;
    color: @MUTED@;
}

QPushButton#SmallButton {
    font-size: @FONT_TINY@;
    color: @MUTED@;
    min-height: 26px;
}

QPushButton#SmallButton:hover:enabled {
    color: @INK@;
}

/* === поля ввода =========================================================== */
QLineEdit,
QPlainTextEdit,
QTextEdit {
    background: @SURFACE@;
    color: @INK@;
    border: 1px solid @RULE@;
    border-radius: @RADIUS@;
    padding: 6px 10px;
    selection-background-color: @NAVY@;
    selection-color: @ON_ACCENT@;
}

QPlainTextEdit,
QTextEdit {
    background: @DEEP@;
    color: @INK2@;
}

QLineEdit:hover:enabled,
QPlainTextEdit:hover:enabled,
QTextEdit:hover:enabled {
    border-color: @MUTED@;
}

QLineEdit:focus,
QPlainTextEdit:focus,
QTextEdit:focus {
    border-color: @ACCENT@;
}

QLineEdit:disabled,
QPlainTextEdit:disabled,
QTextEdit:disabled {
    background: @PRESSED@;
    border-color: @RULE@;
    color: @MUTED@;
}

QLineEdit:read-only {
    color: @INK2@;
}

QLineEdit#PaletteInput {
    font-size: @FONT_H2@;
    min-height: 38px;
    padding: 0 12px;
}

QComboBox {
    background: @SURFACE@;
    color: @INK@;
    border: 1px solid @RULE@;
    border-radius: @RADIUS@;
    padding: 0 10px;
    font-size: @FONT_BODY@;
    min-height: @CONTROL_HEIGHT@;
}

QComboBox:hover:enabled {
    border-color: @MUTED@;
}

QComboBox:pressed:enabled,
QComboBox:on {
    border-color: @ACCENT@;
}

QComboBox:focus {
    border-color: @ACCENT@;
}

QComboBox:disabled {
    background: @PRESSED@;
    border-color: @RULE@;
    color: @MUTED@;
}

QComboBox::drop-down {
    border: none;
    width: 28px;
}

QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid @MUTED@;
}

QComboBox::down-arrow:disabled {
    border-top-color: @TRACK@;
}

QComboBox QAbstractItemView {
    background: @SURFACE@;
    color: @INK2@;
    border: 1px solid @RULE@;
    border-radius: @RADIUS@;
    padding: 4px;
    outline: none;
    selection-background-color: @NAVY@;
    selection-color: @ON_ACCENT@;
}

QComboBox QAbstractItemView::item {
    min-height: 26px;
    padding: 0 8px;
    border-radius: @RADIUS_SMALL@;
}

QComboBox QAbstractItemView::item:hover {
    background: @PRESSED@;
}

QSpinBox,
QDoubleSpinBox {
    background: @SURFACE@;
    color: @INK@;
    border: 1px solid @RULE@;
    border-radius: @RADIUS@;
    padding: 6px 8px;
    selection-background-color: @NAVY@;
    selection-color: @ON_ACCENT@;
}

QSpinBox:hover:enabled,
QDoubleSpinBox:hover:enabled {
    border-color: @MUTED@;
}

QSpinBox:focus,
QDoubleSpinBox:focus {
    border-color: @ACCENT@;
}

QSpinBox:disabled,
QDoubleSpinBox:disabled {
    background: @PRESSED@;
    border-color: @RULE@;
    color: @MUTED@;
}

/* спиновые кнопки: без них системный стиль рисует светлые стрелки в тёмной теме */
QSpinBox::up-button,
QDoubleSpinBox::up-button,
QSpinBox::down-button,
QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    width: 20px;
    background: @SURFACE@;
    border: none;
    border-left: 1px solid @RULE@;
}

QSpinBox::up-button,
QDoubleSpinBox::up-button {
    subcontrol-position: top right;
    border-top-right-radius: @RADIUS@;
}

QSpinBox::down-button,
QDoubleSpinBox::down-button {
    subcontrol-position: bottom right;
    border-bottom-right-radius: @RADIUS@;
}

QSpinBox::up-button:hover,
QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover,
QDoubleSpinBox::down-button:hover {
    background: @PRESSED@;
}

QSpinBox::up-button:pressed,
QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed,
QDoubleSpinBox::down-button:pressed {
    background: @MID@;
}

QSpinBox::up-arrow,
QDoubleSpinBox::up-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid @MUTED@;
}

QSpinBox::down-arrow,
QDoubleSpinBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid @MUTED@;
}

QSpinBox::up-arrow:disabled,
QSpinBox::down-arrow:disabled,
QDoubleSpinBox::up-arrow:disabled,
QDoubleSpinBox::down-arrow:disabled {
    border-bottom-color: @TRACK@;
    border-top-color: @TRACK@;
}

/* === слайдеры ============================================================= */
QSlider::groove:horizontal {
    height: 6px;
    border-radius: 3px;
    background: @TRACK@;
}

QSlider::sub-page:horizontal {
    background: @ACCENT@;
    border-radius: 3px;
}

QSlider::add-page:horizontal {
    background: @TRACK@;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -8px 0;
    border-radius: @RADIUS@;
    background: @ACCENT@;
    border: 3px solid @PAPER@;
}

QSlider::handle:horizontal:hover {
    background: @INK@;
    border-color: @PRESSED@;
}

QSlider::handle:horizontal:pressed {
    background: @NAVY_PRESSED@;
    border-color: @MID@;
}

QSlider::groove:horizontal:disabled,
QSlider::add-page:horizontal:disabled,
QSlider::sub-page:horizontal:disabled {
    background: @RULE@;
}

QSlider::handle:horizontal:disabled {
    background: @MUTED@;
}

QSlider::handle:horizontal:focus {
    background: @MUTED@;
    border-color: @INK@;
}

QSlider#TempSlider::groove:horizontal {
    height: 6px;
    background: transparent;
}

QSlider#TempSlider::sub-page:horizontal,
QSlider#TempSlider::add-page:horizontal {
    background: transparent;
}

QSlider#TempSlider::handle:horizontal {
    width: 16px;
    height: 12px;
    margin: -6px 0;
    border-radius: 5px;
    background: @ACCENT@;
    border: 3px solid @PAPER@;
}

/* === чекбоксы и радио ===================================================== */
QCheckBox,
QRadioButton {
    color: @INK2@;
    font-size: @FONT_BODY@;
    spacing: 8px;
    background: transparent;
}

QCheckBox:hover,
QRadioButton:hover {
    color: @INK@;
}

QCheckBox::indicator,
QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid @TRACK@;
    border-radius: @RADIUS_SMALL@;
    background: @SURFACE@;
}

QRadioButton::indicator {
    border-radius: 8px;
}

QCheckBox::indicator:hover,
QRadioButton::indicator:hover {
    border-color: @MUTED@;
}

QCheckBox::indicator:pressed,
QRadioButton::indicator:pressed {
    border-color: @ACCENT@;
}

QCheckBox::indicator:checked,
QRadioButton::indicator:checked {
    background: @ACCENT@;
    border-color: @ACCENT@;
}

QCheckBox:focus,
QRadioButton:focus {
    color: @INK@;
}

QCheckBox:focus::indicator,
QRadioButton:focus::indicator {
    border-color: @ACCENT@;
}

QCheckBox:disabled,
QRadioButton:disabled {
    color: @MUTED@;
}

QCheckBox::indicator:disabled,
QRadioButton::indicator:disabled {
    background: @PRESSED@;
    border-color: @RULE@;
}

QCheckBox::indicator:checked:disabled,
QRadioButton::indicator:checked:disabled {
    background: @TRACK@;
    border-color: @TRACK@;
}

/* === таблицы и списки ===================================================== */
QTableWidget,
QListWidget,
QListView,
QTreeView {
    background: @DEEP@;
    color: @INK2@;
    border: 1px solid @RULE@;
    border-radius: @RADIUS@;
    gridline-color: transparent;
    font-size: @FONT_SMALL@;
    selection-background-color: @PRESSED@;
    selection-color: @INK@;
    outline: none;
}

QTableWidget:focus,
QListWidget:focus,
QListView:focus {
    border-color: @ACCENT@;
}

QTableWidget::item {
    padding: 8px 10px;
    border-bottom: 1px solid @RULE@;
}

QTableWidget::item:hover {
    background: @PRESSED@;
}

QTableWidget::item:selected {
    background: @PRESSED@;
    color: @INK@;
    /* строка выделяется подчёркиванием на всю ширину: подсказка формы, а не цвета
       (рамку слева нельзя — она рисовалась бы у каждой ячейки строки) */
    border-bottom: 1px solid @ACCENT@;
}

QTableWidget:alternate,
QTableWidget::item:alternate {
    background: @SURFACE@;
}

QListWidget::item,
QListView::item {
    padding: 6px 8px;
    border-radius: @RADIUS_SMALL@;
    border: 1px solid transparent;
}

QListWidget::item:hover,
QListView::item:hover {
    background: @PRESSED@;
}

QListWidget::item:selected,
QListView::item:selected {
    background: @PRESSED@;
    color: @INK@;
    border: 1px solid @RULE@;
    border-left: 2px solid @ACCENT@;
}

QListWidget::item:selected:!active,
QListView::item:selected:!active {
    background: @PRESSED@;
    color: @INK2@;
}

/* отметки в списках (выбор процессов и т.п.): своим стилем, иначе их не видно */
QListWidget::indicator,
QListView::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid @MUTED@;
    border-radius: @RADIUS_SMALL@;
    background: @SURFACE@;
}

QListWidget::indicator:hover,
QListView::indicator:hover {
    border-color: @ACCENT@;
}

QListWidget::indicator:checked,
QListView::indicator:checked {
    background: @ACCENT@;
    border-color: @ACCENT@;
}

QListWidget::indicator:disabled,
QListView::indicator:disabled {
    border-color: @RULE@;
    background: @PRESSED@;
}

QListWidget#PaletteList::item {
    padding: 8px 10px;
    font-size: @FONT_BODY@;
}

QListWidget#JournalList::item {
    padding: 8px 10px;
    font-size: @FONT_SMALL@;
}

/* фон хедера: без него пустая часть строки заголовка остаётся системно-светлой */
QHeaderView,
QHeaderView::section,
QTableCornerButton::section {
    background: @SURFACE@;
    border: none;
}

QHeaderView::section {
    color: @MUTED@;
    font-size: @FONT_TINY@;
    font-weight: 600;
    padding: 8px 10px;
    border-bottom: 1px solid @RULE@;
}

/* === всплывающие подтверждения (тосты) ==================================== */
QWidget#Toast {
    background: @SURFACE@;
    border: 1px solid @RULE@;
    border-left: 4px solid @ACCENT@;
    border-radius: @RADIUS@;
}

QWidget#Toast QLabel {
    color: @INK@;
    font-size: @FONT_SMALL@;
}

QWidget#Toast[kind="info"] {
    border-left-color: @MUTED@;
}

/* «не вышло» — пунктир: форма вместо цвета (как у DangerButton) */
QWidget#Toast[kind="warn"],
QWidget#Toast[kind="error"] {
    border: 1px dashed @DANGER_BORDER2@;
    border-left: 4px solid @MUTED@;
}

/* === меню, подсказки, скроллбары ========================================== */
QMenu {
    background: @SURFACE@;
    color: @INK2@;
    border: 1px solid @RULE@;
    border-radius: @RADIUS@;
    padding: 4px;
}

QMenu::item {
    padding: 6px 18px;
    border-radius: @RADIUS_SMALL@;
}

QMenu::item:selected {
    background: @NAVY@;
    color: @ON_ACCENT@;
}

QMenu::item:disabled {
    color: @MUTED@;
}

QMenu::separator {
    height: 1px;
    background: @RULE@;
    margin: 4px 8px;
}

QToolTip {
    background: @SURFACE@;
    color: @INK2@;
    border: 1px solid @RULE@;
    padding: 6px 8px;
    font-size: @FONT_SMALL@;
}

QScrollBar:vertical {
    width: 10px;
    background: transparent;
    margin: 4px 2px;
}

QScrollBar::handle:vertical {
    min-height: 28px;
    border-radius: @RADIUS_SMALL@;
    background: @TRACK@;
}

QScrollBar::handle:vertical:hover {
    background: @MUTED@;
}

QScrollBar::handle:vertical:pressed {
    background: @ACCENT@;
}

QScrollBar:horizontal {
    height: 10px;
    background: transparent;
    margin: 2px 4px;
}

QScrollBar::handle:horizontal {
    min-width: 28px;
    border-radius: @RADIUS_SMALL@;
    background: @TRACK@;
}

QScrollBar::handle:horizontal:hover {
    background: @MUTED@;
}

QScrollBar::handle:horizontal:pressed {
    background: @ACCENT@;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical,
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
    width: 0;
    height: 0;
    background: transparent;
}
"""


def build_stylesheet() -> str:
    """Собрать единый QSS из активной палитры и токенов геометрии."""
    qss = _QSS
    tokens = {**active(), **GEOMETRY}
    for key, value in tokens.items():
        qss = qss.replace(f"@{key}@", value)
    return qss


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
