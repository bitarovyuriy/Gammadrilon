import ctypes
import json
import math
import os
import sys
import time
import subprocess
import winreg
from ctypes import wintypes
from pathlib import Path

import keyboard


# --- PyQt5 и не-ASCII путь ----------------------------------------------------
# Встроенный в PyQt5 файл qt.conf Qt разбирает как Latin-1 INI: если путь к
# окружению содержит не-ASCII символы (например, D:\Сlaude\...), путь к
# плагинам превращается в мусор и QApplication падает без сообщения об ошибке.
# Переменную окружения QT_PLUGIN_PATH Qt читает корректно — выставляем заранее.
try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
except Exception:
    pass


from PyQt5.QtCore import (
    Qt,
    QObject,
    QTimer,
    QEvent,
    QSize,
    QRect,
    QRectF,
    QPointF,
    pyqtSignal,
    QEasingCurve,
    QUrl,
)
from PyQt5.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt5.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSystemTrayIcon,
    QTabWidget,
    QScrollArea,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)


APP_NAME = "Gammadrilon"
CONFIG_FILE = "presets.json"
ICON_FILE = "icon.ico"

# --- палитра (из gd_theme: тёмная/светлая, ТЗ 5) -------------------------------
import gd_theme as th  # noqa: E402
import gd_i18n as i18n  # noqa: E402
import gd_icons  # noqa: E402

SLIDER_GROOVE_HEIGHT = 6
SLIDER_HANDLE_SIZE = 22

# иконки пунктов сайдбара (gd_icons)
SIDE_ICONS = {
    "tab.cpu": "cpu",
    "tab.gpu": "gpu",
    "tab.gamma": "gamma",
    "tab.rules": "rules",
    "tab.utils": "utils",
    "tab.dns": "dns",
}

UI_FONT = '"Segoe UI Variable Text", "Segoe UI", Arial'
DIGIT_FONT = '"Bahnschrift", "Segoe UI", Arial'

# Рублей бы.
SUPPORT_URL = "https://boosty.to/bonzeros/donate"
SECOND_URL = "https://github.com/bonzeros/Gammadrilon"

def is_another_instance_running():
    mutex_name = f"Global\\{APP_NAME}_mutex"
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32.CreateMutexW(None, False, mutex_name)

    if kernel32.GetLastError() == 183:
        return True

    return False


def app_dir() -> Path:

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent


def resource_path(relative: str) -> Path:
    """
    Путь к ресурсам внутри PyInstaller onefile.
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative

    return app_dir() / relative


def config_path() -> Path:
    return app_dir() / CONFIG_FILE

def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))

def compute_gamma_ramp(brightness: float, contrast: float) -> list[list[int]]:

    ramp = []

    for i in range(256):
        x = i / 255.0

        # Контраст относительно 0.5
        x = 0.5 + (x - 0.5) * contrast

        # Яркость
        x = x * brightness

        # Клип
        x = max(0.0, min(1.0, x))

        value = int(x * 65535)
        ramp.append(value)

    return [ramp, ramp, ramp]


def kelvin_to_rgb_multipliers(kelvin: int) -> tuple[float, float, float]:

    kelvin = int(clamp(kelvin, 3000, 10000))
    temp = kelvin / 100.0

    if temp <= 66:
        red = 255
    else:
        red = temp - 60
        red = 329.698727446 * (red ** -0.1332047592)
        red = clamp(red, 0, 255)

    if temp <= 66:
        green = 99.4708025861 * math.log(temp) - 161.1195681661
        green = clamp(green, 0, 255)
    else:
        green = temp - 60
        green = 288.1221695283 * (green ** -0.0755148492)
        green = clamp(green, 0, 255)

    if temp >= 66:
        blue = 255
    elif temp <= 19:
        blue = 0
    else:
        blue = temp - 10
        blue = 138.5177312231 * math.log(blue) - 305.0447927307
        blue = clamp(blue, 0, 255)

    return red / 255.0, green / 255.0, blue / 255.0


DEFAULT_CONFIG = {
    "settings": {
        "start_with_windows": False,
        "close_on_x": False,
        "current": {
            "brightness": 1.0,
            "contrast": 1.0,
            "gamma": 1.0,
            "temperature": 6500,
        },
        "hotkeys": {
            "next_preset": "page up",
            "prev_preset": "page down",
            "reset": "ctrl+alt+z",
            "cat_vision": "scroll lock",
        },
    },
    "presets": [
        {
            "name": "Normal",
            "brightness": 1.0,
            "contrast": 1.0,
            "gamma": 1.0,
            "temperature": 6500,
            "hotkey": "",
        },
        {
            "name": "Warm Night",
            "brightness": 0.75,
            "contrast": 0.85,
            "gamma": 1.08,
            "temperature": 4500,
            "hotkey": "",
        },
        {
            "name": "Bright Game",
            "brightness": 1.35,
            "contrast": 1.05,
            "gamma": 1.0,
            "temperature": 6500,
            "hotkey": "",
        },
    ],
}


def save_config(data: dict) -> None:
    with config_path().open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)


def load_config() -> dict:
    path = config_path()

    if not path.exists():
        data = json.loads(json.dumps(DEFAULT_CONFIG))
        save_config(data)
        return data

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        data = json.loads(json.dumps(DEFAULT_CONFIG))
        save_config(data)
        return data

    data.setdefault("settings", {})
    data.setdefault("presets", [])

    data["settings"].setdefault("close_on_x", False)
    data["settings"].setdefault("start_with_windows", False)

    data["settings"].setdefault(
        "current",
        json.loads(json.dumps(DEFAULT_CONFIG["settings"]["current"])),
    )
    data["settings"].setdefault(
        "hotkeys",
        json.loads(json.dumps(DEFAULT_CONFIG["settings"]["hotkeys"])),
    )

    for key, value in DEFAULT_CONFIG["settings"]["current"].items():
        data["settings"]["current"].setdefault(key, value)

    for key, value in DEFAULT_CONFIG["settings"]["hotkeys"].items():
        data["settings"]["hotkeys"].setdefault(key, value)

    if not data["presets"]:
        data["presets"] = json.loads(json.dumps(DEFAULT_CONFIG["presets"]))

    for preset in data["presets"]:
        preset.setdefault("name", "Unnamed")
        preset.setdefault("brightness", 1.0)
        preset.setdefault("contrast", 1.0)
        preset.setdefault("gamma", 1.0)
        preset.setdefault("temperature", 6500)
        preset.setdefault("hotkey", "")

        preset["brightness"] = clamp(float(preset["brightness"]), 0.5, 2.0)
        preset["contrast"] = clamp(float(preset["contrast"]), 0.3, 2.0)
        preset["gamma"] = clamp(float(preset["gamma"]), 0.5, 2.5)
        preset["temperature"] = int(clamp(int(preset["temperature"]), 3000, 10000))

    current = data["settings"]["current"]
    current["brightness"] = clamp(float(current.get("brightness", 1.0)), 0.5, 2.0)
    current["contrast"] = clamp(float(current.get("contrast", 1.0)), 0.3, 2.0)
    current["gamma"] = clamp(float(current.get("gamma", 1.0)), 0.5, 2.5)
    current["temperature"] = int(clamp(int(current.get("temperature", 6500)), 3000, 10000))

    save_config(data)
    return data

def get_startup_shortcut_path() -> Path:
    startup_dir = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup_dir / f"{APP_NAME}.bat"


def enable_startup():
    bat_path = get_startup_shortcut_path()
    target = sys.executable if getattr(sys, "frozen", False) else sys.executable

    script_path = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()

    if getattr(sys, "frozen", False):
        command = f'"""{target}"""'
    else:
        command = f'"""{target}""" "{script_path}"'

    content = f'@echo off\nstart "" {command}\n'
    bat_path.write_text(content, encoding="utf-8")


def disable_startup():
    bat_path = get_startup_shortcut_path()
    if bat_path.exists():
        bat_path.unlink()

class GammaController:
    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32

        self.user32.GetDC.argtypes = [wintypes.HWND]
        self.user32.GetDC.restype = wintypes.HDC

        self.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.user32.ReleaseDC.restype = ctypes.c_int

        self.gdi32.SetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
        self.gdi32.SetDeviceGammaRamp.restype = wintypes.BOOL

    def build_ramp(
        self,
        brightness: float,
        contrast: float,
        gamma: float = 1.0,
        temperature: int = 6500,
    ) -> tuple[list[int], list[int], list[int]]:
        brightness = clamp(brightness, 0.5, 2.0)
        contrast = clamp(contrast, 0.3, 2.0)
        gamma = clamp(gamma, 0.5, 2.5)
        temperature = int(clamp(temperature, 3000, 10000))

        red_multiplier, green_multiplier, blue_multiplier = kelvin_to_rgb_multipliers(temperature)

        red = []
        green = []
        blue = []

        for i in range(256):
            x = i / 255.0

            # 1) Контраст вокруг 0.5
            x = 0.5 + (x - 0.5) * contrast

            # 2) Яркость
            x = x * brightness

            # 3) Гамма — обрабатываем отрицательные значения корректно
            if x <= 0.0:
                x = 0.0
            else:
                # гамма работает только на положительной части,
                # верхнюю часть НЕ клипуем здесь, чтобы яркость
                # реально влияла после умножения на температуру
                x = x ** (1.0 / gamma)

            # 4) Температура — каждый канал отдельно
            r_val = x * red_multiplier
            g_val = x * green_multiplier
            b_val = x * blue_multiplier

            # 5) Финальный клип — только сейчас
            r = int(clamp(r_val, 0.0, 1.0) * 65535)
            g = int(clamp(g_val, 0.0, 1.0) * 65535)
            b = int(clamp(b_val, 0.0, 1.0) * 65535)

            red.append(r)
            green.append(g)
            blue.append(b)

        return red, green, blue

    def apply(
        self,
        brightness: float,
        contrast: float,
        gamma: float = 1.0,
        temperature: int = 6500,
    ) -> bool:
        red, green, blue = self.build_ramp(brightness, contrast, gamma, temperature)

        gamma_array_type = ctypes.c_ushort * (3 * 256)
        gamma_array = gamma_array_type()

        for i in range(256):
            gamma_array[i] = red[i]
            gamma_array[256 + i] = green[i]
            gamma_array[512 + i] = blue[i]

        hdc = self.user32.GetDC(None)

        if not hdc:
            return False

        try:
            result = self.gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(gamma_array))
            return bool(result)
        finally:
            self.user32.ReleaseDC(None, hdc)

    def reset(self) -> bool:
        return self.apply(1.0, 1.0, 1.0, 6500)


class HotkeyManager(QObject):
    nextPreset = pyqtSignal()
    prevPreset = pyqtSignal()
    resetRequested = pyqtSignal()
    catVisionRequested = pyqtSignal()
    presetRequested = pyqtSignal(int)
    paletteRequested = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.handles = []

    def clear(self):
        for handle in self.handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass

        self.handles.clear()

    def register(self, config: dict):
        self.clear()

        hotkeys = config.get("settings", {}).get("hotkeys", {})
        presets = config.get("presets", [])

        def safe_add(hotkey: str, callback):
            hotkey = str(hotkey).strip()

            if not hotkey:
                return

            try:
                handle = keyboard.add_hotkey(hotkey, callback, suppress=False)
                self.handles.append(handle)
            except Exception as error:
                self.error.emit(f"Hotkey error: {hotkey} — {error}")

        safe_add(hotkeys.get("next_preset", "page up"), lambda: self.nextPreset.emit())
        safe_add(hotkeys.get("prev_preset", "page down"), lambda: self.prevPreset.emit())
        safe_add(hotkeys.get("reset", "ctrl+alt+z"), lambda: self.resetRequested.emit())
        safe_add(hotkeys.get("cat_vision", "scroll lock"), lambda: self.catVisionRequested.emit())
        safe_add(hotkeys.get("palette", "ctrl+alt+g"), lambda: self.paletteRequested.emit())

        for index, preset in enumerate(presets):
            preset_hotkey = str(preset.get("hotkey", "")).strip()

            if preset_hotkey:
                safe_add(preset_hotkey, lambda i=index: self.presetRequested.emit(i))


class TickScale(QWidget):
    """Шкала делений под слайдером: риски + подписи, текущее значение подсвечено."""

    def __init__(self, minimum: int, maximum: int, ticks, suffix: str = "", parent=None):
        super().__init__(parent)

        self.minimum = minimum
        self.maximum = maximum
        self.ticks = list(ticks)
        self.suffix = suffix
        self.current = None

        self.setFixedHeight(18)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_current(self, value):
        self.current = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        pad = SLIDER_HANDLE_SIZE / 2.0
        width = self.width() - 2 * pad

        if width <= 0:
            return

        font = QFont("Bahnschrift")
        font.setPixelSize(10)
        painter.setFont(font)

        span = max(1, self.maximum - self.minimum)

        for tick in self.ticks:
            x = pad + width * (tick - self.minimum) / span

            active = self.current is not None and abs(tick - self.current) < 1

            painter.setPen(QPen(QColor(th.INK if active else th.RULE), 1))
            painter.drawLine(int(round(x)), 0, int(round(x)), 4)

            painter.setPen(QPen(QColor(th.INK if active else th.MUTED), 1))

            text = f"{tick}{self.suffix}"
            metrics = painter.fontMetrics()
            text_width = metrics.horizontalAdvance(text)

            if tick == self.minimum:
                rect = QRect(int(round(x)), 6, text_width, self.height() - 6)
            elif tick == self.maximum:
                rect = QRect(int(round(x - text_width)), 6, text_width, self.height() - 6)
            else:
                rect = QRect(int(round(x - text_width / 2.0)), 6, text_width, self.height() - 6)

            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)


class TemperatureSlider(QSlider):
    """Слайдер поверх градиентной полосы тёплое → холодное."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        pad = SLIDER_HANDLE_SIZE / 2.0
        track_rect = QRectF(
            pad,
            self.height() / 2.0 - SLIDER_GROOVE_HEIGHT / 2.0,
            max(0.0, self.width() - 2 * pad),
            SLIDER_GROOVE_HEIGHT,
        )

        if track_rect.width() > 0:
            gradient = QLinearGradient(track_rect.left(), 0, track_rect.right(), 0)
            gradient.setColorAt(0.00, QColor(th.TEMP_WARM))
            gradient.setColorAt(0.26, QColor("#A7A7A7"))
            gradient.setColorAt(0.50, QColor("#C9C9C9"))
            gradient.setColorAt(0.74, QColor(th.ACCENT))
            gradient.setColorAt(1.00, QColor(th.TEMP_COLD))

            painter.setPen(Qt.NoPen)
            painter.setBrush(gradient)
            painter.drawRoundedRect(track_rect, 3, 3)

        painter.end()
        super().paintEvent(event)


class AppComboBox(QComboBox):
    """QComboBox с собственной стрелкой (в QSS её не нарисовать без картинки)."""

    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        cx = self.width() - 21
        cy = self.height() / 2.0

        pen = QPen(QColor(th.ACCENT if self.underMouse() else th.MUTED), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        painter.drawLine(QPointF(cx - 4.5, cy - 2), QPointF(cx, cy + 2.5))
        painter.drawLine(QPointF(cx, cy + 2.5), QPointF(cx + 4.5, cy - 2))


class CurveWidget(QWidget):
    def __init__(self, gamma_controller: GammaController):
        super().__init__()

        self.gamma_controller = gamma_controller
        self.brightness = 1.0
        self.contrast = 1.0
        self.gamma = 1.0
        self.temperature = 6500

        self.setMinimumHeight(135)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_values(self, brightness: float, contrast: float, gamma: float, temperature: int):
        self.brightness = brightness
        self.contrast = contrast
        self.gamma = gamma
        self.temperature = temperature
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        painter.setPen(QPen(QColor(th.RULE), 1))
        painter.setBrush(QColor(th.DEEP))
        painter.drawRoundedRect(rect, 9, 9)

        plot = rect.adjusted(11, 11, -11, -11)

        painter.setPen(QPen(QColor(th.RULE), 1))

        for i in range(1, 4):
            x = plot.left() + plot.width() * i / 4
            y = plot.top() + plot.height() * i / 4
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        red, _, _ = self.gamma_controller.build_ramp(
            self.brightness,
            self.contrast,
            self.gamma,
            self.temperature,
        )

        path = QPainterPath()

        for i, value in enumerate(red):
            x = plot.left() + plot.width() * i / 255.0
            y = plot.bottom() - plot.height() * (value / 65535.0)

            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        pen = QPen(QColor(th.ACCENT), 2.5)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPath(path)


class HotkeySettingsDialog(QDialog):
    def __init__(self, hotkeys: dict, parent=None):
        super().__init__(parent)

        self.setWindowTitle(i18n.t("Глобальные клавиши"))
        self.setModal(True)
        self.setMinimumWidth(430)

        self.inputs = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)

        title = QLabel(i18n.t("Глобальные клавиши"))
        title.setObjectName("DialogTitle")
        root.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        root.addLayout(form)

        items = [
            ("next_preset", i18n.t("Следующий пресет")),
            ("prev_preset", i18n.t("Предыдущий пресет")),
            ("reset", i18n.t("Аварийный сброс")),
            ("cat_vision", i18n.t("Кошачье зрение")),
        ]

        for row, (key, label_text) in enumerate(items):
            label = QLabel(label_text)
            edit = QLineEdit(hotkeys.get(key, ""))
            edit.setPlaceholderText(i18n.t("например: ctrl+alt+1"))

            self.inputs[key] = edit

            form.addWidget(label, row, 0)
            form.addWidget(edit, row, 1)

        hint = QLabel(
            i18n.t("Примеры: page up, page down, scroll lock, ctrl+alt+z, ctrl+shift+1.")
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel_button = QPushButton(i18n.t("Отмена"))
        save_button = QPushButton(i18n.t("Сохранить"))
        save_button.setObjectName("PrimaryButton")

        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self.accept)

        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        root.addLayout(buttons)

        self.setStyleSheet(APP_STYLESHEET)

    def result_hotkeys(self) -> dict:
        return {key: edit.text().strip() for key, edit in self.inputs.items()}


class NavItem(QWidget):
    """Пункт сайдбара: иконка + текст + бейдж, состояние active (макет A)."""

    clicked = pyqtSignal(str)

    def __init__(self, key: str, text: str, icon_name: str = "", badge: str = "", parent=None):
        super().__init__(parent)
        self.key = key
        self.icon_name = icon_name
        self.setObjectName("NavItem")
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("active", False)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(10)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(15, 15)
        if icon_name:
            self.icon_label.setPixmap(gd_icons.icon(icon_name, 15, "#C4C4C4").pixmap(15, 15))
        lay.addWidget(self.icon_label)

        self.text_label = QLabel(text)
        self.text_label.setObjectName("NavText")
        lay.addWidget(self.text_label)
        lay.addStretch(1)

        self.badge = None
        if badge:
            self.badge = QLabel(badge)
            self.badge.setObjectName("NavBadge")
            lay.addWidget(self.badge)

    def set_active(self, on: bool) -> None:
        self.setProperty("active", bool(on))
        if self.icon_name:
            color = "#FFFFFF" if on else "#C4C4C4"
            self.icon_label.setPixmap(gd_icons.icon(self.icon_name, 15, color).pixmap(15, 15))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.config = load_config()
        i18n.set_language(self.config.get("settings", {}).get("language") or i18n.detect())
        self.gamma_controller = GammaController()
        self.hotkey_manager = HotkeyManager()

        self.current_preset_index = 0
        self.is_quitting = False

        # базовое окно — горизонтальное (сайдбар + страница)
        self.compact_size = QSize(910, 700)
        self.min_window_size = QSize(720, 560)

        self.cat_active = False
        self.cat_previous_values = None

        self.animation_data = None
        self.gamma_animation_timer = QTimer(self)
        self.gamma_animation_timer.timeout.connect(self.animation_step)

        self.cat_timer = QTimer(self)
        self.cat_timer.setSingleShot(True)
        self.cat_timer.timeout.connect(self.stop_cat_vision)

        self.setWindowTitle(APP_NAME)
        self.resize(self.compact_size)
        self.setMinimumSize(self.min_window_size)
        self._setup_frameless()
        self._text_bindings = []
        self._tooltip_bindings = []

        icon_path = resource_path(ICON_FILE)

        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.build_ui()
        self.build_tray()
        self.start_rules()
        self.start_quiet_hour()
        try:
            self.apply_theme(self.config.get("settings", {}).get("theme", "dark"))
        except Exception:  # noqa: BLE001
            pass
        try:
            import gd_onboarding

            if gd_onboarding.should_show(self.config):
                QTimer.singleShot(
                    500, lambda: gd_onboarding.run(self.config, save_config, self))
        except Exception:  # noqa: BLE001
            pass
        self.bind_hotkeys()

        current = self.config["settings"]["current"]

        self.set_ui_values(
            current.get("brightness", 1.0),
            current.get("contrast", 1.0),
            current.get("gamma", 1.0),
            current.get("temperature", 6500),
            apply_now=True,
            save=False,
        )
              
    def make_card(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Card")

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        return frame, layout

    def bind_text(self, widget, key: str) -> None:
        widget.setText(i18n.t(key))
        self._text_bindings.append((widget, key))

    def bind_tooltip(self, widget, key: str) -> None:
        widget.setToolTip(i18n.t(key))
        self._tooltip_bindings.append((widget, key))

    @staticmethod
    def set_value_text(label: QLabel, value, suffix: str, big: bool = False):
        number_size, unit_size = (30, 14) if big else (17, 11)

        label.setTextFormat(Qt.RichText)
        label.setText(
            f'<span style="font-size:{number_size}px;font-weight:600">{value}</span>'
            f'<span style="font-size:{unit_size}px;color:{th.MUTED}"> {suffix}</span>'
        )

    def make_slider_row(
        self,
        title: str,
        minimum: int,
        maximum: int,
        value: int,
        suffix: str,
        big: bool = False,
    ) -> tuple[QWidget, QSlider, QLabel]:
        container = QWidget()

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)

        label = QLabel()
        self.bind_text(label, title)
        label.setObjectName("CardLabel")

        value_label = QLabel()
        value_label.setObjectName("BigValue" if big else "RowValue")
        self.set_value_text(value_label, value, suffix, big)

        top.addWidget(label)
        top.addStretch()
        top.addWidget(value_label, 0, Qt.AlignBottom)

        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(minimum)
        slider.setMaximum(maximum)
        slider.setValue(value)
        slider.setCursor(Qt.PointingHandCursor)
        slider.setMinimumHeight(SLIDER_HANDLE_SIZE)

        layout.addLayout(top)
        layout.addWidget(slider)

        return container, slider, value_label

    def tune_button(self, button: QPushButton):
        button.setMinimumHeight(38)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def build_ui(self):
        self.setStyleSheet(APP_STYLESHEET)

        # каркас варианта A «Пульт» (монохром): сайдбар + стек страниц + статусбар
        self.side = QWidget()
        self.side.setObjectName("Sidebar")
        self.side.setFixedWidth(226)
        self._side_layout = QVBoxLayout(self.side)
        self._side_layout.setContentsMargins(10, 12, 10, 12)
        self._side_layout.setSpacing(2)
        self._nav_items: dict = {}
        self._key_to_index: dict[str, int] = {}
        self._page_widgets: dict = {}
        self._page_heads: dict = {}

        self.tabs = QStackedWidget()
        self.tabs.setObjectName("Pages")

        # ---------- страница гаммы ----------
        gamma_page = QWidget()
        gamma_page.setObjectName("Root")

        main_layout = QHBoxLayout(gamma_page)
        main_layout.setContentsMargins(16, 16, 16, 14)
        main_layout.setSpacing(14)

        # ---------- левая (основная) панель ----------
        self.left_panel = QWidget()
        self.left_panel.setMinimumWidth(440)
        self.left_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(14)

        # шапка
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(11)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(34, 34)
        self.icon_label.setAlignment(Qt.AlignCenter)

        icon_path = resource_path(ICON_FILE)

        if icon_path.exists():
            pixmap = self._mono_pixmap(QIcon(str(icon_path)), 34)

            if not pixmap.isNull():
                self.icon_label.setPixmap(pixmap)

        if self.icon_label.pixmap() is None or self.icon_label.pixmap().isNull():
            self.icon_label.setText("☀")
            self.icon_label.setObjectName("FallbackIcon")

        title_block = QVBoxLayout()
        title_block.setContentsMargins(0, 0, 0, 0)
        title_block.setSpacing(1)

        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")

        self.version_label = QLabel("v1.0.1")
        self.version_label.setObjectName("Version")

        title_block.addWidget(title)
        title_block.addWidget(self.version_label)

        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("StatusDot")

        self.status_label = QLabel(i18n.t("Готово"))
        self.status_label.setObjectName("Status")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        status_block = QHBoxLayout()
        status_block.setContentsMargins(0, 0, 0, 0)
        status_block.setSpacing(6)
        status_block.addWidget(self.status_dot, 0, Qt.AlignVCenter)
        status_block.addWidget(self.status_label, 0, Qt.AlignVCenter)

        header_layout.addWidget(self.icon_label)
        header_layout.addLayout(title_block)
        header_layout.addStretch()
        header_layout.addLayout(status_block)

        left_layout.addWidget(header)

        # карточка гаммы
        gamma_card, gamma_layout = self.make_card()

        self.gamma_row, self.gamma_slider, self.gamma_value = self.make_slider_row(
            "Гамма",
            50,
            250,
            100,
            "%",
            big=True,
        )

        self.gamma_slider.valueChanged.connect(self.on_slider_changed)

        gamma_layout.addWidget(self.gamma_row)

        self.gamma_scale = TickScale(50, 250, (50, 100, 150, 200, 250))
        self.gamma_scale.set_current(self.gamma_slider.value())
        gamma_layout.addWidget(self.gamma_scale)

        left_layout.addWidget(gamma_card)

        # карточка пресетов
        presets_card, presets_layout = self.make_card()

        presets_title = QLabel()
        self.bind_text(presets_title, "Пресет")
        presets_title.setObjectName("CardLabel")
        presets_layout.addWidget(presets_title)

        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(10)

        self.preset_combo = AppComboBox()
        self.preset_combo.setMinimumHeight(42)
        self.preset_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.refresh_preset_combo()
        self.preset_combo.currentIndexChanged.connect(self.on_preset_selected)

        self.save_button = QPushButton()
        self.bind_text(self.save_button, "Сохранить как…")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.setMinimumHeight(42)
        self.save_button.setMinimumWidth(112)
        self.save_button.clicked.connect(self.save_as_preset)

        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(self.save_button)

        presets_layout.addLayout(preset_row)

        preset_actions = QHBoxLayout()
        preset_actions.setContentsMargins(0, 0, 0, 0)
        preset_actions.setSpacing(8)

        self.reset_button = QPushButton()
        self.bind_text(self.reset_button, "Сброс")
        self.reset_button.setObjectName("GhostButton")

        self.delete_button = QPushButton()
        self.bind_text(self.delete_button, "Удалить")
        self.delete_button.setObjectName("GhostButton")

        self.cat_button = QPushButton()
        self.bind_text(self.cat_button, "Кошачье зрение")
        self.cat_button.setObjectName("ToggleButton")
        self.cat_button.setCheckable(True)

        for button in (self.reset_button, self.delete_button, self.cat_button):
            self.tune_button(button)

        self.reset_button.clicked.connect(self.reset_all)
        self.delete_button.clicked.connect(self.delete_current_preset)
        self.cat_button.clicked.connect(self.toggle_cat_vision)

        preset_actions.addWidget(self.reset_button)
        preset_actions.addWidget(self.delete_button)
        preset_actions.addWidget(self.cat_button)

        presets_layout.addLayout(preset_actions)

        left_layout.addWidget(presets_card)

        # ---------- точная настройка (бывшая выезжающая панель) ----------
        advanced_card, advanced_layout = self.make_card()
        advanced_layout.setSpacing(16)

        adv_title = QLabel()
        self.bind_text(adv_title, "Точная настройка")
        adv_title.setObjectName("SectionTitle")
        advanced_layout.addWidget(adv_title)

        self.brightness_row, self.brightness_slider, self.brightness_value = self.make_slider_row(
            "Яркость",
            50,
            200,
            100,
            "%",
        )

        self.contrast_row, self.contrast_slider, self.contrast_value = self.make_slider_row(
            "Контраст",
            30,
            200,
            100,
            "%",
        )

        self.brightness_slider.valueChanged.connect(self.on_slider_changed)
        self.contrast_slider.valueChanged.connect(self.on_slider_changed)

        # температура — слайдер поверх градиентной полосы
        temp_block = QWidget()

        temp_layout = QVBoxLayout(temp_block)
        temp_layout.setContentsMargins(0, 0, 0, 0)
        temp_layout.setSpacing(6)

        temp_top = QHBoxLayout()
        temp_top.setContentsMargins(0, 0, 0, 0)
        temp_top.setSpacing(10)

        temp_label = QLabel()
        self.bind_text(temp_label, "Цветовая температура")
        temp_label.setObjectName("CardLabel")

        self.temperature_value = QLabel()
        self.temperature_value.setObjectName("RowValue")
        self.set_value_text(self.temperature_value, 6500, "K")

        temp_top.addWidget(temp_label)
        temp_top.addStretch()
        temp_top.addWidget(self.temperature_value, 0, Qt.AlignBottom)

        self.temperature_slider = TemperatureSlider(Qt.Horizontal)
        self.temperature_slider.setObjectName("TempSlider")
        self.temperature_slider.setMinimum(3000)
        self.temperature_slider.setMaximum(10000)
        self.temperature_slider.setValue(6500)
        self.temperature_slider.setMinimumHeight(SLIDER_HANDLE_SIZE)
        self.temperature_slider.setCursor(Qt.PointingHandCursor)
        self.temperature_slider.valueChanged.connect(self.on_slider_changed)

        temp_ends = QHBoxLayout()
        temp_ends.setContentsMargins(0, 0, 0, 0)

        warm_label = QLabel()
        self.bind_text(warm_label, "3000 · тёплый")
        warm_label.setObjectName("Hint")

        cold_label = QLabel()
        self.bind_text(cold_label, "10000 · холодный")
        cold_label.setObjectName("Hint")
        cold_label.setAlignment(Qt.AlignRight)

        temp_ends.addWidget(warm_label)
        temp_ends.addStretch()
        temp_ends.addWidget(cold_label)

        temp_layout.addLayout(temp_top)
        temp_layout.addWidget(self.temperature_slider)
        temp_layout.addLayout(temp_ends)

        advanced_layout.addWidget(self.brightness_row)
        advanced_layout.addWidget(self.contrast_row)
        advanced_layout.addWidget(temp_block)

        # кривая гаммы
        curve_block = QWidget()

        curve_layout = QVBoxLayout(curve_block)
        curve_layout.setContentsMargins(0, 0, 0, 0)
        curve_layout.setSpacing(8)

        curve_head = QHBoxLayout()
        curve_head.setContentsMargins(0, 0, 0, 0)

        curve_label = QLabel()
        self.bind_text(curve_label, "Кривая гаммы")
        curve_label.setObjectName("CardLabel")

        curve_note = QLabel()
        self.bind_text(curve_note, "вход → выход")
        curve_note.setObjectName("Hint")

        curve_head.addWidget(curve_label)
        curve_head.addStretch()
        curve_head.addWidget(curve_note)

        self.curve = CurveWidget(self.gamma_controller)
        self.curve.setMinimumHeight(130)

        curve_layout.addLayout(curve_head)
        curve_layout.addWidget(self.curve)

        advanced_layout.addWidget(curve_block)

        # переключатели
        options_layout = QVBoxLayout()
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(10)

        self.start_with_windows_checkbox = QCheckBox()
        self.bind_text(self.start_with_windows_checkbox, "Запускать вместе с Windows")
        self.start_with_windows_checkbox.setChecked(
            bool(self.config.get("settings", {}).get("start_with_windows", False))
        )
        self.start_with_windows_checkbox.stateChanged.connect(self.on_start_with_windows_changed)

        self.close_on_x_checkbox = QCheckBox()
        self.bind_text(
            self.close_on_x_checkbox,
            "Выходить из приложения при закрытии окна",
        )
        self.bind_tooltip(
            self.close_on_x_checkbox,
            "Если выключено, окно скроется в системный трей, а приложение продолжит работать.",
        )
        self.close_on_x_checkbox.setChecked(
            bool(self.config.get("settings", {}).get("close_on_x", False))
        )
        self.close_on_x_checkbox.stateChanged.connect(self.on_close_on_x_changed)

        options_layout.addWidget(self.start_with_windows_checkbox)
        options_layout.addWidget(self.close_on_x_checkbox)

        advanced_layout.addLayout(options_layout)

        # горячие клавиши
        self.preset_hotkey_button = QPushButton()
        self.bind_text(self.preset_hotkey_button, "Клавиша пресета")
        self.preset_hotkey_button.setObjectName("SmallButton")
        self.preset_hotkey_button.setMinimumHeight(32)
        self.preset_hotkey_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.preset_hotkey_button.clicked.connect(self.set_current_preset_hotkey)

        self.global_hotkeys_button = QPushButton()
        self.bind_text(self.global_hotkeys_button, "Глобальные клавиши")
        self.global_hotkeys_button.setObjectName("SmallButton")
        self.global_hotkeys_button.setMinimumHeight(32)
        self.global_hotkeys_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.global_hotkeys_button.clicked.connect(self.edit_global_hotkeys)

        hotkey_row = QHBoxLayout()
        hotkey_row.setContentsMargins(0, 0, 0, 0)
        hotkey_row.setSpacing(8)
        hotkey_row.addWidget(self.global_hotkeys_button)
        hotkey_row.addWidget(self.preset_hotkey_button)

        advanced_layout.addLayout(hotkey_row)

        advanced_layout.addStretch()

        # ссылки
        self.support_button = QPushButton("Мне на чай")
        self.bind_text(self.support_button, "Мне на чай")
        self.support_button.setObjectName("SmallButton")
        self.support_button.setMinimumHeight(32)
        self.support_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.support_button.clicked.connect(self.open_support_link)

        self.second_link_button = QPushButton("GitHub")
        self.second_link_button.setObjectName("SmallButton")
        self.second_link_button.setMinimumHeight(32)
        self.second_link_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.second_link_button.clicked.connect(self.open_second_link)

        links_row = QHBoxLayout()
        links_row.setContentsMargins(0, 0, 0, 0)
        links_row.setSpacing(8)
        links_row.addWidget(self.support_button)
        links_row.addWidget(self.second_link_button)

        advanced_layout.addLayout(links_row)

        left_layout.addWidget(advanced_card)

        left_layout.addStretch()

        footer = QLabel()
        self.bind_text(
            footer,
            "PgUp / PgDn — переключение пресетов · Ctrl+Alt+Z — аварийный сброс · ScrollLock — кошачье зрение",
        )
        footer.setObjectName("Hint")
        footer.setAlignment(Qt.AlignCenter)
        footer.setWordWrap(True)
        left_layout.addWidget(footer)

        main_layout.addWidget(self.left_panel)

        # ---------- вкладки ----------
        self._add_page(gamma_page, "tab.gamma")

        if str(app_dir()) not in sys.path:
            sys.path.insert(0, str(app_dir()))
        from gd_power_ui import PowerWindow

        self._power_page = PowerWindow(standalone=False)
        self._add_page(self._power_page, "tab.cpu")

        from gd_gpu_ui import GpuWidget

        self._gpu_page = GpuWidget(standalone=False)
        self._add_page(self._gpu_page, "tab.gpu")

        from gd_rules_ui import RulesWidget

        self._rules_page = RulesWidget(standalone=False)
        self._add_page(self._rules_page, "tab.rules")

        from gd_utils_ui import UtilsWidget

        self._utils_page = UtilsWidget(standalone=False, host=self)
        from PyQt5.QtWidgets import QScrollArea

        utils_scroll = QScrollArea()
        utils_scroll.setWidgetResizable(True)
        utils_scroll.setFrameShape(QScrollArea.NoFrame)
        utils_scroll.setWidget(self._utils_page)
        utils_scroll.setObjectName("UtilitiesScrollArea")
        self._utils_scroll = utils_scroll
        self._add_page(utils_scroll, "tab.utils")

        from gd_dns_ui import DnsWidget

        self._dns_page = DnsWidget(standalone=False)
        self._add_page(self._dns_page, "tab.dns")

        self._retranslate_tabs()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(self.tabs.currentIndex())

        # каркас: титлбар сверху, сайдбар слева, страницы справа, статусбар снизу
        central = QWidget()
        central.setObjectName("Central")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_titlebar())

        side_wrap = QWidget()
        side_wrap.setObjectName("SidebarWrap")
        side_lay = QVBoxLayout(side_wrap)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(0)
        side_lay.addWidget(self.side, 1)
        side_lay.addWidget(self._build_side_foot())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(side_wrap)
        body_layout.addWidget(self.tabs, 1)
        root.addWidget(body, 1)
        root.addWidget(self._build_statusbar())
        self.setCentralWidget(central)

    # -- каркас «Пульт»: сайдбар, страницы, статусбар (вариант A, монохром) -----
    def _rebuild_sidebar(self) -> None:
        """Пункты сайдбара: заголовки групп + NavItem (иконка, бейдж, active)."""
        if not hasattr(self, "_side_layout"):
            return
        current_key = self._key_for_widget(self.tabs.currentWidget())
        while self._side_layout.count():
            entry = self._side_layout.takeAt(0)
            w = entry.widget()
            if w is not None:
                w.deleteLater()
        self._nav_items = {}
        layout = (
            ("grp", "side.power"), ("page", "tab.cpu"), ("page", "tab.gpu"),
            ("grp", "side.system"), ("page", "tab.gamma"), ("page", "tab.rules"),
            ("page", "tab.utils"), ("page", "tab.dns"),
        )
        for kind, key in layout:
            if kind == "grp":
                label = QLabel(i18n.t(key))
                label.setObjectName("SideGroup")
                self._side_layout.addWidget(label)
                continue
            item = NavItem(key, i18n.t(key), SIDE_ICONS.get(key, ""), self._side_badge(key))
            item.setToolTip(i18n.t(f"{key}.full"))
            item.clicked.connect(self._on_nav_click)
            item.set_active(key == current_key)
            self._side_layout.addWidget(item)
            self._nav_items[key] = item
        self._side_layout.addStretch(1)

    def _key_for_widget(self, widget) -> str:
        """Ключ пункта сайдбара для виджета (обёртки) страницы."""
        if widget is None:
            return ""
        idx = self.tabs.indexOf(widget)
        for key, k_idx in self._key_to_index.items():
            if k_idx == idx:
                return key
        return ""

    def _on_nav_click(self, key: str) -> None:
        idx = self._key_to_index.get(key)
        if idx is not None and idx != self.tabs.currentIndex():
            self.tabs.setCurrentIndex(idx)

    def _side_badge(self, key: str) -> str:
        """Бейдж пункта: лимит мощности GPU, число правил."""
        try:
            if key == "tab.gpu":
                import gd_gpu

                state = gd_gpu.read_state() or {}
                pl = state.get("pl")
                return f"{pl:.0f} Вт" if pl else ""
            if key == "tab.rules":
                import gd_rules

                rules = getattr(gd_rules, "load_rules", None)
                if rules is None:
                    rules = getattr(gd_rules, "load", None)
                data = rules() if rules else []
                return str(len(data)) if data else ""
        except Exception:  # noqa: BLE001
            pass
        return ""

    # -- титлбар, фут сайдбара, безрамочное окно --------------------------------
    def _setup_frameless(self) -> None:
        """Безрамочное окно: свой титлбар + нативный ресайз по краям."""
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMouseTracking(True)
        self._resize_margin = 6
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _edges_at(self, global_pos):
        """Какие края окна под курсором (для нативного ресайза)."""
        rect = self.frameGeometry()
        m = self._resize_margin
        edges = Qt.Edges()
        if global_pos.x() <= rect.left() + m:
            edges |= Qt.LeftEdge
        if global_pos.x() >= rect.right() - m:
            edges |= Qt.RightEdge
        if global_pos.y() <= rect.top() + m:
            edges |= Qt.TopEdge
        if global_pos.y() >= rect.bottom() - m:
            edges |= Qt.BottomEdge
        return edges

    def _cursor_for_edges(self, edges):
        if edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
            return Qt.SizeFDiagCursor
        if edges in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
            return Qt.SizeBDiagCursor
        if edges & (Qt.LeftEdge | Qt.RightEdge):
            return Qt.SizeHorCursor
        if edges & (Qt.TopEdge | Qt.BottomEdge):
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def eventFilter(self, obj, event):  # noqa: N802
        etype = event.type()
        # колесо над ползунком прокручивает страницу, а не меняет значение
        if etype == QEvent.Wheel and isinstance(obj, QSlider):
            scroll = obj.parent()
            while scroll is not None and not isinstance(scroll, QAbstractScrollArea):
                scroll = scroll.parent()
            if scroll is not None:
                QApplication.sendEvent(scroll.viewport(), event)
            return True
        if (
            etype in (QEvent.MouseButtonPress, QEvent.MouseMove)
            and self.isVisible()
            and not self.isMaximized()
            and not self.isFullScreen()
        ):
            try:
                gp = event.globalPos()
            except AttributeError:
                return super().eventFilter(obj, event)
            if self.rect().contains(self.mapFromGlobal(gp)):
                edges = self._edges_at(gp)
                if etype == QEvent.MouseButtonPress and edges:
                    handle = self.windowHandle()
                    if handle is not None:
                        handle.startSystemResize(edges)
                        return True
                elif etype == QEvent.MouseMove and self.mouseGrabber() is None:
                    self.setCursor(self._cursor_for_edges(edges))
        return super().eventFilter(obj, event)

    @staticmethod
    def _mono_pixmap(icon, size: int):
        """Обесцвеченная иконка (строгий монохром в титлбаре)."""
        from PyQt5.QtGui import QImage, QPixmap

        image = icon.pixmap(size, size).toImage().convertToFormat(QImage.Format_ARGB32)
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color.alpha() == 0:
                    continue
                gray = int(0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue())
                color.setRgb(gray, gray, gray, color.alpha())
                image.setPixelColor(x, y, color)
        return QPixmap.fromImage(image)

    def _build_titlebar(self):
        """Кастомный титлбар: бренд, чип режима, быстрые кнопки, кнопки окна."""
        bar = QWidget()
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(42)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 8, 0)
        lay.setSpacing(10)

        icon_path = resource_path(ICON_FILE)
        if icon_path.exists():
            logo = QLabel()
            logo.setObjectName("BrandLogo")
            logo.setFixedSize(18, 18)
            logo.setPixmap(self._mono_pixmap(QIcon(str(icon_path)), 18))
            lay.addWidget(logo)
        else:
            dot = QFrame()
            dot.setObjectName("BrandDot")
            dot.setFixedSize(16, 16)
            lay.addWidget(dot)

        brand = QLabel(i18n.t("Gammadrilon"))
        brand.setObjectName("Brand")
        lay.addWidget(brand)

        ver = QLabel("v3.0")
        ver.setObjectName("BrandVer")
        lay.addWidget(ver)
        lay.addStretch(1)

        self._tb_mode = QPushButton()
        self._tb_mode.setObjectName("ModeChip")
        self._tb_mode.setCursor(Qt.PointingHandCursor)
        self._tb_mode.setToolTip(i18n.t("Текущий режим системы"))
        self._tb_mode.clicked.connect(self._show_cpu_page)
        lay.addWidget(self._tb_mode)

        for name, tip, slot in (
            ("palette", "Палитра команд (Ctrl+Alt+G)", self.open_palette),
            ("journal", "Журнал изменений", self.open_journal),
            ("theme", "Переключить тему", self.toggle_theme),
        ):
            btn = QPushButton()
            btn.setObjectName("TitleBtn")
            btn.setFixedSize(34, 28)
            btn.setIcon(gd_icons.icon(name, 15, "#8A8A8A"))
            btn.setIconSize(QSize(15, 15))
            btn.setToolTip(i18n.t(tip))
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            lay.addWidget(btn)

        controls = QWidget()
        controls.setObjectName("WinControls")
        ctl_lay = QHBoxLayout(controls)
        ctl_lay.setContentsMargins(0, 0, 0, 0)
        ctl_lay.setSpacing(0)
        for obj, glyph, slot in (
            ("WinMin", "\u2014", self.showMinimized),
            ("WinMax", "\u25a2", self._toggle_max),
            ("WinClose", "\u2715", self.close),
        ):
            btn = QPushButton(glyph)
            btn.setObjectName(obj)
            btn.setFixedSize(40, 30)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            ctl_lay.addWidget(btn)
        lay.addWidget(controls)
        self._update_mode_chip()
        return bar

    def _build_side_foot(self):
        """Низ сайдбара: индикатор службы питания."""
        foot = QWidget()
        foot.setObjectName("SideFoot")
        lay = QHBoxLayout(foot)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)
        led = QFrame()
        led.setObjectName("FootLed")
        led.setFixedSize(7, 7)
        lay.addWidget(led)
        self._foot_label = QLabel(i18n.t("Служба питания активна"))
        self._foot_label.setObjectName("FootText")
        lay.addWidget(self._foot_label)
        lay.addStretch(1)
        return foot

    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _show_cpu_page(self) -> None:
        idx = self._key_to_index.get("tab.cpu")
        if idx is not None:
            self.tabs.setCurrentIndex(idx)

    def _update_mode_chip(self) -> None:
        """Чип режима в титлбаре: текущий профиль CPU."""
        if not hasattr(self, "_tb_mode"):
            return
        name = ""
        try:
            page = getattr(self, "_power_page", None)
            if page is not None and hasattr(page, "current_profile"):
                pid = page.current_profile()
                if pid:
                    import gd_power

                    meta = gd_power.PROFILES.get(pid) or {}
                    name = i18n.t(str(meta.get("name") or pid).upper())
        except Exception:  # noqa: BLE001
            pass
        self._tb_mode.setText(f"{i18n.t('Режим:')} {name or i18n.t('не применён')}  \u25be")

    def _add_page(self, widget, key: str):
        """Страница в стек с шапкой (заголовок + подпись) — как в макете A."""
        shell = QWidget()
        shell.setObjectName("PageShell")
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        head.setObjectName("PageHead")
        head_lay = QVBoxLayout(head)
        head_lay.setContentsMargins(22, 16, 22, 6)
        head_lay.setSpacing(2)
        title = QLabel(i18n.t(f"{key}.title"))
        title.setObjectName("PageTitle")
        sub = QLabel(i18n.t(f"{key}.sub"))
        sub.setObjectName("PageSub")
        head_lay.addWidget(title)
        head_lay.addWidget(sub)
        outer.addWidget(head)
        if isinstance(widget, QScrollArea):
            outer.addWidget(widget, 1)
        else:
            scroll = QScrollArea()
            scroll.setObjectName("PageScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setWidget(widget)
            outer.addWidget(scroll, 1)

        self._page_heads[key] = (title, sub)
        self._page_widgets[key] = widget
        self._key_to_index[key] = self.tabs.count()
        self.tabs.addWidget(shell)

    def _retranslate_page_heads(self) -> None:
        for key, (title, sub) in getattr(self, "_page_heads", {}).items():
            title.setText(i18n.t(f"{key}.title"))
            if key == "tab.cpu":
                sub.setText(self._cpu_subtitle() or i18n.t(f"{key}.sub"))
            else:
                sub.setText(i18n.t(f"{key}.sub"))

    def _cpu_subtitle(self) -> str:
        """Подпись шапки CPU: «AMD Ryzen 5 5600 · схема «Высокая производительность»»."""
        parts = []
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                name = winreg.QueryValueEx(key, "ProcessorNameString")[0]
            for tail in (" 6-Core Processor", " 8-Core Processor", " 12-Core Processor",
                         " 16-Core Processor", " Processor"):
                name = name.replace(tail, "")
            if name.strip():
                parts.append(name.strip())
        except Exception:  # noqa: BLE001
            pass
        try:
            import gd_power

            guid = gd_power.get_active_scheme()
            scheme = gd_power.scheme_name(guid) if guid else ""
            if scheme:
                parts.append(f"{i18n.t('схема')} «{scheme}»")
        except Exception:  # noqa: BLE001
            pass
        return " · ".join(parts)

    def _build_statusbar(self):
        """Нижняя полоса: живые метрики (частота/нагрузка CPU, GPU, RAM, DNS)."""
        bar = QWidget()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(34)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(14)
        self._sb_cpu = QLabel("CPU —")
        self._sb_gpu = QLabel("GPU —")
        self._sb_ram = QLabel("RAM —")
        self._sb_dns = QLabel("DNS —")
        items = [self._sb_cpu, self._sb_gpu, self._sb_ram, self._sb_dns]
        for i, w in enumerate(items):
            w.setObjectName("StatusItem")
            lay.addWidget(w)
            if i < len(items) - 1:
                sep = QFrame()
                sep.setObjectName("StatusSep")
                sep.setFrameShape(QFrame.VLine)
                sep.setFixedHeight(14)
                lay.addWidget(sep)
        lay.addStretch(1)
        self._sb_hints = QLabel(i18n.t("status.hints"))
        self._sb_hints.setObjectName("StatusHint")
        lay.addWidget(self._sb_hints)

        self._sb_sensors = None
        self._sb_gpu_tick = 0
        self._sb_dns_tick = 0
        self._sb_timer = QTimer(self)
        self._sb_timer.timeout.connect(self._refresh_statusbar)
        self._sb_timer.start(2000)
        self._refresh_statusbar()
        return bar

    def _refresh_statusbar(self) -> None:
        """CPU/RAM — каждые 2 с; GPU — ~6 с; DNS — ~30 с (netsh дорогой)."""
        try:
            from gd_sensors import CpuSensors, ram_usage

            if self._sb_sensors is None:
                self._sb_sensors = CpuSensors()
            s = self._sb_sensors.sample()
            mhz, load = s.get("mhz"), s.get("load_pct")
            cpu = "CPU"
            if mhz:
                cpu += f" {mhz / 1000:.2f} ГГц"
            if load is not None:
                cpu += f" · {load:.0f}%"
            self._sb_cpu.setText(cpu)
            ram = ram_usage()
            self._sb_ram.setText(f"RAM {ram:.0f}%" if ram is not None else "RAM —")
        except Exception:  # noqa: BLE001
            pass

        self._sb_gpu_tick += 1
        if self._sb_gpu_tick % 3 == 1:
            try:
                import gd_gpu

                st = gd_gpu.read_state()
                if st:
                    temp = st.get("temp")
                    pl, pl_max = st.get("pl") or 0, st.get("pl_max") or 0
                    self._sb_gpu.setText(
                        f"GPU {temp:.0f} °C · {pl:.0f}/{pl_max:.0f} Вт"
                        if temp is not None else "GPU —")
            except Exception:  # noqa: BLE001
                pass

        self._sb_dns_tick += 1
        if self._sb_dns_tick % 15 == 1:
            try:
                import gd_dns

                st = gd_dns.current_state()
                servers = st.get("servers") or []
                self._sb_dns.setText("DNS " + (servers[0] if servers else "—"))
            except Exception:  # noqa: BLE001
                pass

    def build_tray(self):
        from gd_tray import TrayAssistant

        icon_path = resource_path(ICON_FILE)
        self.tray = TrayAssistant(str(icon_path) if icon_path.exists() else None, parent=self)

        self.tray.showRequested.connect(self.show_window)
        self.tray.cpuProfileRequested.connect(self.on_cpu_profile)
        self.tray.gpuProfileRequested.connect(self.on_gpu_profile)
        self.tray.paletteRequested.connect(self.open_palette)
        self.tray.journalRequested.connect(self.open_journal)
        self.tray.rulesRequested.connect(self.open_rules_tab)
        self.tray.utilsRequested.connect(self.open_utils_tab)
        self.tray.themeRequested.connect(self._tray_theme)
        self.tray.quitRequested.connect(self.quit_app)
        self.tray.show()

        # живой тултип (ТЗ 5.1): раз в секунду, без всплывающих окон
        self._gpu_cache = None
        self._tray_tick_n = 0
        self._tray_sensors = None
        self._tray_timer = QTimer(self)
        self._tray_timer.setInterval(1000)
        self._tray_timer.timeout.connect(self._tray_tick)
        self._tray_timer.start()
        self._tray_tick()

    def _tray_tick(self):
        """Тултип и цвет иконки трея — по живым данным."""
        from gd_sensors import CpuSensors, ram_usage
        from gd_tray import build_tooltip

        if self._tray_sensors is None:
            self._tray_sensors = CpuSensors()
        sensors = self._tray_sensors.sample()

        self._tray_tick_n += 1
        if self._tray_tick_n % 5 == 1:          # GPU-опрос реже: это subprocess
            try:
                import gd_gpu

                self._gpu_cache = gd_gpu.read_state()
            except Exception:  # noqa: BLE001
                self._gpu_cache = None

        try:
            cpu_profile = self._power_page.current_profile()
        except Exception:  # noqa: BLE001
            cpu_profile = None
        try:
            gpu_profile = self._gpu_page.current_profile()
        except Exception:  # noqa: BLE001
            gpu_profile = None

        tip = build_tooltip(cpu_profile, sensors, self._gpu_cache, gpu_profile, ram_usage())
        self.tray.refresh(cpu_profile, gpu_profile, tip)

    # -- движок правил (ТЗ 5.4) --------------------------------------------
    def start_rules(self):
        import gd_rules

        self._rule_engine = gd_rules.RuleEngine()
        self._rule_engine.prime()
        self._rule_timer = QTimer(self)
        self._rule_timer.setInterval(3000)
        self._rule_timer.timeout.connect(self._rules_tick)
        self._rule_timer.start()

    def _rules_tick(self):
        """Проверить правила и применить сработавшие."""
        import gd_rules

        rules = gd_rules.load_rules()
        fired = self._rule_engine.evaluate(rules)
        if not fired:
            return
        for rule, _why in fired:
            result = self._run_rule_action(rule)
            gd_rules.log_fire(rule, result)
            page = getattr(self, "_rules_page", None)
            if page is not None:
                page._reload_log()

    def _run_rule_action(self, rule: dict) -> str:
        action = rule.get("action") or {}
        kind = action.get("kind")
        name = rule.get("name") or "правило"
        try:
            if kind == "cpu_profile":
                self._power_page.apply_profile(action.get("profile"), source="правило")
                return i18n.t("«{v0}»: профиль CPU применён", v0=name)
            if kind == "cpu_restore":
                self._power_page.apply_profile("restore", source="правило")
                return i18n.t("«{v0}»: схема CPU возвращена", v0=name)
            if kind == "gpu_profile":
                self._gpu_page.apply_profile(action.get("profile"), source="правило")
                return i18n.t("«{v0}»: профиль GPU применён", v0=name)
            if kind == "gpu_restore":
                self._gpu_page.apply_profile("restore", source="правило")
                return i18n.t("«{v0}»: лимит GPU возвращён", v0=name)
            if kind == "purge_standby":
                utils = getattr(self, "_utils_page", None)
                if utils is None:
                    return i18n.t("«{v0}»: вкладка «Утилиты» недоступна", v0=name)
                utils._purge(auto=True)
                return i18n.t("«{v0}»: standby list очищен", v0=name)
        except Exception as exc:  # noqa: BLE001
            return i18n.t("«{v0}»: ошибка — {v1}", v0=name, v1=exc)
        return i18n.t("«{v0}»: действие недоступно", v0=name)

    # -- действия из трея --------------------------------------------------
    def on_cpu_profile(self, pid):
        """Применение профиля CPU из трея (действие, а не загрузка пресета)."""
        try:
            self._power_page.apply_profile(pid, source="трей")
        except Exception as exc:  # noqa: BLE001
            self.set_status(i18n.t("Профиль не применён: {v0}", v0=exc))

    def on_gpu_profile(self, name):
        try:
            self._gpu_page.apply_profile(name, source="трей")
        except Exception as exc:  # noqa: BLE001
            self.set_status(i18n.t("Профиль GPU не применён: {v0}", v0=exc))

    def open_palette(self):
        from gd_palette import CommandPalette

        self._palette = CommandPalette(self)
        self._palette.exec_()

    def open_journal(self):
        from gd_journal_ui import JournalDialog

        dlg = JournalDialog(self)
        dlg.exec_()

    def on_tray_activated(self, reason):

        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_tab_changed(self, index):
        """Сенсоры и компаньоны работают только на своей странице."""
        # подсветка активного пункта сайдбара (в т.ч. при переключении из трея/палитры)
        key = self._key_for_widget(self.tabs.widget(index))
        for nav_key, nav_item in getattr(self, "_nav_items", {}).items():
            nav_item.set_active(nav_key == key)
        self._update_mode_chip()
        page = self._page_widgets.get(key)
        power = getattr(self, "_power_page", None)
        gpu = getattr(self, "_gpu_page", None)
        if power is not None:
            (power.activate() if page is power else power.deactivate())
        if gpu is not None:
            (gpu.activate() if page is gpu else gpu.deactivate())
        rules = getattr(self, "_rules_page", None)
        if rules is not None and page is rules:
            rules.reload()
        utils = getattr(self, "_utils_page", None)
        if utils is not None:
            (utils.activate() if page is getattr(self, "_utils_scroll", None) else utils.deactivate())
        dns = getattr(self, "_dns_page", None)
        if dns is not None and page is dns:
            dns._refresh_now()

    def open_dns_tab(self):
        """Открыть вкладку «DNS» в главном окне."""
        self.tabs.setCurrentWidget(self._dns_page)
        self.show_window()

    def apply_theme(self, name: str) -> str:
        """Переключить тёмную/светлую тему (ТЗ 5)."""
        global APP_STYLESHEET
        name = th.apply(name)
        APP_STYLESHEET = build_app_stylesheet()
        try:
            self.config.setdefault("settings", {})["theme"] = name
            save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(APP_STYLESHEET)
        for page in (getattr(self, "_power_page", None), getattr(self, "_gpu_page", None),
                     getattr(self, "_rules_page", None), getattr(self, "_utils_page", None),
                     getattr(self, "_dns_page", None)):
            if page is not None:
                try:
                    page.setStyleSheet(th.STYLESHEET)
                except Exception:  # noqa: BLE001
                    pass
        try:
            self.setStyleSheet(APP_STYLESHEET)
            self.update()
        except Exception:  # noqa: BLE001
            pass
        if hasattr(self, "brightness_value"):
            self.update_value_labels()
        for widget_name in ("gamma_scale", "curve", "preset_combo"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.update()
        return name

    def toggle_theme(self):
        return self.apply_theme("light" if th.active_name() == "dark" else "dark")

    def _tray_theme(self, checked: bool = False):
        """Пункт трея «Светлая тема» — привести тему к состоянию галки."""
        want = "light" if checked else "dark"
        if th.active_name() != want:
            self.apply_theme(want)
        try:
            self.tray.set_theme_checked(th.active_name() == "light")
        except Exception:  # noqa: BLE001
            pass

    def show_first_run(self):
        """Показать экран первого запуска вручную (из палитры команд)."""
        import gd_onboarding

        gd_onboarding.run(self.config, save_config, self)

    def set_language(self, name: str) -> str:
        """Сменить язык интерфейса (ТЗ 5). Динамически обновляем оболочку."""
        was_ready = self.status_label.text() == i18n.t("Готово")
        name = i18n.set_language(name, self.config, save_config)
        if was_ready:
            self.status_label.setText(i18n.t("Готово"))
        for widget, key in self._text_bindings:
            widget.setText(i18n.t(key))
        for widget, key in self._tooltip_bindings:
            widget.setToolTip(i18n.t(key))
        self._retranslate_tabs()
        try:
            self.tray.retranslate()
            self.tray.set_theme_checked(th.active_name() == "light")
        except Exception:  # noqa: BLE001
            pass
        try:
            import gd_toast

            gd_toast.show_toast(
                ("Язык переключён. Внутренние экраны обновятся после перезапуска."
                 if name == "ru" else
                 "Language switched. Inner screens will refresh after restart."),
                "info")
        except Exception:  # noqa: BLE001
            pass
        return name

    def _retranslate_tabs(self) -> None:
        """Оболочка каркаса: пункты сайдбара, шапки страниц, подсказка статусбара."""
        self._rebuild_sidebar()
        self._retranslate_page_heads()
        hints = getattr(self, "_sb_hints", None)
        if hints is not None:
            hints.setText(i18n.t("status.hints"))

    def toggle_language(self):
        return self.set_language("en" if i18n.current() == "ru" else "ru")

    def open_utils_tab(self):
        """Открыть вкладку «Утилиты» в главном окне."""
        self.tabs.setCurrentWidget(getattr(self, "_utils_scroll", None) or self._utils_page)
        self.show_window()

    # -- сброс всех изменений и «тихий час» (ТЗ 6.3, п.3 и п.5) -------------
    def reset_everything(self) -> list[str]:
        """Одна кнопка: вернуть питание CPU/GPU, DNS, правила, автоочистку, таймер и гамму."""
        import gd_extra
        import gd_journal
        import gd_rules
        import gd_toast

        gd_toast.suppress(True)          # не плодить по тосту на каждый откат
        done: list[str] = []

        power = getattr(self, "_power_page", None)
        if power is not None and getattr(power, "client", None) is not None:
            try:
                r = power.client.call({"cmd": "restore"})
                if r.get("ok"):
                    done.append("питание CPU")
                    power._active_profile = None
            except Exception:  # noqa: BLE001
                pass

        gpu = getattr(self, "_gpu_page", None)
        if gpu is not None and getattr(gpu, "client", None) is not None:
            try:
                r = gpu.client.call({"cmd": "restore"})
                if r.get("ok"):
                    done.append("лимит GPU")
                    gpu._active_profile = None
            except Exception:  # noqa: BLE001
                pass

        dns = getattr(self, "_dns_page", None)
        if dns is not None:
            try:
                dns._restore()
                done.append("DNS")
            except Exception:  # noqa: BLE001
                pass

        try:
            rules = gd_rules.load_rules()
            if any(r.get("enabled") for r in rules):
                for r in rules:
                    r["enabled"] = False
                gd_rules.save_rules(rules)
                done.append(i18n.t("правила ({v0})", v0=len(rules)))
        except Exception:  # noqa: BLE001
            pass

        utils = getattr(self, "_utils_page", None)
        if utils is not None:
            try:
                utils._load_auto()
                if utils.chk_auto.isChecked():
                    utils.chk_auto.setChecked(False)
                    utils._save_auto()
                    done.append("автоочистка ОЗУ")
            except Exception:  # noqa: BLE001
                pass

        try:
            if (gd_extra.timer_ms() or 99) < 1.0:
                gd_extra.set_timer_resolution(False)
                done.append("точность таймера")
        except Exception:  # noqa: BLE001
            pass

        try:
            self.gamma_controller.reset()
            done.append("гамма")
        except Exception:  # noqa: BLE001
            pass

        try:
            gd_journal.record("reset", "все изменения Gammadrilon", None,
                              ", ".join(done) or "нечего", why="сброс всех изменений",
                              source="утилиты")
        except Exception:  # noqa: BLE001
            pass
        gd_toast.suppress(False)
        gd_toast.show_toast(
            i18n.t("toast.reset",
                   what=", ".join(done) if done else i18n.t("toast.reset_nothing")),
            "ok" if done else "info")
        return done

    def start_quiet_hour(self):
        """Планировщик «Тихий час»: проверка раз в 30 с."""
        self._quiet_prev = None
        self._quiet_timer = QTimer(self)
        self._quiet_timer.timeout.connect(self._quiet_tick)
        self._quiet_timer.start(30000)
        self._quiet_tick()

    def _quiet_tick(self):
        import gd_profiles

        q = gd_profiles.load_quiet()
        inside = gd_profiles.in_window(q)
        if inside and not q.get("applied"):
            self._quiet_apply(q)
        elif not inside and q.get("applied"):
            self._quiet_release(q)

    def _quiet_apply(self, q: dict):
        import gd_journal
        import gd_profiles

        gc = self.gamma_controller
        self._quiet_prev = {"brightness": getattr(gc, "brightness", 1.0),
                            "contrast": getattr(gc, "contrast", 1.0),
                            "gamma": getattr(gc, "gamma", 1.0),
                            "temperature": getattr(gc, "temperature", 6500)}
        try:
            self.apply_values(0.5, self._quiet_prev["contrast"],
                              self._quiet_prev["gamma"],
                              self._quiet_prev["temperature"], save=False)
        except Exception:  # noqa: BLE001
            pass
        power = getattr(self, "_power_page", None)
        if power is not None:
            try:
                power.apply_profile("eco", source="тихий час")
            except Exception:  # noqa: BLE001
                pass
        q["applied"] = True
        gd_profiles.save_quiet(q)
        try:
            import gd_toast

            gd_toast.show_toast(i18n.t("toast.quiet_on"), "info")
        except Exception:  # noqa: BLE001
            pass
        try:
            gd_journal.record("quiet_hour", f"{q.get('start')}–{q.get('end')}", None,
                              "ЭКО CPU + минимум яркости", why="тихий час",
                              source="утилиты")
        except Exception:  # noqa: BLE001
            pass

    def _quiet_release(self, q: dict):
        import gd_journal
        import gd_profiles

        prev = self._quiet_prev or {"brightness": 1.0, "contrast": 1.0,
                                    "gamma": 1.0, "temperature": 6500}
        try:
            self.apply_values(prev["brightness"], prev["contrast"], prev["gamma"],
                              prev["temperature"], save=False)
        except Exception:  # noqa: BLE001
            pass
        power = getattr(self, "_power_page", None)
        if power is not None and getattr(power, "client", None) is not None:
            try:
                power.client.call({"cmd": "restore"})
                power._active_profile = None
            except Exception:  # noqa: BLE001
                pass
        q["applied"] = False
        gd_profiles.save_quiet(q)
        self._quiet_prev = None
        try:
            import gd_toast

            gd_toast.show_toast(i18n.t("toast.quiet_off"), "info")
        except Exception:  # noqa: BLE001
            pass
        try:
            gd_journal.record("quiet_hour", "выход из окна", "ЭКО + минимум яркости",
                              None, why="тихий час завершён", source="утилиты")
        except Exception:  # noqa: BLE001
            pass

    def open_rules_tab(self):
        """Открыть вкладку «Правила» в главном окне."""
        self.tabs.setCurrentWidget(self._rules_page)
        self.show_window()

    def open_gpu_tab(self):
        """Открыть вкладку «Питание GPU» в главном окне."""
        self.tabs.setCurrentWidget(self._gpu_page)
        self.show_window()

    def open_power_window(self):
        """Открыть вкладку «Питание CPU» в главном окне."""
        self.tabs.setCurrentWidget(self._power_page)
        self.show_window()

    def bind_hotkeys(self):
        self.hotkey_manager.nextPreset.connect(self.next_preset)
        self.hotkey_manager.prevPreset.connect(self.prev_preset)
        self.hotkey_manager.resetRequested.connect(self.reset_all)
        self.hotkey_manager.catVisionRequested.connect(self.toggle_cat_vision)
        self.hotkey_manager.presetRequested.connect(self.apply_preset_by_index)
        self.hotkey_manager.paletteRequested.connect(self.open_palette)
        self.hotkey_manager.error.connect(self.set_status)

        self.hotkey_manager.register(self.config)

    def values_from_ui(self) -> tuple[float, float, float, int]:
        brightness = self.brightness_slider.value() / 100.0
        contrast = self.contrast_slider.value() / 100.0
        gamma = self.gamma_slider.value() / 100.0
        temperature = self.temperature_slider.value()

        return brightness, contrast, gamma, temperature

    def set_ui_values(
        self,
        brightness: float,
        contrast: float,
        gamma: float,
        temperature: int,
        apply_now: bool = True,
        save: bool = True,
    ):
        brightness = clamp(brightness, 0.5, 2.0)
        contrast = clamp(contrast, 0.3, 2.0)
        gamma = clamp(gamma, 0.5, 2.5)
        temperature = int(clamp(temperature, 3000, 10000))

        sliders = (
            self.brightness_slider,
            self.contrast_slider,
            self.gamma_slider,
            self.temperature_slider,
        )

        for slider in sliders:
            slider.blockSignals(True)

        self.brightness_slider.setValue(int(round(brightness * 100)))
        self.contrast_slider.setValue(int(round(contrast * 100)))
        self.gamma_slider.setValue(int(round(gamma * 100)))
        self.temperature_slider.setValue(int(temperature))

        for slider in sliders:
            slider.blockSignals(False)

        self.update_value_labels()

        if apply_now:
            self.apply_values(brightness, contrast, gamma, temperature, save=save)

    def update_value_labels(self):
        brightness, contrast, gamma, temperature = self.values_from_ui()

        self.set_value_text(self.brightness_value, int(round(brightness * 100)), "%")
        self.set_value_text(self.contrast_value, int(round(contrast * 100)), "%")
        self.set_value_text(self.gamma_value, int(round(gamma * 100)), "%", big=True)
        self.set_value_text(self.temperature_value, int(temperature), "K")

        self.gamma_scale.set_current(int(round(gamma * 100)))
        self.curve.set_values(brightness, contrast, gamma, temperature)

    def on_slider_changed(self):
        self.update_value_labels()

        brightness, contrast, gamma, temperature = self.values_from_ui()
        self.apply_values(brightness, contrast, gamma, temperature, save=True)

    def apply_values(
        self,
        brightness: float,
        contrast: float,
        gamma: float,
        temperature: int,
        save: bool = True,
    ):
        ok = self.gamma_controller.apply(brightness, contrast, gamma, temperature)

        if save:
            self.config["settings"]["current"] = {
                "brightness": round(brightness, 4),
                "contrast": round(contrast, 4),
                "gamma": round(gamma, 4),
                "temperature": int(temperature),
            }
            save_config(self.config)

        if not ok:
            self.set_status(
                i18n.t("Ошибка Gamma API. Попробуйте запустить от имени администратора.")
            )


    def refresh_preset_combo(self):
        if not hasattr(self, "preset_combo"):
            return

        current_index = self.preset_combo.currentIndex()

        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()

        for preset in self.config["presets"]:
            name = preset.get("name", "Unnamed")
            hotkey = preset.get("hotkey", "")

            if hotkey:
                self.preset_combo.addItem(f"{name}  [{hotkey}]")
            else:
                self.preset_combo.addItem(name)

        if self.preset_combo.count() > 0:
            safe_index = max(0, min(current_index, self.preset_combo.count() - 1))
            self.preset_combo.setCurrentIndex(safe_index)

        self.preset_combo.blockSignals(False)

    def on_preset_selected(self, index: int):
        if index < 0:
            return

        self.apply_preset_by_index(index)

    def apply_preset_by_index(self, index: int):
        presets = self.config["presets"]

        if not presets:
            return

        if index < 0 or index >= len(presets):
            return

        self.current_preset_index = index

        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)

        preset = presets[index]

        self.animate_to(
            preset.get("brightness", 1.0),
            preset.get("contrast", 1.0),
            preset.get("gamma", 1.0),
            preset.get("temperature", 6500),
            duration_ms=700,
            save_at_end=True,
        )

        self.set_status(i18n.t("Пресет: {v0}", v0=preset.get("name", "Unnamed")))

    def next_preset(self):
        presets = self.config["presets"]

        if not presets:
            return

        self.current_preset_index += 1

        if self.current_preset_index >= len(presets):
            self.current_preset_index = 0

        self.apply_preset_by_index(self.current_preset_index)

    def prev_preset(self):
        presets = self.config["presets"]

        if not presets:
            return

        self.current_preset_index -= 1

        if self.current_preset_index < 0:
            self.current_preset_index = len(presets) - 1

        self.apply_preset_by_index(self.current_preset_index)

    def save_as_preset(self):
        name, ok = QInputDialog.getText(
            self,
            i18n.t("Сохранить пресет"),
            i18n.t("Имя пресета:"),
        )

        if not ok:
            return

        name = name.strip()

        if not name:
            return

        brightness, contrast, gamma, temperature = self.values_from_ui()

        preset = {
            "name": name,
            "brightness": round(brightness, 4),
            "contrast": round(contrast, 4),
            "gamma": round(gamma, 4),
            "temperature": int(temperature),
            "hotkey": "",
        }

        self.config["presets"].append(preset)
        save_config(self.config)

        self.refresh_preset_combo()
        self.preset_combo.setCurrentIndex(len(self.config["presets"]) - 1)

        self.hotkey_manager.register(self.config)
        self.set_status(i18n.t("Пресет сохранён: {v0}", v0=name))

    def delete_current_preset(self):
        index = self.preset_combo.currentIndex()

        if index < 0 or index >= len(self.config["presets"]):
            return

        if len(self.config["presets"]) <= 1:
            QMessageBox.warning(
                self,
                i18n.t("Невозможно удалить"),
                i18n.t("Нельзя удалить последний пресет."),
            )
            return

        name = self.config["presets"][index].get("name", "Unnamed")

        answer = QMessageBox.question(
            self,
            i18n.t("Удалить пресет"),
            i18n.t("Удалить пресет «{v0}»?", v0=name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        del self.config["presets"][index]

        self.current_preset_index = max(
            0,
            min(index, len(self.config["presets"]) - 1),
        )

        save_config(self.config)

        self.refresh_preset_combo()
        self.preset_combo.setCurrentIndex(self.current_preset_index)

        self.hotkey_manager.register(self.config)
        self.set_status(i18n.t("Пресет удалён: {v0}", v0=name))

    def set_current_preset_hotkey(self):
        index = self.preset_combo.currentIndex()

        if index < 0 or index >= len(self.config["presets"]):
            return

        preset = self.config["presets"][index]

        hotkey, ok = QInputDialog.getText(
            self,
            i18n.t("Клавиша пресета"),
            i18n.t(
                "Горячая клавиша для «{v0}»: пример ctrl+alt+1",
                v0=preset.get("name", "Unnamed"),
            ),
            text=preset.get("hotkey", ""),
        )

        if not ok:
            return

        preset["hotkey"] = hotkey.strip()

        save_config(self.config)
        self.refresh_preset_combo()
        self.preset_combo.setCurrentIndex(index)

        self.hotkey_manager.register(self.config)
        self.set_status(i18n.t("Горячая клавиша пресета сохранена"))

    def edit_global_hotkeys(self):
        hotkeys = self.config["settings"].get("hotkeys", {})
        dialog = HotkeySettingsDialog(hotkeys, self)

        if dialog.exec_() == QDialog.Accepted:
            self.config["settings"]["hotkeys"] = dialog.result_hotkeys()
            save_config(self.config)
            self.hotkey_manager.register(self.config)
            self.set_status(i18n.t("Глобальные клавиши обновлены"))

    def open_support_link(self):
        QDesktopServices.openUrl(QUrl(SUPPORT_URL))

    def open_second_link(self):
        QDesktopServices.openUrl(QUrl(SECOND_URL))

    def reset_all(self):
        self.cat_active = False
        self.cat_button.setChecked(False)
        self.cat_timer.stop()
        self.cat_previous_values = None

        self.animate_to(
            1.0,
            1.0,
            1.0,
            6500,
            duration_ms=450,
            save_at_end=True,
        )

        self.set_status(i18n.t("Сброс"))

    def toggle_cat_vision(self):
        if self.cat_active:
            self.stop_cat_vision()
            return

        self.cat_active = True
        self.cat_button.setChecked(True)
        self.cat_previous_values = self.values_from_ui()

        brightness, contrast, gamma, temperature = self.values_from_ui()

   
        cat_gamma = 2.2

        self.animate_to(
            brightness,
            contrast,
            cat_gamma,
            temperature,
            duration_ms=350,
            save_at_end=False,
    )


    def stop_cat_vision(self):
        if not self.cat_active:
            return

        self.cat_active = False
        self.cat_button.setChecked(False)

        if self.cat_previous_values:
            brightness, contrast, gamma, temperature = self.cat_previous_values

            self.animate_to(
                brightness,
                contrast,
                gamma,
                temperature,
                duration_ms=450,
                save_at_end=False,
            )

        self.cat_previous_values = None
        self.set_status(i18n.t("Кошачье зрение: выключено"))


    def animate_to(
        self,
        brightness: float,
        contrast: float,
        gamma: float,
        temperature: int,
        duration_ms: int = 700,
        save_at_end: bool = True,
    ):
        start_brightness, start_contrast, start_gamma, start_temperature = self.values_from_ui()

        self.animation_data = {
            "start_time": time.perf_counter(),
            "duration": max(100, duration_ms) / 1000.0,
            "start": (
                start_brightness,
                start_contrast,
                start_gamma,
                start_temperature,
            ),
            "end": (
                clamp(brightness, 0.5, 2.0),
                clamp(contrast, 0.3, 2.0),
                clamp(gamma, 0.5, 2.5),
                int(clamp(temperature, 3000, 10000)),
            ),
            "save_at_end": save_at_end,
        }

        self.gamma_animation_timer.start(16)

    def animation_step(self):
        if not self.animation_data:
            self.gamma_animation_timer.stop()
            return

        elapsed = time.perf_counter() - self.animation_data["start_time"]
        duration = self.animation_data["duration"]

        progress = clamp(elapsed / duration, 0.0, 1.0)

        # Smoothstep easing
        eased = progress * progress * (3 - 2 * progress)

        start_b, start_c, start_g, start_t = self.animation_data["start"]
        end_b, end_c, end_g, end_t = self.animation_data["end"]

        brightness = start_b + (end_b - start_b) * eased
        contrast = start_c + (end_c - start_c) * eased
        gamma = start_g + (end_g - start_g) * eased
        temperature = int(start_t + (end_t - start_t) * eased)

        self.set_ui_values(
            brightness,
            contrast,
            gamma,
            temperature,
            apply_now=True,
            save=False,
        )

        if progress >= 1.0:
            self.gamma_animation_timer.stop()

            save_at_end = bool(self.animation_data.get("save_at_end", True))

            self.set_ui_values(
                end_b,
                end_c,
                end_g,
                int(end_t),
                apply_now=True,
                save=save_at_end,
            )

            self.animation_data = None


    def set_status(self, text: str):
        self.status_label.setText(text)

    def on_close_on_x_changed(self, state):
        self.config["settings"]["close_on_x"] = state == Qt.Checked
        save_config(self.config)

        if state == Qt.Checked:
            self.set_status(i18n.t("Выход из приложения"))
        else:
            self.set_status(i18n.t("Свернуть в трей при закрытии окна"))

    def on_start_with_windows_changed(self, state):
        enabled = state == Qt.Checked
        self.config["settings"]["start_with_windows"] = enabled
        save_config(self.config)

        try:
            if enabled:
                enable_startup()
                self.set_status(i18n.t("Автозапуск включён"))
            else:
                disable_startup()
                self.set_status(i18n.t("Автозапуск выключен"))
        except Exception as e:
            QMessageBox.warning(self, i18n.t("Ошибка автозапуска"), str(e))

    def closeEvent(self, event):
        if self.is_quitting:
            event.accept()
            return

        close_on_x = bool(self.config.get("settings", {}).get("close_on_x", False))

        if close_on_x:
            event.accept()
            self.quit_app()
            return

        event.ignore()
        self.hide()

    def quit_app(self):
        self.is_quitting = True

        try:
            self.hotkey_manager.clear()
        except Exception:
            pass

        try:
            self.gamma_controller.reset()
        except Exception:
            pass

        # откат профиля питания (если вкладка «Питание CPU» использовалась)
        try:
            if getattr(self, "_power_page", None) is not None:
                self._power_page.client.shutdown(restore=True)
        except Exception:
            pass

        # остановка GPU-хелпера с откатом лимита мощности
        try:
            if getattr(self, "_gpu_page", None) is not None:
                self._gpu_page.client.shutdown(restore=True)
        except Exception:
            pass

        QApplication.quit()

_APP_QSS = """
/* --- каркас «Пульт»: титлбар, сайдбар, статусбар (вариант A, монохром) --- */
QWidget#Central {
    background: @PAPER@;
}

QStackedWidget#Pages,
QWidget#PanelRoot {
    background: @PAPER@;
}

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
    font-size: 12.5px;
    font-weight: 600;
}

QLabel#BrandVer {
    color: @MUTED@;
    font-size: 10px;
}

QPushButton#ModeChip {
    background: @PRESSED@;
    border: 1px solid @TRACK@;
    border-radius: 13px;
    padding: 0 10px;
    color: @INK2@;
    font-size: 11.5px;
    min-height: 26px;
}

QPushButton#ModeChip:hover {
    border-color: @MUTED@;
    color: @INK@;
}

QPushButton#TitleBtn {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}

QPushButton#TitleBtn:hover {
    background: @PRESSED@;
    border-color: @RULE@;
}

QPushButton#WinMin,
QPushButton#WinMax {
    background: transparent;
    border: none;
    color: @MUTED@;
    font-size: 12px;
}

QPushButton#WinMin:hover,
QPushButton#WinMax:hover {
    background: @PRESSED@;
    color: @INK@;
}

QPushButton#WinClose {
    background: transparent;
    border: none;
    color: @MUTED@;
    font-size: 12px;
}

QPushButton#WinClose:hover {
    background: @ACCENT@;
    color: @ON_ACCENT@;
}

QWidget#SidebarWrap {
    background: @SURFACE@;
    border-right: 1px solid @RULE@;
}

QWidget#Sidebar {
    background: @SURFACE@;
}

QLabel#SideGroup {
    color: @MUTED@;
    font-size: 10.5px;
    font-weight: 600;
    padding: 14px 6px 6px 6px;
}

QWidget#NavItem {
    border-radius: 8px;
    border: 1px solid transparent;
}

QWidget#NavItem:hover {
    background: @PRESSED@;
}

QWidget#NavItem[active="true"] {
    background: @PRESSED@;
    border: 1px solid @RULE@;
    border-left: 3px solid @ACCENT@;
}

QLabel#NavText {
    color: @INK2@;
    font-size: 12.5px;
    background: transparent;
}

QWidget#NavItem[active="true"] QLabel#NavText {
    color: @INK@;
}

QLabel#NavBadge {
    color: @MUTED@;
    font-size: 10.5px;
    border: 1px solid @RULE@;
    border-radius: 5px;
    padding: 1px 6px;
    background: transparent;
}

QWidget#SideFoot {
    background: @SURFACE@;
    border-top: 1px solid @RULE@;
}

QFrame#FootLed {
    background: @ACCENT@;
    border-radius: 3px;
}

QLabel#FootText {
    color: @MUTED@;
    font-size: 11px;
}

QWidget#StatusBar {
    background: @SURFACE@;
    border-top: 1px solid @RULE@;
}

QWidget#PageHead {
    background: @PAPER@;
}

QScrollArea#PageScroll {
    background: @PAPER@;
    border: none;
}

QScrollArea#PageScroll > QWidget > QWidget {
    background: @PAPER@;
}

QLabel#PageTitle {
    color: @INK@;
    font-size: 20px;
    font-weight: 600;
}

QLabel#PageSub {
    color: @MUTED@;
    font-size: 12px;
}

QLabel#Value {
    color: @INK@;
    font-size: 26px;
    font-weight: 600;
}

QLabel#BigValue {
    color: @INK@;
    font-size: 26px;
    font-weight: 600;
}

QLabel#StatusItem {
    color: @MUTED@;
    font-size: 12px;
}

QLabel#StatusHint {
    color: @MUTED@;
    font-size: 11.5px;
}

QFrame#StatusSep {
    color: @RULE@;
    background: @RULE@;
    border: none;
    max-width: 1px;
}

QScrollArea#UtilitiesScrollArea {
    background: @PAPER@;
    border: none;
}

QWidget#Root {
    background: @PAPER@;
    color: @INK2@;
    font-family: "Segoe UI Variable Text", "Segoe UI", Arial;
    font-size: 13px;
}

QStackedWidget {
    background: @PAPER@;
}

QDialog {
    background: @PAPER@;
    color: @INK2@;
    font-family: "Segoe UI Variable Text", "Segoe UI", Arial;
    font-size: 13px;
}

QLabel {
    color: @INK2@;
    background: transparent;
}

QLabel#AppTitle {
    font-family: "Bahnschrift", "Segoe UI", Arial;
    font-size: 17px;
    font-weight: 600;
    color: @INK@;
}

QLabel#Version {
    color: @MUTED@;
    font-size: 10px;
}

QLabel#Status {
    color: @INK@;
    font-size: 11px;
    background: @SURFACE@;
    border: 1px solid @RULE@;
    border-radius: 8px;
    padding: 4px 8px;
}

QLabel#StatusDot {
    color: @ACCENT@;
    font-size: 10px;
}

QLabel#FallbackIcon {
    color: @INK@;
    font-size: 22px;
}

QLabel#SectionTitle {
    font-family: "Bahnschrift", "Segoe UI", Arial;
    font-size: 14px;
    font-weight: 600;
    color: @INK@;
}

QLabel#CardLabel {
    color: @MUTED@;
    font-size: 11px;
    font-weight: 600;
}

QLabel#BigValue,
QLabel#RowValue {
    font-family: "Bahnschrift", "Segoe UI", Arial;
    color: @INK@;
}

QLabel#Hint {
    color: @MUTED@;
    font-size: 11px;
}

QLabel#Metric {
    font-family: "Bahnschrift", "Segoe UI", Arial;
    font-size: 14px;
    font-weight: 600;
    color: @INK@;
}

QPushButton#DangerButton {
    background: @DANGER_BG@;
    color: @DANGER@;
    border: 1px solid @DANGER_BORDER@;
    border-radius: 6px;
    padding: 7px 14px;
    font-size: 11px;
    font-weight: 600;
}

QPushButton#DangerButton:hover {
    background: @DANGER_HOVER@;
    border-color: @DANGER_BORDER2@;
}

QLabel#DialogTitle {
    font-family: "Bahnschrift", "Segoe UI", Arial;
    font-size: 16px;
    font-weight: 600;
    color: @INK@;
}

QFrame#Card {
    background: @SURFACE@;
    border: 1px solid @RULE@;
    border-radius: 12px;
}

/* --- таблицы (макет A) --- */
QTableWidget {
    background: @SURFACE@;
    border: 1px solid @RULE@;
    border-radius: 8px;
    gridline-color: transparent;
    font-size: 12.5px;
    color: @INK2@;
    selection-background-color: @PRESSED@;
    selection-color: @INK@;
    outline: none;
}

QTableWidget::item {
    padding: 9px 10px;
    border-bottom: 1px solid @RULE@;
}

QTableWidget::item:hover {
    background: @PRESSED@;
}

QTableWidget::item:selected {
    background: @PRESSED@;
    color: @INK@;
    border-left: 2px solid @ACCENT@;
}

QHeaderView::section {
    background: @SURFACE@;
    color: @MUTED@;
    font-size: 10.5px;
    font-weight: 600;
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid @RULE@;
}

QTableCornerButton::section {
    background: @SURFACE@;
    border: none;
}

/* --- чекбоксы --- */
QCheckBox {
    color: @INK2@;
    font-size: 12.5px;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid @TRACK@;
    border-radius: 4px;
    background: @SURFACE@;
}

QCheckBox::indicator:hover {
    border-color: @MUTED@;
}

QCheckBox::indicator:checked {
    background: @ACCENT@;
    border-color: @ACCENT@;
}

QSlider::groove:horizontal {
    height: 6px;
    border-radius: 3px;
    background: @RULE@;
}

QSlider::sub-page:horizontal {
    background: @ACCENT@;
    border-radius: 3px;
}

QSlider::add-page:horizontal {
    background: @RULE@;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -8px 0;
    border-radius: 11px;
    background: @ACCENT@;
    border: 3px solid @PAPER@;
}

QSlider::handle:horizontal:hover {
    background: @INK@;
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

QPushButton {
    background: @SURFACE@;
    color: @INK2@;
    border: 1px solid @RULE@;
    border-radius: 8px;
    padding: 0 12px;
    font-size: 13px;
}

QPushButton:hover {
    background: @PRESSED@;
    border-color: @ACCENT@;
    color: @INK@;
}

QPushButton:pressed {
    background: @PRESSED@;
}

QPushButton#PrimaryButton {
    background: @NAVY@;
    border-color: @NAVY@;
    color: @ON_ACCENT@;
    font-weight: 600;
}

QPushButton#PrimaryButton:hover {
    background: @NAVY_HOVER@;
    border-color: @NAVY_HOVER@;
}

QPushButton#PrimaryButton:pressed {
    background: @NAVY_PRESSED@;
}

QPushButton#GhostButton {
    border-color: transparent;
    color: @MUTED@;
}

QPushButton#GhostButton:hover {
    border-color: @RULE@;
    color: @INK@;
}

QPushButton#ToggleButton {
    border-color: @ACCENT@;
    color: @ACCENT@;
}

QPushButton#ToggleButton:hover {
    background: @MID@;
}

QPushButton#ToggleButton:checked {
    background: @ACCENT@;
    border-color: @ACCENT@;
    color: @PAPER@;
    font-weight: 600;
}

QPushButton#WideButton {
    color: @INK@;
    font-weight: 600;
    font-size: 13px;
}

QPushButton#SmallButton {
    font-size: 11.5px;
    color: @INK2@;
}

QComboBox {
    background: @SURFACE@;
    color: @INK@;
    border: 1px solid @RULE@;
    border-radius: 8px;
    padding: 0 11px;
    font-size: 13px;
}

QComboBox:hover {
    border-color: @ACCENT@;
}

QComboBox::drop-down {
    border: none;
    width: 36px;
}

QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
}

QComboBox QAbstractItemView {
    background: @SURFACE@;
    color: @INK@;
    border: 1px solid @RULE@;
    border-radius: 9px;
    padding: 4px;
    outline: none;
    selection-background-color: @NAVY@;
    selection-color: @ON_ACCENT@;
}

QCheckBox {
    color: @INK2@;
    font-size: 13px;
    spacing: 9px;
    background: transparent;
}

QCheckBox:hover {
    color: @INK@;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid @RULE@;
    background: transparent;
}

QCheckBox::indicator:hover {
    border-color: @ACCENT@;
}

QCheckBox::indicator:checked {
    background: @ACCENT@;
    border: 1px solid @ACCENT@;
}

QCheckBox:disabled {
    color: @MUTED@;
}

QPushButton:disabled {
    color: @MUTED@;
    background: @PRESSED@;
    border-color: @RULE@;
}

QLineEdit {
    background: @SURFACE@;
    color: @INK@;
    border: 1px solid @RULE@;
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: @NAVY@;
}

QLineEdit:focus {
    border-color: @ACCENT@;
}

QLineEdit:disabled,
QComboBox:disabled {
    color: @MUTED@;
    background: @PRESSED@;
}

QTableWidget,
QListWidget {
    background: @DEEP@;
    color: @INK2@;
    border: 1px solid @RULE@;
    border-radius: 8px;
    gridline-color: @RULE@;
    selection-background-color: @NAVY@;
    selection-color: @ON_ACCENT@;
}

QHeaderView::section {
    background: @SURFACE@;
    color: @MUTED@;
    border: none;
    border-bottom: 1px solid @RULE@;
    padding: 7px 8px;
}

QAbstractScrollArea::viewport {
    background: transparent;
}

QScrollArea::viewport {
    background: @PAPER@;
}

QLineEdit#PaletteInput {
    font-size: 15px;
    padding: 12px;
}

QListWidget {
    background: @DEEP@;
    color: @INK2@;
    border: 1px solid @RULE@;
    border-radius: 9px;
    padding: 4px;
    outline: 0;
}

QListWidget::item {
    padding: 7px 8px;
    border-radius: 6px;
}

QListWidget::item:selected {
    background: @NAVY@;
    color: @ON_ACCENT@;
}

QListWidget#PaletteList::item {
    padding: 9px 10px;
    font-size: 13px;
}

QMenu {
    background: @SURFACE@;
    color: @INK2@;
    border: 1px solid @RULE@;
    padding: 4px;
}

QMenu::item {
    padding: 6px 18px;
    border-radius: 6px;
}

QMenu::item:selected {
    background: @NAVY@;
    color: @ON_ACCENT@;
}

QMenu::separator {
    height: 1px;
    background: @RULE@;
    margin: 4px 8px;
}

QScrollBar:vertical {
    width: 10px;
    background: @PAPER@;
    margin: 2px;
}

QScrollBar::handle:vertical {
    min-height: 28px;
    border-radius: 4px;
    background: @TRACK@;
}

QScrollBar::handle:vertical:hover {
    background: @ACCENT@;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}

/* --- доступность: видимый фокус для клавиатурной навигации (ТЗ 5) --- */
QPushButton:focus,
QPushButton:default {
    border: 1px solid @ACCENT@;
}

QPushButton#PrimaryButton:focus,
QPushButton#DangerButton:focus {
    border: 2px solid @ACCENT@;
}

QPushButton#Segment:focus {
    border: 1px solid @ACCENT@;
}

QCheckBox:focus {
    color: @INK@;
}

QCheckBox:focus::indicator {
    border-color: @ACCENT@;
}

QComboBox:focus {
    border-color: @ACCENT@;
}

QLineEdit:focus {
    border-color: @ACCENT@;
}

QSlider:focus {
    border: 1px solid @ACCENT@;
    border-radius: 4px;
}

QTableWidget:focus,
QListWidget:focus {
    border: 1px solid @ACCENT@;
}

QTabBar::tab:focus {
    color: @INK@;
    border-bottom: 2px solid @ACCENT@;
}
"""


def build_app_stylesheet() -> str:
    """QSS главного окна из активной палитры (плейсхолдеры @KEY@)."""
    qss = _APP_QSS
    for key, value in th.active().items():
        qss = qss.replace(f"@{key}@", value)
    return qss


APP_STYLESHEET = build_app_stylesheet()

def main():
    if sys.platform != "win32":
        print("This application works only on Windows.")
        return
    
    if is_another_instance_running():
        return
    
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    app.setQuitOnLastWindowClosed(False)

    icon_path = resource_path(ICON_FILE)

    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()

    if window.config.get("settings", {}).get("start_with_windows", False):
        try:
            enable_startup()
        except Exception:
            pass
        
    code = app.exec_()

    try:
        window.hotkey_manager.clear()
    except Exception:
        pass

    sys.exit(code)


if __name__ == "__main__":
    # режимы компаньонов для собранного exe: Gammadrilon.exe --power-service / --gpu-service
    if len(sys.argv) > 1 and sys.argv[1] in ("--power-service", "--gpu-service"):
        mode = sys.argv.pop(1)
        if mode == "--power-service":
            import gd_power_service

            sys.exit(gd_power_service.main())
        import gd_gpu_service

        sys.exit(gd_gpu_service.main())
    main()