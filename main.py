import ctypes
import json
import math
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

import keyboard

import gd_proc


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
    QPushButton,
    QSizePolicy,
    QSlider,
    QSystemTrayIcon,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)


APP_NAME = "Gammadrilon"
APP_VERSION = "v3.1"
CONFIG_FILE = "presets.json"
ICON_FILE = "icon.ico"

#: итог чтения конфига: None — всё в порядке; "backup"/"no_backup" — файл был
#: битым и заменён дефолтом (окно честно скажет об этом при старте)
CONFIG_RECOVERY: str | None = None

# --- палитра (из gd_theme: тёмная/светлая, ТЗ 5) -------------------------------
import gd_theme as th  # noqa: E402
import gd_i18n as i18n  # noqa: E402
import gd_dialog  # noqa: E402
import gd_icons  # noqa: E402
import gd_agent  # noqa: E402
import gd_perf  # noqa: E402
import gd_display  # noqa: E402
import gd_update  # noqa: E402

SLIDER_GROOVE_HEIGHT = 6
SLIDER_HANDLE_SIZE = 22

# сторож гаммы: система может сбросить рампу (сон, блокировка, смена режима) —
# раз в несколько секунд сверяем факт с целью и возвращаем своё. Порог в
# единицах канала (0..65535): сброс к «ровной» кривой даёт тысячи, квантование
# драйвера — десятки.
GAMMA_WATCH_INTERVAL_MS = 4000
GAMMA_WATCH_TOLERANCE = 2000

# запись конфига откладывается и схлопывается: перетаскивание слайдера иначе
# писало бы файл на каждый тик (диск + антивирус на каждое переименование).
# Выход из приложения сбрасывает отложенное немедленно.
CONFIG_SAVE_DELAY_MS = 400

# иконки пунктов сайдбара (gd_icons)
SIDE_ICONS = {
    "tab.cpu": "cpu",
    "tab.gpu": "gpu",
    "tab.gamma": "gamma",
    "tab.rules": "rules",
    "tab.utils": "utils",
    "tab.dns": "dns",
    "tab.zapret": "zapret",
    "tab.agent": "api",
}

# --- устойчивость: лог падений и глобальные обработчики -----------------------
_CRASH_KEEP = []   # держим файл-дескриптор живым: faulthandler пишет в него
_CRASH_LOG_MAX_BYTES = 512 * 1024   # лог не растёт бесконечно: обрезаем при старте
_INSTANCE_MUTEX = []   # хендл мьютекса «один экземпляр»: держим живым


def _crash_log_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base / "gammadrilon.log"


def _trim_crash_log(path: Path) -> None:
    """Оставить последние ~512 КБ лога падений.

    Лог открывается на дозапись и живёт до конца процесса, поэтому чистить его
    раньше было некому: у пользователя, который ловит редкий краш месяцами,
    файл рос без предела. Обрезаем при старте — историю последних падений
    сохраняем, хвост прошлых запусков теряем (он и так не нужен).
    """
    try:
        if path.stat().st_size <= _CRASH_LOG_MAX_BYTES:
            return
        with path.open("rb") as fh:
            fh.seek(-_CRASH_LOG_MAX_BYTES // 2, os.SEEK_END)
            tail = fh.read()
        path.write_bytes(tail)
    except OSError:
        pass


def install_crash_handlers() -> None:
    """Сделать краши видимыми: traceback в файл + (по возможности) диалог.

    До этого исключение в фоновом потоке или падение нативного кода завершали
    процесс молча: пользователь видел только «приложение закрылось». Теперь
    faulthandler пишет стек всех потоков, а sys/threading.excepthook —
    Python-исключения (и показывает их в монохромном диалоге gd_dialog,
    если GUI ещё жив).
    """
    import faulthandler
    import threading
    import traceback

    log = None
    try:
        _path = _crash_log_path()
        _trim_crash_log(_path)
        log = open(_path, "a", encoding="utf-8")
        log.write(f"\n=== запуск {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")  # i18n: skip (лог-файл)
        log.flush()
        faulthandler.enable(file=log, all_threads=True)
        _CRASH_KEEP.append(log)
    except Exception:  # noqa: BLE001
        log = None

    def _report(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        if log is not None:
            try:
                log.write(text)
                log.flush()
            except Exception:  # noqa: BLE001
                pass
        try:
            if QApplication.instance() is not None:
                import gd_dialog

                gd_dialog.notify(
                    None,
                    APP_NAME,
                    f"{i18n.t('Непредвиденная ошибка')}:\n{exc}\n\n"
                    f"{i18n.t('Лог падений')}: {_crash_log_path()}",
                )
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = _report

    def _thread_hook(args) -> None:
        _report(args.exc_type, args.exc_value, args.exc_traceback)

    try:
        threading.excepthook = _thread_hook
    except Exception:  # noqa: BLE001
        pass



UI_FONT = '"Segoe UI Variable Text", "Segoe UI", Arial'
DIGIT_FONT = '"Bahnschrift", "Segoe UI", Arial'

# Рублей бы.
SUPPORT_URL = "https://boosty.to/bonzeros/donate"
SECOND_URL = "https://github.com/bonzeros/Gammadrilon"

def is_another_instance_running() -> bool:
    """Второй экземпляр уже запущен?

    Мьютекс живёт в ``Local\\`` — в пространстве сессии пользователя. ``Global\\``
    требует привилегии ``SeCreateGlobalPrivilege`` (её нет у обычной учётки),
    создание там молча падало, и приложение стартовало вторым окном. Хендл
    сохраняем в модульном списке: пока он открыт, мьютекс существует (закрытие
    или сборка мусором сняли бы защиту).

    Если мьютекс не создался вообще, запуск не блокируем: не пустить человека в
    приложение из-за неудачной проверки хуже, чем допустить второе окно.
    """
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}_mutex")
    if not handle:
        return False

    _INSTANCE_MUTEX.append(handle)
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def _warn_already_running() -> None:
    """Сказать «Gammadrilon уже запущен» и не оставить человека в недоумении.

    Второй экземпляр выходил молча: с точки зрения пользователя двойной клик по
    ярлыку «ничего не делал». Qt здесь ещё не поднят, поэтому говорим нативным
    MessageBox — он не требует QApplication.
    """
    text = i18n.t("Gammadrilon уже запущен — окно живёт в трее (рядом с часами).")
    try:
        user32 = ctypes.windll.user32
        user32.MessageBoxW.argtypes = [
            wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT,
        ]
        user32.MessageBoxW.restype = ctypes.c_int
        # 0x40 = MB_ICONINFORMATION, 0x10000 = MB_SETFOREGROUND (показать поверх)
        user32.MessageBoxW(None, text, f"{APP_NAME} {APP_VERSION}", 0x40 | 0x10000)
        return
    except Exception:  # noqa: BLE001
        pass
    print(f"{APP_NAME} {APP_VERSION}: {text}")


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


def merge_known_into(defaults: dict, incoming: dict, skipped: list[str], prefix: str = "") -> None:
    """Слить ``incoming`` в ``defaults``, не выходя за структуру умолчаний.

    Настройки приезжают из чужого файла: незнакомые ключи и значения чужого типа
    отбрасываем — они не должны ломать приложение (например, строкой там, где
    ждут число). Пропущенное копится в ``skipped``: импорт обязан сказать, что
    именно он не взял, а не сделать вид, что перенёс всё.
    """
    for key, value in incoming.items():
        name = prefix + str(key)
        if key not in defaults:
            skipped.append(name)
            continue
        ref = defaults[key]
        if isinstance(ref, dict):
            if isinstance(value, dict):
                merge_known_into(ref, value, skipped, name + ".")
            else:
                skipped.append(name)
        elif isinstance(ref, list):
            if isinstance(value, list):
                defaults[key] = value
            else:
                skipped.append(name)
        elif ref is None:
            if value is None or isinstance(value, (str, int, float, bool)):
                defaults[key] = value
            else:
                skipped.append(name)
        elif isinstance(ref, bool) or isinstance(value, bool):
            # bool — подкласс int: True вместо числа здесь означал бы «1», и
            # проверка типов ниже пропустила бы такую подмену молча
            if isinstance(ref, bool) and isinstance(value, bool):
                defaults[key] = value
            else:
                skipped.append(name)
        elif isinstance(value, (int, float)) and isinstance(ref, (int, float)):
            defaults[key] = value
        elif isinstance(ref, str) and isinstance(value, str):
            defaults[key] = value
        else:
            skipped.append(name)


def safe_float(value, fallback: float) -> float:
    """float без исключения: конфиг правит и пользователь, а не только код."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def safe_int(value, fallback: int) -> int:
    """int без исключения: конфиг правит и пользователь, а не только код."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback

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
        # лимит GPU после закрытия приложения: True — остаётся на карте
        # (до перезагрузки или кнопки «Вернуть как было»), False — возвращается
        "gpu_keep_on_exit": True,
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
            "perf_toggle": "ctrl+alt+p",
        },
        # переключатель производительности (клавиша perf_toggle): что применять,
        # когда ограничения включаются («limited») и когда снимаются («boost»).
        # Пустой профиль в блоке = «этот блок не трогаем».
        "perf_profiles": {
            "limited": {"cpu": "eco", "gpu": "quiet"},
            "boost": {"cpu": "normal", "gpu": "default"},
        },
        # состояние переключателя: True — ограничения включены
        "perf_limited": False,
        "perf_sound": True,
        # автолимит GPU: план (последнее подтверждённое значение) и галочка
        # «применять при запуске» (приложение вернёт план после перезагрузки)
        "gpu_auto_pl": None,
        "gpu_apply_on_start": False,
        # проверка обновлений ходит в сеть — по умолчанию выключена (обещание
        # «без облака и телеметрии» остаётся в силе, пока галочку не поставят)
        "check_updates": False,
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

#: Настройки, которые приложение пишет в конфиг уже по ходу работы: в
#: DEFAULT_CONFIG их нет (свежий конфиг не должен притворяться настроенным), но
#: перенос настроек обязан знать их в лицо — иначе язык, тема, путь zapret и
#: локальный API объявлялись бы «незнакомыми ключами». Значения здесь — ровно
#: то, что подставляет код, когда ключа нет (``.get(key, ...)``).
RUNTIME_SETTINGS: dict = {
    "language": None,          # None — язык по системе (см. i18n.detect)
    "theme": "dark",
    "agent_api": False,
    "zapret_path": "",
}

#: Ключи, которые переносить нельзя: это состояние этой машины и этого
#: человека, а не настройка («первый запуск пройден здесь» — и должен остаться).
SETTINGS_NOT_TRANSFERABLE = ("first_run_done",)


def save_config(data: dict) -> bool:
    """Записать конфиг атомарно: обрыв записи не оставит обрезанный JSON.

    Пишем во временный файл рядом и заменяем им presets.json (`os.replace` на
    NTFS атомарен), с fsync — чтобы переименование не обогнало данные при
    внезапном выключении. Ошибки файловой системы не бросаются наружу: из слота
    Qt необработанное исключение завершает приложение (PyQt5), а занятый чужим
    редактором файл — не повод падать. Честный ответ — False; вызывающий сам
    решает, что сказать пользователю.
    """
    path = config_path()
    tmp = path.with_name(path.name + ".tmp")

    try:
        with tmp.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


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
        data = None

    if not isinstance(data, dict):
        # битый файл (не JSON или не объект — список, строка) не выбрасываем:
        # рядом остаётся копия, а окно честно скажет о замене — иначе пресеты
        # «тихо исчезают»
        global CONFIG_RECOVERY
        try:
            path.replace(path.with_name(path.name + ".bak"))
            CONFIG_RECOVERY = "backup"
        except OSError:
            CONFIG_RECOVERY = "no_backup"
        data = json.loads(json.dumps(DEFAULT_CONFIG))
        save_config(data)
        return data

    # файл нормализуется (недостающие ключи, клампы) — запись только если что-то
    # действительно изменилось: иначе каждый старт переписывал бы presets.json
    before = json.dumps(data, sort_keys=True, ensure_ascii=False)

    # форма файла могла пострадать от ручной правки: приводим к ожидаемой,
    # иначе старт падал бы AttributeError на первом же setdefault
    if not isinstance(data.get("settings"), dict):
        data["settings"] = {}
    if not isinstance(data.get("presets"), list):
        data["presets"] = []

    settings = data["settings"]

    settings.setdefault("close_on_x", False)
    settings.setdefault("start_with_windows", False)
    settings.setdefault("gpu_keep_on_exit", True)

    if not isinstance(settings.get("current"), dict):
        settings["current"] = json.loads(json.dumps(DEFAULT_CONFIG["settings"]["current"]))
    if not isinstance(settings.get("hotkeys"), dict):
        settings["hotkeys"] = json.loads(json.dumps(DEFAULT_CONFIG["settings"]["hotkeys"]))

    for key, value in DEFAULT_CONFIG["settings"]["current"].items():
        settings["current"].setdefault(key, value)

    for key, value in DEFAULT_CONFIG["settings"]["hotkeys"].items():
        settings["hotkeys"].setdefault(key, value)

    # переключатель производительности и автолимит GPU
    settings.setdefault("perf_limited", False)
    settings.setdefault("perf_sound", True)
    settings.setdefault("gpu_apply_on_start", False)

    if settings.get("gpu_auto_pl") is not None:
        try:
            settings["gpu_auto_pl"] = int(settings["gpu_auto_pl"])
        except (TypeError, ValueError):
            settings["gpu_auto_pl"] = None

    profiles = settings.setdefault(
        "perf_profiles",
        json.loads(json.dumps(DEFAULT_CONFIG["settings"]["perf_profiles"])),
    )
    if not isinstance(profiles, dict):
        profiles = json.loads(json.dumps(DEFAULT_CONFIG["settings"]["perf_profiles"]))
        settings["perf_profiles"] = profiles
    for slot, default in DEFAULT_CONFIG["settings"]["perf_profiles"].items():
        block = profiles.get(slot)
        if not isinstance(block, dict):
            block = dict(default)
            profiles[slot] = block
        for part in ("cpu", "gpu"):
            block.setdefault(part, default.get(part, ""))

    # мусор в списке пресетов (строка или число вместо объекта) раньше валил
    # старт AttributeError — выбрасываем его до нормализации
    data["presets"] = [p for p in data["presets"] if isinstance(p, dict)]

    if not data["presets"]:
        data["presets"] = json.loads(json.dumps(DEFAULT_CONFIG["presets"]))

    for preset in data["presets"]:
        preset.setdefault("name", "Unnamed")
        preset.setdefault("brightness", 1.0)
        preset.setdefault("contrast", 1.0)
        preset.setdefault("gamma", 1.0)
        preset.setdefault("temperature", 6500)
        preset.setdefault("hotkey", "")

        if not isinstance(preset["name"], str) or not preset["name"].strip():
            preset["name"] = "Unnamed"
        if not isinstance(preset["hotkey"], str):
            preset["hotkey"] = ""

        preset["brightness"] = clamp(safe_float(preset["brightness"], 1.0), 0.5, 2.0)
        preset["contrast"] = clamp(safe_float(preset["contrast"], 1.0), 0.3, 2.0)
        preset["gamma"] = clamp(safe_float(preset["gamma"], 1.0), 0.5, 2.5)
        preset["temperature"] = int(clamp(safe_int(preset["temperature"], 6500), 3000, 10000))

    current = settings["current"]
    current["brightness"] = clamp(safe_float(current.get("brightness", 1.0), 1.0), 0.5, 2.0)
    current["contrast"] = clamp(safe_float(current.get("contrast", 1.0), 1.0), 0.3, 2.0)
    current["gamma"] = clamp(safe_float(current.get("gamma", 1.0), 1.0), 0.5, 2.5)
    current["temperature"] = int(clamp(safe_int(current.get("temperature", 6500), 6500), 3000, 10000))

    if json.dumps(data, sort_keys=True, ensure_ascii=False) != before:
        save_config(data)
    return data

def get_startup_shortcut_path() -> Path:
    """Скрипт автозапуска. `.vbs`: WScript умеет запускать процесс без окна."""
    startup_dir = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup_dir / f"{APP_NAME}.vbs"


def _legacy_startup_bat() -> Path:
    """Старый .bat-автозапуск (до тихого способа) — подчищаем при переключении."""
    return get_startup_shortcut_path().with_suffix(".bat")


def enable_startup():
    """Включить автозапуск без окна.

    Раньше это был .bat: он стартовал через `cmd` и при входе в систему
    мелькал чёрным окном. WScript.Shell.Run с флагом 0 (скрыто) не показывает
    ничего. В dev-режиме берём pythonw.exe — у python.exe консольная подсистема.
    """
    frozen = getattr(sys, "frozen", False)
    target = sys.executable if frozen else gd_proc.python_exe()
    command = f'"{target}"'
    if not frozen:
        command += f' "{Path(__file__).resolve()}"'
    # в VBS кавычка внутри строкового литерала удваивается
    vbs_command = command.replace('"', '""')
    content = (
        # i18n: skip — комментарий внутри .vbs-файла автозапуска, пользователь его не видит
        "' Gammadrilon — автозапуск без окна (файл создан приложением)\r\n"
        "Set sh = CreateObject(\"WScript.Shell\")\r\n"
        f'sh.Run "{vbs_command}", 0, False\r\n'
    )
    get_startup_shortcut_path().write_text(content, encoding="utf-16")
    legacy = _legacy_startup_bat()
    if legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass


def disable_startup():
    for path in (get_startup_shortcut_path(), _legacy_startup_bat()):
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass

class GammaController:
    """Гамма через SetDeviceGammaRamp.

    Обычно рампа ставится на DC всего экрана, но некоторые драйверы его не
    принимают — тогда пробуем DC отдельных дисплеев (CreateDC("\\\\.\\DISPLAYn")).
    Чтение (GetDeviceGammaRamp) идёт тем же путём: оно нужно сторожу гаммы,
    который возвращает рампу после сброса системой.
    """

    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32

        self.user32.GetDC.argtypes = [wintypes.HWND]
        self.user32.GetDC.restype = wintypes.HDC

        self.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.user32.ReleaseDC.restype = ctypes.c_int

        self.gdi32.CreateDCW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p
        ]
        self.gdi32.CreateDCW.restype = wintypes.HDC

        self.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi32.DeleteDC.restype = wintypes.BOOL

        self.gdi32.SetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
        self.gdi32.SetDeviceGammaRamp.restype = wintypes.BOOL

        self.gdi32.GetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
        self.gdi32.GetDeviceGammaRamp.restype = wintypes.BOOL

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

    @staticmethod
    def ramp_array(red: list[int], green: list[int], blue: list[int]):
        gamma_array_type = ctypes.c_ushort * (3 * 256)
        gamma_array = gamma_array_type()

        for i in range(256):
            gamma_array[i] = red[i]
            gamma_array[256 + i] = green[i]
            gamma_array[512 + i] = blue[i]

        return gamma_array

    @staticmethod
    def ramp_max_diff(left, right) -> int:
        """Максимальное расхождение двух рамп по всем каналам (0..65535)."""
        return max(
            max(abs(a - b) for a, b in zip(left_channel, right_channel))
            for left_channel, right_channel in zip(left, right)
        )

    def _display_dc_names(self) -> tuple[str, ...]:
        """DC дисплеев: «DISPLAY» (основной) и \\\\.\\DISPLAY1..16 (пропуски бывают)."""
        return ("DISPLAY",) + tuple(f"\\\\.\\DISPLAY{i}" for i in range(1, 17))

    def _set_on_hdc(self, hdc, gamma_array) -> bool:
        try:
            return bool(self.gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(gamma_array)))
        except Exception:  # noqa: BLE001 — сбой драйвера не должен ронять окно
            return False

    def _read_from_hdc(self, hdc):
        gamma_array = (ctypes.c_ushort * (3 * 256))()

        try:
            ok = self.gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(gamma_array))
        except Exception:  # noqa: BLE001
            return None

        if not ok:
            return None

        return (
            [gamma_array[i] for i in range(256)],
            [gamma_array[256 + i] for i in range(256)],
            [gamma_array[512 + i] for i in range(256)],
        )

    def apply(
        self,
        brightness: float,
        contrast: float,
        gamma: float = 1.0,
        temperature: int = 6500,
    ) -> bool:
        gamma_array = self.ramp_array(*self.build_ramp(brightness, contrast, gamma, temperature))

        # 1) прежний путь: DC всего экрана (принимает гамму у большинства систем)
        hdc = self.user32.GetDC(None)

        if hdc:
            try:
                if self._set_on_hdc(hdc, gamma_array):
                    return True
            finally:
                self.user32.ReleaseDC(None, hdc)

        # 2) запасной путь: DC отдельных дисплеев — некоторые драйверы
        #    принимают гамму только так (screen DC отвечает FALSE)
        applied = False

        for name in self._display_dc_names():
            hdc = self.gdi32.CreateDCW(name, None, None, None)

            if not hdc:
                continue

            try:
                if self._set_on_hdc(hdc, gamma_array):
                    applied = True
            finally:
                self.gdi32.DeleteDC(hdc)

        return applied

    def read_current(self):
        """Фактическая рампа дисплея или None, если система её не отдаёт."""
        hdc = self.user32.GetDC(None)

        if hdc:
            try:
                current = self._read_from_hdc(hdc)
            finally:
                self.user32.ReleaseDC(None, hdc)

            if current is not None:
                return current

        for name in self._display_dc_names():
            hdc = self.gdi32.CreateDCW(name, None, None, None)

            if not hdc:
                continue

            try:
                current = self._read_from_hdc(hdc)
            finally:
                self.gdi32.DeleteDC(hdc)

            if current is not None:
                return current

        return None

    def reset(self) -> bool:
        return self.apply(1.0, 1.0, 1.0, 6500)


class HotkeyManager(QObject):
    nextPreset = pyqtSignal()
    prevPreset = pyqtSignal()
    resetRequested = pyqtSignal()
    catVisionRequested = pyqtSignal()
    presetRequested = pyqtSignal(int)
    paletteRequested = pyqtSignal()
    perfToggle = pyqtSignal()
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
        safe_add(hotkeys.get("perf_toggle", "ctrl+alt+p"), lambda: self.perfToggle.emit())

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
        font.setPixelSize(th.TINY_PX)
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
    def __init__(self, hotkeys: dict, perf: dict | None = None,
                 sound: bool = True, parent=None):
        super().__init__(parent)

        self.setWindowTitle(i18n.t("Глобальные клавиши"))
        self.setModal(True)
        self.setMinimumWidth(430)

        self.inputs = {}
        self.perf_inputs = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.DIALOG_MARGINS)
        root.setSpacing(th.DIALOG_SPACING)

        title = QLabel(i18n.t("Глобальные клавиши"))
        title.setObjectName("DialogTitle")
        root.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(th.SPACE_M)
        form.setVerticalSpacing(th.SPACE_S)
        root.addLayout(form)

        items = [
            ("next_preset", i18n.t("Следующий пресет")),            ("prev_preset", i18n.t("Предыдущий пресет")),
            ("reset", i18n.t("Аварийный сброс")),
            ("cat_vision", i18n.t("Кошачье зрение")),
            ("perf_toggle", i18n.t("Переключить производительность")),
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

        # --- профили переключения производительности ----------------------
        # Клавиша «Переключить производительность» применяет пару профилей
        # CPU+GPU: что ставить, когда ограничения включаются и когда снимаются.
        perf = perf or {}

        perf_title = QLabel(i18n.t("Профили переключения"))
        perf_title.setObjectName("DialogTitle")
        root.addWidget(perf_title)

        perf_hint = QLabel(i18n.t(
            "Что применять по клавише: «Ограничение» — когда лимиты включаются, "
            "«Производительность» — когда снимаются. «Не менять» — не трогать блок."))
        perf_hint.setObjectName("Hint")
        perf_hint.setWordWrap(True)
        root.addWidget(perf_hint)

        perf_form = QGridLayout()
        perf_form.setHorizontalSpacing(th.SPACE_M)
        perf_form.setVerticalSpacing(th.SPACE_S)
        root.addLayout(perf_form)

        cpu_choices = [
            ("", i18n.t("(не менять)")),
            ("eco", i18n.t("ЭКО")),
            ("normal", i18n.t("НОРМА")),
            ("boost", i18n.t("БУСТ")),
        ]
        gpu_choices = [
            ("", i18n.t("(не менять)")),
            ("quiet", i18n.t("Тихий")),
            ("default", i18n.t("Дефолт")),
            ("game", i18n.t("Игровой")),
            ("max", i18n.t("Макс")),
        ]

        perf_form.addWidget(QLabel(""), 0, 0)
        perf_form.addWidget(QLabel(i18n.t("CPU")), 0, 1)
        perf_form.addWidget(QLabel(i18n.t("GPU")), 0, 2)

        for row, (slot, label_text) in enumerate(
                (("limited", i18n.t("Ограничение")),
                 ("boost", i18n.t("Производительность"))), start=1):
            spec = perf.get(slot) or {}

            cpu_combo = QComboBox()
            for pid, caption in cpu_choices:
                cpu_combo.addItem(caption, pid)
            index = cpu_combo.findData(spec.get("cpu") or "")
            cpu_combo.setCurrentIndex(max(index, 0))

            gpu_combo = QComboBox()
            for pid, caption in gpu_choices:
                gpu_combo.addItem(caption, pid)
            index = gpu_combo.findData(spec.get("gpu") or "")
            gpu_combo.setCurrentIndex(max(index, 0))

            self.perf_inputs[slot] = (cpu_combo, gpu_combo)

            perf_form.addWidget(QLabel(label_text), row, 0)
            perf_form.addWidget(cpu_combo, row, 1)
            perf_form.addWidget(gpu_combo, row, 2)

        # Звук переключателя: галка «вкл/выкл» плюс прослушивание обоих мотивов —
        # характер слышен сразу, без реального переключения профилей (и без прав).
        sound_row = QHBoxLayout()
        self.chk_sound = QCheckBox(i18n.t("Звук при переключении"))
        self.chk_sound.setChecked(bool(sound))
        self.chk_sound.setToolTip(i18n.t("Звук отклика на переключение профилей."))
        sound_row.addWidget(self.chk_sound)
        sound_row.addStretch()

        self.btn_sound_limited = QPushButton(i18n.t("▶ Ограничение"))
        self.btn_sound_limited.setToolTip(i18n.t("Прослушать звук включения ограничений"))
        self.btn_sound_limited.clicked.connect(lambda: self._preview_sound(True))
        sound_row.addWidget(self.btn_sound_limited)

        self.btn_sound_boost = QPushButton(i18n.t("▶ Производительность"))
        self.btn_sound_boost.setToolTip(i18n.t("Прослушать звук снятия ограничений"))
        self.btn_sound_boost.clicked.connect(lambda: self._preview_sound(False))
        sound_row.addWidget(self.btn_sound_boost)
        root.addLayout(sound_row)

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

    def result_perf(self) -> dict:
        """Пара профилей переключения: {limited: {cpu, gpu}, boost: {cpu, gpu}}."""
        return {
            slot: {"cpu": cpu.currentData() or "", "gpu": gpu.currentData() or ""}
            for slot, (cpu, gpu) in self.perf_inputs.items()
        }

    def result_sound(self) -> bool:
        return bool(self.chk_sound.isChecked())

    def _preview_sound(self, limited: bool) -> None:
        """Прослушать мотив переключателя — независимо от галки звука.

        Галка выключает отклик на реальное переключение, а не прослушивание:
        иначе проверить, что звук слышно, было бы нечем.
        """
        gd_perf.play_sound(limited)


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
        # пункт меню должен быть достижим с клавиатуры: раньше Tab его пропускал,
        # и в раздел можно было попасть только мышью
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(text)
        self.setProperty("active", False)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(th.SPACE_M, 0, th.SPACE_M, 0)
        lay.setSpacing(th.SPACE_S)

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

    def set_badge(self, text: str) -> None:
        """Обновить бейдж справа (пустая строка — бейдж скрыт)."""
        text = text or ""
        if not text:
            if self.badge is not None:
                self.badge.hide()
            return
        if self.badge is None:
            self.badge = QLabel(text)
            self.badge.setObjectName("NavBadge")
            self.layout().addWidget(self.badge)
        else:
            self.badge.setText(text)
        self.badge.show()

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

    def keyPressEvent(self, event):  # noqa: N802
        """Enter/Space на пункте — то же, что клик: иначе меню недостижимо с клавиатуры."""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self.key)
            event.accept()
            return
        super().keyPressEvent(event)


class AgentBridge(QObject):
    """Мост «поток API-сервера → GUI-поток» (ТЗ, этап 8).

    Сервер живёт в своих потоках и не трогает ни Qt-виджеты, ни PDH-запросы:
    каждый запрос превращается в сигнал (он thread-safe), GUI-поток выполняет
    действие и будит ожидающий серверный поток через `threading.Event`.
    """

    request = pyqtSignal(object)


class TitleBar(QWidget):
    """Шапка окна, которую можно тянуть мышью.

    Окно без рамки, системного заголовка нет — сдвинуть окно мышью было нечем.
    Сначала пробуем нативный перетаск (`QWindow.startSystemMove`): он даёт прилипание
    к краям экрана и корректный выход из развёрнутого состояния. Если платформа его не
    поддерживает (offscreen-прогоны, нестандартные сборки Qt), тянем окно сами: смещение
    считается от точки нажатия, поэтому под курсором окно не прыгает. Нажатия на кнопки
    шапки сюда не доходят — кнопки забирают событие себе.
    """

    def __init__(self, win):
        super().__init__()
        self._win = win
        self._grab_global = None      # точка нажатия в экранных координатах
        self._grab_win = None         # левый верхний угол окна в тот момент

    def _start_system_move(self) -> bool:
        """Нативный перетаск средствами ОС; False — платформа не умеет."""
        if self._win.isFullScreen():
            return False
        handle = self._win.windowHandle()
        if handle is None:
            return False
        try:
            return bool(handle.startSystemMove())
        except (AttributeError, TypeError):   # Qt < 5.15: метода нет
            return False

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton or self._start_system_move():
            super().mousePressEvent(event)
            return
        self._grab_global = event.globalPos()
        self._grab_win = self._win.frameGeometry().topLeft()
        self._restore_from_max()
        event.accept()

    def _restore_from_max(self) -> None:
        """Развёрнутое окно тянем как системное: сначала прежний размер, затем сдвиг.

        Курсор остаётся на том же месте по горизонтали (окно не улетает от руки) и на
        шапке по вертикали.
        """
        if not self._win.isMaximized():
            return
        normal = self._win.normalGeometry()
        full = self._win.frameGeometry()
        ratio = (self._grab_global.x() - full.left()) / max(1, full.width())
        self._win.showNormal()
        self._win.move(self._grab_global.x() - int(normal.width() * ratio),
                       max(0, self._grab_global.y() - self.height() // 2))
        self._grab_win = self._win.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._grab_global is None or not (event.buttons() & Qt.LeftButton):
            super().mouseMoveEvent(event)
            return
        delta = event.globalPos() - self._grab_global
        if not delta.isNull():
            self._win.move(self._grab_win + delta)
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._grab_global = None
        self._grab_win = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        """Двойной клик по шапке — развернуть/восстановить, как в системном окне."""
        if event.button() == Qt.LeftButton:
            self._grab_global = None
            self._grab_win = None
            self._win._toggle_max()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.config = load_config()
        i18n.set_language(self.config.get("settings", {}).get("language") or i18n.detect())
        self.gamma_controller = GammaController()
        self.hotkey_manager = HotkeyManager()

        # запись конфига — не на каждый тик слайдера: собираем изменения и пишем
        # один раз, когда пользователь остановился (выход сбрасывает немедленно)
        self._config_save_timer = QTimer(self)
        self._config_save_timer.setSingleShot(True)
        self._config_save_timer.setInterval(CONFIG_SAVE_DELAY_MS)
        self._config_save_timer.timeout.connect(self._flush_config_save)
        self._config_pending = False

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

        # сторож гаммы: система (сон, блокировка, смена режима) может сбросить
        # рампу — сверяем факт с целью и возвращаем своё
        self._gamma_applied_ok = False
        self._gamma_applied_values = None   # что приложение фактически поставило на экран
        self.gamma_watch_timer = QTimer(self)
        self.gamma_watch_timer.setInterval(GAMMA_WATCH_INTERVAL_MS)
        self.gamma_watch_timer.timeout.connect(self.gamma_watch_tick)

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
        # локальный API для инструментов (ТЗ, этап 8): сервер и обработчики
        self._agent_server = None
        self._agent_confirm_open = False
        self._agent_confirm_dialog = None
        self._agent_bridge = AgentBridge()
        self._agent_bridge.request.connect(self._agent_handle)
        self._agent_handlers = {
            "status": self._agent_status,
            "sensors": self._agent_sensors,
            "journal": self._agent_journal,
            "rules": self._agent_rules,
            "zapret": self._agent_zapret,
            "cpu_profile": self._agent_cpu_profile,
            "gpu_profile": self._agent_gpu_profile,
            "gpu_limit": self._agent_gpu_limit,
            "dns_set": self._agent_dns_set,
        }
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
        self._fix_tab_order()

        # автолимит GPU: после старта вернуть лимит по настройке (фон, один раз)
        QTimer.singleShot(1500, self._gpu_autostart_check)

        # локальный API: поднимаем только если пользователь включал его сам
        if bool(self.config.get("settings", {}).get("agent_api", False)):
            QTimer.singleShot(1200, self._agent_autostart)

        current = self.config["settings"]["current"]

        self.set_ui_values(
            current.get("brightness", 1.0),
            current.get("contrast", 1.0),
            current.get("gamma", 1.0),
            current.get("temperature", 6500),
            apply_now=True,
            save=False,
        )

        self.gamma_watch_timer.start()

        # честный старт: если конфиг пришлось заменить дефолтом, об этом говорят
        self._config_recovery_note()

    def make_card(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Card")

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(*th.CARD_MARGINS)
        layout.setSpacing(th.CARD_SPACING)

        return frame, layout

    def bind_text(self, widget, key: str) -> None:
        widget.setText(i18n.t(key))
        self._text_bindings.append((widget, key))

    def bind_tooltip(self, widget, key: str) -> None:
        widget.setToolTip(i18n.t(key))
        self._tooltip_bindings.append((widget, key))

    @staticmethod
    def set_value_text(label: QLabel, value, suffix: str, big: bool = False):
        number_size = th.VALUE_PX if big else th.VALUE_S_PX
        unit_size = th.VALUE_UNIT_PX if big else th.VALUE_S_UNIT_PX

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
        layout.setSpacing(th.SPACE_XS)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(th.SPACE_S)

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
        self._side_layout.setContentsMargins(th.SPACE_M, th.SPACE_M, th.SPACE_M, th.SPACE_M)
        self._side_layout.setSpacing(th.SPACE_XS)
        self._nav_items: dict = {}
        self._key_to_index: dict[str, int] = {}
        self._page_widgets: dict = {}
        self._page_heads: dict = {}

        # кнопки настройки горячих клавиш живут в низу сайдбара (см. `_build_side_tools`):
        # блок создаётся один раз, `_rebuild_sidebar` только переставляет его в конец
        self._side_tools = self._build_side_tools()

        self.tabs = QStackedWidget()
        self.tabs.setObjectName("Pages")

        # ---------- страница гаммы ----------
        gamma_page = QWidget()
        gamma_page.setObjectName("Root")

        main_layout = QHBoxLayout(gamma_page)
        main_layout.setContentsMargins(*th.PAGE_MARGINS)
        main_layout.setSpacing(th.PAGE_SPACING)

        # ---------- левая (основная) панель ----------
        self.left_panel = QWidget()
        self.left_panel.setMinimumWidth(440)
        self.left_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(th.PAGE_SPACING)

        # шапка
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(th.SPACE_M)

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
        title_block.setSpacing(0)

        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")

        self.version_label = QLabel(APP_VERSION)
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
        status_block.setSpacing(th.SPACE_XS)
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
        preset_row.setSpacing(th.SPACE_S)

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
        temp_layout.setSpacing(th.SPACE_XS)

        temp_top = QHBoxLayout()
        temp_top.setContentsMargins(0, 0, 0, 0)
        temp_top.setSpacing(th.SPACE_S)

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
        options_layout.setSpacing(th.SPACE_S)

        # автозапуск с Windows живёт во вкладке «Утилиты» (галочка + честный
        # откат при отказе); здесь остаётся только выход по закрытию окна.
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

        options_layout.addWidget(self.close_on_x_checkbox)

        advanced_layout.addLayout(options_layout)

        # горячие клавиши переехали в сайдбар (см. `_build_side_tools`): в карточке
        # «Точная настройка» кнопки терялись среди слайдеров и уезжали под сгиб

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

        self._gpu_page = GpuWidget(standalone=False, config=self.config,
                                   save_fn=save_config)
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

        from gd_zapret_ui import ZapretWidget

        self._zapret_page = ZapretWidget(standalone=False, host=self)
        self._add_page(self._zapret_page, "tab.zapret")

        from gd_agent_ui import AgentWidget

        self._agent_page = AgentWidget(standalone=False, host=self)
        self._add_page(self._agent_page, "tab.agent")

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
        # блок горячих клавиш — постоянный жилец низа сайдбара: пересборка списка
        # пунктов не должна его удалять (иначе кнопки исчезнут после смены языка)
        tools = getattr(self, "_side_tools", None)
        while self._side_layout.count():
            entry = self._side_layout.takeAt(0)
            w = entry.widget()
            if w is not None and w is not tools:
                w.deleteLater()
        self._nav_items = {}
        layout = (
            ("grp", "side.power"), ("page", "tab.cpu"), ("page", "tab.gpu"),
            ("grp", "side.system"), ("page", "tab.gamma"), ("page", "tab.rules"),
            ("page", "tab.utils"), ("page", "tab.dns"), ("page", "tab.zapret"),
            ("page", "tab.agent"),
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
        if tools is not None:
            # под пунктами меню, над индикатором службы питания
            self._side_layout.addWidget(tools)
        # бейдж GPU берётся из общего кэша: даём фоновому сборщику прогреться
        QTimer.singleShot(1500, self._refresh_gpu_badge)

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
                import gd_metrics

                pl = (gd_metrics.gpu() or {}).get("pl")
                return i18n.t("{v0:.0f} Вт", v0=pl) if pl else ""
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

    def _refresh_gpu_badge(self) -> None:
        """Обновить бейдж GPU в сайдбаре из кэша метрик (без подпроцессов)."""
        item = getattr(self, "_nav_items", {}).get("tab.gpu")
        if item is None:
            return
        try:
            item.set_badge(self._side_badge("tab.gpu"))
        except Exception:  # noqa: BLE001
            pass

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
        """Кастомный титлбар: бренд, чип режима, быстрые кнопки, кнопки окна.

        Сам виджет — `TitleBar`: за пустые места шапки окно тянется мышью.
        """
        bar = TitleBar(self)
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(42)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(th.SPACE_M, 0, th.SPACE_S, 0)
        lay.setSpacing(th.SPACE_S)

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

        ver = QLabel(APP_VERSION)
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

    def _build_side_tools(self):
        """Низ сайдбара: настройка горячих клавиш.

        Кнопки раньше лежали в карточке «Точная настройка» и терялись среди слайдеров
        (плюс уезжали под сгиб на низких окнах), хотя настройка нужна редко и вразброс
        по разным разделам. Здесь они прижаты к низу меню и доступны с любой страницы.
        """
        box = QWidget()
        box.setObjectName("SideTools")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(th.SPACE_XS)

        head = QLabel()
        head.setObjectName("SideToolsHead")
        self.bind_text(head, "Горячие клавиши")
        lay.addWidget(head)

        for attr, title, tip, slot in (
            ("global_hotkeys_button", "Глобальные клавиши",
             "Переключение пресета ПК, привязка профилей и аварийный сброс", self.edit_global_hotkeys),
            ("preset_hotkey_button", "Клавиша пресета",
             "Горячая клавиша, включающая текущий пресет гаммы", self.set_current_preset_hotkey),
        ):
            btn = QPushButton()
            btn.setObjectName("SideToolBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(26)
            self.bind_text(btn, title)
            self.bind_tooltip(btn, tip)
            btn.clicked.connect(slot)
            setattr(self, attr, btn)
            lay.addWidget(btn)

        return box

    def _build_side_foot(self):
        """Низ сайдбара: честный индикатор службы питания.

        В макете A подпись была статичной — «Служба питания активна» и вечно
        горящий LED, хотя компаньон питания ленивый: его нет до первого
        применения профиля. Индикатор показывает реальное состояние (см.
        :meth:`_sync_foot`), иначе подпись врёт пользователю.
        """
        foot = QWidget()
        foot.setObjectName("SideFoot")
        lay = QHBoxLayout(foot)
        lay.setContentsMargins(th.SPACE_M, th.SPACE_S, th.SPACE_M, th.SPACE_S)
        lay.setSpacing(th.SPACE_S)
        self._foot_led = QFrame()
        self._foot_led.setObjectName("FootLed")
        self._foot_led.setFixedSize(7, 7)
        lay.addWidget(self._foot_led)
        self._foot_label = QLabel("")
        self._foot_label.setObjectName("FootText")
        lay.addWidget(self._foot_label)
        lay.addStretch(1)
        self._foot_state = None
        self._sync_foot()
        return foot

    def _sync_foot(self) -> None:
        """Обновить индикатор службы питания по живому состоянию компаньона.

        Дешёвый вызов (без обращений к пайпу и без запуска компаньона), поэтому
        дёргается из уже существующего тика статусбара.
        """
        label = getattr(self, "_foot_label", None)
        led = getattr(self, "_foot_led", None)
        if label is None or led is None:
            return          # фут ещё не построен: build_ui зовёт _retranslate_tabs раньше
        page = getattr(self, "_power_page", None)
        try:
            alive = bool(page is not None and page.service_alive())
            profile = page.current_profile() if page is not None else None
        except Exception:  # noqa: BLE001
            return
        if (alive, profile) == self._foot_state:
            return
        self._foot_state = (alive, profile)

        if alive:
            names = {"eco": i18n.t("ЭКО"), "normal": i18n.t("НОРМАЛ"), "boost": i18n.t("БУСТ")}
            text = i18n.t("Служба питания активна")
            tip = i18n.t("Компаньон питания работает: профили применяются через него.")
            tip += (" " + i18n.t("Профиль: {v0}.", v0=names.get(profile, str(profile).upper()))
                    if profile else " " + i18n.t("Активна исходная схема."))
        else:
            text = i18n.t("Служба питания не запущена")
            tip = i18n.t("Компаньон поднимется, когда применишь профиль CPU.")

        try:
            label.setText(text)
            label.setToolTip(tip)
            led.setToolTip(tip)
            led.setProperty("state", "on" if alive else "off")
            led.style().unpolish(led)   # QSS перечитывает [state="..."] только так
            led.style().polish(led)
        except RuntimeError:            # виджет уже удалён: выход приложения
            pass

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
        head_lay.setContentsMargins(*th.HEAD_MARGINS)
        head_lay.setSpacing(th.HEAD_SPACING)
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

    def _fix_tab_order(self) -> None:
        """Порядок обхода Tab: сайдбар → содержимое страницы → кнопки титлбара.

        Qt кладёт виджеты в цепочку фокуса по порядку создания, поэтому титлбар
        (он создаётся первым) оказывался впереди меню: первый Tab уходил в «Режим»
        и быстрые кнопки, а до разделов надо было пройти всю шапку. Здесь цепочка
        переставляется в читаемый порядок; кнопки окна — в конце, они дублируют
        системные (Alt+Space, мышь).
        """
        nav = list(getattr(self, "_nav_items", {}).values())
        if not nav:
            return
        # вся цепочка целиком; `keep` не даёт PyQt пересоздать обёртки — иначе
        # id() переиспользуется и обход обрывается на середине
        ring, seen, keep = [], set(), []
        w = self
        for _ in range(4000):
            w = w.nextInFocusChain()
            if w is None or w is self or id(w) in seen:
                break
            seen.add(id(w))
            keep.append(w)
            ring.append(w)
        # isVisibleTo(self), а не isVisible(): в __init__ окно ещё не показано, и все
        # дети «невидимы». isVisibleTo учитывает только явное скрытие — скрытые
        # страницы стека (isHidden) отсеиваются, а видимые виджеты остаются.
        stops = [x for x in ring if x.focusPolicy() != Qt.NoFocus and x.isVisibleTo(self)]
        page = [x for x in stops if self.tabs.isAncestorOf(x)]
        tail = [x for x in stops if x.objectName() == "ModeChip"
                or x.objectName().startswith("Win") or x.objectName() == "TitleBtn"]
        if not page or not tail:
            return
        # setTabOrder(anchor, target) переносит target сразу за anchor. Идём по
        # желаемой последовательности, каждый раз сдвигая якорь: так кольцо
        # фокуса перестраивается целиком (пара (окно, первый пункт) у Qt не
        # срабатывает — верхний уровень не переставляется).
        anchor = stops[0]
        # кнопки настройки горячих клавиш стоят в сайдбаре сразу после пунктов меню:
        # иначе Qt держит их там, где они были созданы (до пунктов меню), и Tab
        # уходит в них раньше навигации
        tools = [x for x in (getattr(self, "global_hotkeys_button", None),
                             getattr(self, "preset_hotkey_button", None))
                 if x is not None and x in stops]
        for target in (*nav, *tools, *page, *tail):
            if target is anchor:
                continue
            QWidget.setTabOrder(anchor, target)
            anchor = target

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

            now = time.monotonic()
            cache = getattr(self, "_scheme_cache", None)
            if cache is None or now - cache[1] > 20:
                guid = gd_power.get_active_scheme()
                scheme = gd_power.scheme_name(guid) if guid else ""
                cache = (scheme, now)
                self._scheme_cache = cache
            if cache[0]:
                parts.append(f"{i18n.t('схема')} «{cache[0]}»")
        except Exception:  # noqa: BLE001
            pass
        return " · ".join(parts)

    def _build_statusbar(self):
        """Нижняя полоса: живые метрики (частота/нагрузка CPU, GPU, RAM, DNS)."""
        bar = QWidget()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(34)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(th.SPACE_L, 0, th.SPACE_L, 0)
        lay.setSpacing(th.SPACE_M)
        self._sb_cpu = QLabel("CPU —")
        self._sb_gpu = QLabel("GPU —")
        self._sb_ram = QLabel("RAM —")
        self._sb_dns = QLabel("DNS —")
        # DNS читается только по запросу — говорим об этом прямо в подсказке
        self.bind_tooltip(self._sb_dns,
                          "DNS читается только по запросу: откройте вкладку «DNS» или "
                          "нажмите «Замерить». В скобках — возраст показанных данных.")
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

        self._sb_timer = QTimer(self)
        self._sb_timer.timeout.connect(self._refresh_statusbar)
        self._sb_timer.start(2000)
        self._refresh_statusbar()
        return bar

    def _refresh_statusbar(self) -> None:
        """CPU/RAM/GPU/DNS — из общего фонового кэша, без подпроцессов в GUI."""
        import gd_metrics

        snap = gd_metrics.snapshot()

        s = snap.get("cpu") or {}
        mhz, load = s.get("mhz"), s.get("load_pct")
        cpu = "CPU"
        if mhz:
            cpu += i18n.t(" {v0:.2f} ГГц", v0=mhz / 1000)
        if load is not None:
            cpu += f" · {load:.0f}%"
        self._sb_cpu.setText(cpu)

        ram_pct = snap.get("ram")
        self._sb_ram.setText(f"RAM {ram_pct:.0f}%" if ram_pct is not None else "RAM —")

        st = snap.get("gpu")
        if st:
            temp = st.get("temp")
            pl, pl_max = st.get("pl") or 0, st.get("pl_max") or 0
            self._sb_gpu.setText(
                i18n.t("GPU {v0:.0f} °C · {v1:.0f}/{v2:.0f} Вт",
                       v0=temp, v1=pl, v2=pl_max)
                if temp is not None else "GPU —")
            self._refresh_gpu_badge()

        dst = snap.get("dns")
        if dst:
            servers = dst.get("servers") or []
            text = "DNS " + (servers[0] if servers else "—")
            age = gd_metrics.dns_age()
            # DNS читается по запросу: возраст цифр — часть честного показа
            if age and age >= 60:
                text += i18n.t(" ({v0:.0f} мин)", v0=age / 60)
            self._sb_dns.setText(text)

        self._sync_foot()       # индикатор службы питания — на этом же тике
        self._check_power_service()   # смерть компаньона питания — тоже новость
        self._check_dpi_bypass()   # смерть нашего winws — тоже новость, а не деталь
        self._check_gpu_drift()    # слетевший лимит GPU из-за сна/драйвера — тоже

    def _check_power_service(self) -> None:
        """Не пропал ли компаньон питания прямо сейчас?

        Он единственный сторож отката профиля CPU: пока окно свёрнуто, его смерть
        была видна только в футе сайдбара (которого свёрнутое окно не показывает),
        а применённый профиль оставался в системе навсегда. Проверка O(1) — это
        состояние своего же процесса, поэтому живёт на тике статусбара.
        """
        page = getattr(self, "_power_page", None)
        if page is None or self.is_quitting:
            return              # выход сам просит компаньон уйти с откатом
        try:
            page.check_service_gone()
        except Exception:  # noqa: BLE001 — доклад о службе не должен ломать тик
            pass

    def _check_gpu_drift(self) -> None:
        """Не вернула ли система свой лимит мощности, пока окно свёрнуто?

        Чтение состояния GPU живёт в общем кэше метрик и стоит ноль, а вот
        сверку «что применено» с «что на карте» раньше делал только старт:
        сон, TDR и сброс драйвера возвращали номинал молча, и применённый
        профиль в трее выглядел рабочим. Тик тот же — двухсекундный.
        """
        if self.is_quitting:
            return              # на выходе приложение само решает судьбу лимита
        page = getattr(self, "_gpu_page", None)
        if page is None:
            return
        try:
            page.check_drift()
        except Exception:  # noqa: BLE001 — доклад о лимите не должен ломать тик
            pass

    def _check_dpi_bypass(self) -> None:
        """Не умер ли наш winws, пока окно свёрнуто? Спросить вкладку обхода.

        Опрос вкладки живёт только на открытой вкладке (экономия холостого
        расхода), а обход включают и уходят в трей: тогда о смерти процесса
        никто бы не сказал. Проверка дешёвая — файл состояния и один процесс,
        поэтому живёт на том же двухсекундном тике, что и статусбар.
        """
        if self.is_quitting:
            return              # выход сам останавливает winws: тост был бы ложным
        page = getattr(self, "_zapret_page", None)
        if page is None:
            return
        try:
            page.check_vanished()
        except Exception:  # noqa: BLE001 — сообщение об обходе не должно ломать тик
            pass

    def build_tray(self):
        from gd_tray import TrayAssistant

        icon_path = resource_path(ICON_FILE)
        self.tray = TrayAssistant(str(icon_path) if icon_path.exists() else None, parent=self)

        self.tray.showRequested.connect(self.show_window)
        self.tray.cpuProfileRequested.connect(self.on_cpu_profile)
        self.tray.gpuProfileRequested.connect(self.on_gpu_profile)
        self.tray.perfToggleRequested.connect(self._on_perf_toggle)
        self.tray.paletteRequested.connect(self.open_palette)
        self.tray.journalRequested.connect(self.open_journal)
        self.tray.rulesRequested.connect(self.open_rules_tab)
        self.tray.utilsRequested.connect(self.open_utils_tab)
        self.tray.themeRequested.connect(self._tray_theme)
        self.tray.resetEverythingRequested.connect(self._tray_reset_everything)
        self.tray.quitRequested.connect(self.quit_app)
        # галка переключателя производительности — из конфига (переживает запуск)
        try:
            limited = bool((self.config.get("settings") or {}).get("perf_limited", False))
        except Exception:  # noqa: BLE001
            limited = False
        self.tray.set_perf_limited(limited)
        self.tray.show()

        # живой тултип (ТЗ 5.1): раз в секунду, без всплывающих окон
        self._gpu_cache = None
        self._tray_timer = QTimer(self)
        self._tray_timer.setInterval(1000)
        self._tray_timer.timeout.connect(self._tray_tick)
        self._tray_timer.start()
        self._tray_tick()

    def _tray_tick(self):
        """Тултип и цвет иконки трея — по данным общего кэша метрик."""
        import gd_metrics
        from gd_tray import build_tooltip

        snap = gd_metrics.snapshot()
        self._gpu_cache = snap.get("gpu")

        try:
            cpu_profile = self._power_page.current_profile()
        except Exception:  # noqa: BLE001
            cpu_profile = None
        try:
            gpu_profile = self._gpu_page.current_profile()
        except Exception:  # noqa: BLE001
            gpu_profile = None

        tip = build_tooltip(cpu_profile, snap.get("cpu"), self._gpu_cache,
                            gpu_profile, snap.get("ram"))
        self.tray.refresh(cpu_profile, gpu_profile, tip)

    # -- движок правил (ТЗ 5.4) --------------------------------------------
    def start_rules(self):
        import gd_bg
        import gd_rules

        self._rule_engine = gd_rules.RuleEngine()
        self._rule_engine.prime()
        # окружение правил (список процессов, питание, загрузка) снимаем в фоне:
        # psutil.process_iter в GUI-потоке каждые 3 с — это фризы
        self._rule_env = gd_bg.periodic(self, 3000, self._collect_rule_env, self._apply_rule_env)
        self._rule_env.trigger()
        self._rule_timer = QTimer(self)
        self._rule_timer.setInterval(3000)
        self._rule_timer.timeout.connect(self._rules_tick)
        self._rule_timer.start()

    def _collect_rule_env(self):
        """Фоновой снимок окружения для правил — только то, что правилам нужно.

        Нет включённых правил на процессы/питание/загрузку (обычный случай после
        установки) — снимать нечего, и `psutil.process_iter` не запускается вовсе.
        Правилу «тихий час» (расписание + загрузка) список процессов не нужен:
        `env_need` возвращает лишь те части окружения, которые кто-то читает.
        """
        import gd_rules

        need = gd_rules.env_need()
        return gd_rules.collect_env(need) if need else None

    @staticmethod
    def _apply_rule_env(env) -> None:
        """Сохранить снятое окружение (приходит в GUI-поток)."""
        if env is None:
            return
        import gd_rules

        gd_rules.update_env(env)

    def _rules_tick(self):
        """Проверить правила и применить сработавшие."""
        import gd_rules

        rules = gd_rules.load_rules()
        fired = self._rule_engine.evaluate(rules)
        if not fired:
            return
        for rule, _why in fired:
            # None — действие идёт в фоне (нужны права): в лог попадёт по факту
            result = self._run_rule_action(rule)
            if result is not None:
                self._log_rule_result(rule, result)

    def _log_rule_result(self, rule: dict, result: str) -> None:
        """Запись в лог правил и обновление окна «Правила», если оно есть."""
        import gd_rules

        gd_rules.log_fire(rule, result)
        page = getattr(self, "_rules_page", None)
        if page is not None:
            page._reload_log()

    def _report_failure(self, status: str, detail: str | None = None) -> None:
        """Отказ фонового или внешнего действия: статус окна + тревожный тост.

        Успех правил, профилей и «тихого часа» виден тостом, а отказы жили только в
        статусбаре и логе вкладки — а окно в этот момент обычно свёрнуто в трей
        (правило сработало само, профиль пришёл из трея, сработал тихий час).
        Тост повторяет формулировку, которая уходит в лог: одна причина — один текст.
        """
        self.set_status(status, ok=False)
        try:
            import gd_toast

            gd_toast.show_toast(detail or status, "warn")
        except Exception:  # noqa: BLE001
            pass

    def _rule_client(self):
        """Клиент хелпера для фоновых действий правил (один на приложение).

        Вкладки «Утилиты»/«DNS» могут быть ни разу не открыты, поэтому правило
        не одалживает клиента у виджета, а держит свой — хелпер многоклиентный.
        """
        import gd_gpu

        client = getattr(self, "_rule_helper_client", None)
        if client is None:
            client = gd_gpu.GpuClient()
            self._rule_helper_client = client
        return client

    def _run_rule_action(self, rule: dict) -> str | None:
        """Выполнить действие правила.

        Возвращает строку для лога правил — или None, если действие фоновое
        (нужны права администратора) и отчитается само, когда реально завершится.
        """
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
                self._rule_purge(rule)
                return None
            if kind == "set_dns":
                self._rule_set_dns(rule)
                return None
        except Exception as exc:  # noqa: BLE001
            detail = i18n.t("«{v0}»: ошибка — {v1}", v0=name, v1=exc)
            self._report_failure(
                i18n.t("Правило «{v0}»: действие не выполнено.", v0=name), detail)
            return detail
        detail = i18n.t("«{v0}»: действие недоступно", v0=name)
        self._report_failure(
            i18n.t("Правило «{v0}»: действие недоступно.", v0=name), detail)
        return detail

    def _rule_purge(self, rule: dict) -> None:
        """Очистка standby list по правилу: в фоне и через уже запущенный хелпер.

        Вкладка «Утилиты» для этого не нужна, а UAC правило не поднимает
        (`call_existing`): окно запроса прав поверх чужой игры — сюрприз. Нет
        сессии — честная причина отказа попадёт и в лог правил, и в статусбар.
        """
        import gd_bg
        import gd_journal

        name = rule.get("name") or "правило"
        client = self._rule_client()

        def done(resp):
            resp = resp if isinstance(resp, dict) else {}
            if not resp.get("ok"):
                detail = i18n.t("«{v0}»: очистка не выполнена — {v1}",
                                v0=name, v1=resp.get("error") or "?")
                self._log_rule_result(rule, detail)
                self._report_failure(
                    i18n.t("Правило «{v0}»: очистка ОЗУ не выполнена.", v0=name), detail)
                return
            data = resp.get("data") or {}
            freed = (((data.get("after") or {}).get("Free") or 0)
                     - ((data.get("before") or {}).get("Free") or 0)) / 1024 ** 2
            self._log_rule_result(
                rule, i18n.t("«{v0}»: standby list очищен, свободно {v1:+.0f} МБ", v0=name, v1=freed)
            )
            gd_journal.record("memory_purge", "standby list", None, None,
                              why=i18n.t("очистка ОЗУ по правилу"),
                              source=i18n.t("правило"), reversible=False)
            import gd_toast

            gd_toast.show_toast(i18n.t("toast.purge", mb=freed), "ok")

        self._rule_purge_worker = gd_bg.submit(
            lambda: client.call_existing({"cmd": "purge_standby"}), done, parent=self
        )

    def _rule_set_dns(self, rule: dict) -> None:
        """Смена DNS по правилу: провайдер выбирается в правиле, UAC из фона — нет.

        DoH правило не включает: шифрование канала — отдельное осознанное решение,
        а не побочный эффект сработавшего триггера.
        """
        import gd_bg
        import gd_dns
        import gd_journal
        import gd_rules

        name = rule.get("name") or "правило"
        action = rule.get("action") or {}
        key = action.get("dns")
        servers = gd_rules.dns_servers(key)
        label = dict(gd_rules.DNS_PROFILES).get(key, key or "?")
        if not servers:
            detail = i18n.t("«{v0}»: у резолвера нет адресов — пропущено", v0=name)
            self._log_rule_result(rule, detail)
            self._report_failure(
                i18n.t("Правило «{v0}»: DNS не изменён.", v0=name), detail)
            return
        client = self._rule_client()

        def work():
            # «как было» — до смены; отказ записи вернём честной припиской в лог
            remembered, _before = gd_dns.remember_before_change()
            resp = client.call_existing({"cmd": "dns_apply", "mode": "static", "servers": servers})
            if isinstance(resp, dict):
                resp["remembered"] = remembered
            return resp

        def done(resp):
            resp = resp if isinstance(resp, dict) else {}
            if not resp.get("ok"):
                detail = i18n.t("«{v0}»: DNS не изменён — {v1}",
                                v0=name, v1=resp.get("error") or "?")
                self._log_rule_result(rule, detail)
                self._report_failure(
                    i18n.t("Правило «{v0}»: DNS не изменён.", v0=name), detail)
                return
            remembered = bool(resp.get("remembered", True))
            self._log_rule_result(
                rule, i18n.t("«{v0}»: DNS переключён на {v1} ({v2})",
                             v0=name, v1=label, v2=", ".join(servers))
                + ("" if remembered else " — " + i18n.t(
                    "прежний DNS не запомнен, откат к нему недоступен"))
            )
            gd_journal.record("dns_apply", i18n.t("DNS"), None, ", ".join(servers),
                              why=i18n.t("смена DNS по правилу"),
                              source=i18n.t("правило"), reversible=remembered)
            import gd_metrics

            gd_metrics.request_dns()   # смена — это и есть запрос: статусбар покажет новые адреса
            import gd_toast

            gd_toast.show_toast(i18n.t("toast.dns_apply", name=label), "ok")

        self._rule_dns_worker = gd_bg.submit(work, done, parent=self)

    # -- действия из трея --------------------------------------------------
    def on_cpu_profile(self, pid):
        """Применение профиля CPU из трея (действие, а не загрузка пресета)."""
        try:
            self._power_page.apply_profile(pid, source="трей")
        except Exception as exc:  # noqa: BLE001
            self._report_failure(i18n.t("Профиль не применён: {v0}", v0=exc))

    def on_gpu_profile(self, name):
        try:
            self._gpu_page.apply_profile(name, source="трей")
        except Exception as exc:  # noqa: BLE001
            self._report_failure(i18n.t("Профиль GPU не применён: {v0}", v0=exc))

    # -- локальный API для инструментов (ТЗ, этап 8) ------------------------
    def _agent_autostart(self):
        """Поднять API при старте приложения (только если включал пользователь)."""
        ok, err = self.start_agent_api()
        if not ok:
            try:
                self._agent_page._show_warn(err)
            except Exception:  # noqa: BLE001
                pass
            self.set_status(i18n.t("Локальный API не запущен: {v0}", v0=err), ok=False)

    def start_agent_api(self) -> tuple[bool, str]:
        """Включить локальный API: сервер, токен, аудит. → (ок, ошибка)."""
        if self._agent_server is not None and self._agent_server.running():
            return True, ""
        server = gd_agent.AgentServer(runner=self._agent_runner, version=APP_VERSION)
        ok, err = server.start()
        if not ok:
            return False, err
        self._agent_server = server
        self._agent_page.set_running(server.port, server.warn)
        self.set_agent_autostart(True)
        self.set_status(i18n.t("Локальный API включён: {v0}", v0=server.endpoint()))
        return True, ""

    def stop_agent_api(self, remember: bool = True) -> None:
        """Выключить API: остановить сервер и убрать адрес из конфига."""
        server, self._agent_server = self._agent_server, None
        if server is not None:
            server.stop()
        try:
            self._agent_page.set_stopped()
        except Exception:  # noqa: BLE001
            pass
        if remember:
            self.set_agent_autostart(False)
            self.set_status(i18n.t("Локальный API выключен."))

    def agent_state(self) -> dict:
        server = self._agent_server
        if server is None or not server.running():
            return {"running": False, "port": 0, "warn": ""}
        return {"running": True, "port": server.port, "warn": server.warn}

    def agent_autostart(self) -> bool:
        return bool(self.config.get("settings", {}).get("agent_api", False))

    def set_agent_autostart(self, on: bool) -> None:
        """Запомнить «поднимать API при старте» (в presets.json).

        Файл пишем только при изменении: `start_agent_api` зовёт это при каждом
        запуске API, в том числе по автостарту, — иначе каждый старт окна с уже
        включённым автостартом переписывал бы настройки «в никуда».
        """
        settings = self.config.setdefault("settings", {})
        if bool(settings.get("agent_api", False)) == bool(on):
            return
        settings["agent_api"] = bool(on)
        self._save_config()

    def _agent_runner(self, action: str, params: dict, mutate: bool) -> dict:
        """Вызов из потока API-сервера: маршалим в GUI-поток и ждём итог.

        Серверные потоки не трогают ни Qt-виджеты, ни PDH-запросы: действие
        выполняется в GUI-потоке (там же, где кнопки вкладок), а этот поток
        ждёт результат через event. Не дождались — честная ошибка, не выдумка.
        """
        import threading

        req = {"action": action, "params": params, "mutate": mutate,
               "event": threading.Event(), "result": None}
        self._agent_bridge.request.emit(req)
        if not req["event"].wait(gd_agent.action_timeout(action) + 5.0):
            return {"ok": False, "error": i18n.t(
                "таймаут: окно не ответило — приложение занято, скрыто в трее или запрос ждёт подтверждения")}
        return req["result"] or {"ok": False, "error": i18n.t("пустой ответ приложения")}

    def _agent_handle(self, req: dict) -> None:
        """GUI-поток: подтверждение пользователя (для mutate) и выполнение."""
        action = req.get("action") or ""
        spec = gd_agent.find(action)
        if spec is None:
            self._agent_reply(req, {"ok": False, "error": i18n.t("неизвестное действие: {v0}", v0=action)})
            return
        if spec.kind != "mutate" or not req.get("mutate"):
            self._agent_execute(req, spec)
            return
        if self._agent_confirm_open:
            self._agent_reply(req, {"ok": False, "error": i18n.t(
                "уже есть запрос на подтверждение — этот отклонён")})
            return
        from gd_agent_ui import AgentConfirmDialog

        self._agent_confirm_open = True
        self.show_window()          # запрос нужно увидеть: окно могло быть в трее
        dlg = AgentConfirmDialog(spec, req.get("params") or {}, parent=self)
        self._agent_confirm_dialog = dlg

        def _decided(res):
            self._agent_confirm_open = False
            self._agent_confirm_dialog = None
            if int(res) == QDialog.Accepted:
                self._agent_execute(req, spec)
            else:
                self._agent_reply(req, {"ok": False, "error": i18n.t("пользователь отклонил запрос")})

        dlg.finished.connect(_decided)
        dlg.open()

    def _agent_execute(self, req: dict, spec) -> None:
        """Выполнить действие (после подтверждения) и вернуть итог в поток сервера."""
        handler = self._agent_handlers.get(spec.name)
        if handler is None:
            self._agent_reply(req, {"ok": False, "error": i18n.t("действие не подключено: {v0}", v0=spec.name)})
            return
        try:
            res = handler(req.get("params") or {}, req)
        except Exception as exc:  # noqa: BLE001
            self._agent_reply(req, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return
        if res is not None:      # None = асинхронное действие: ответит on_done-хук страницы
            self._agent_reply(req, res)

    @staticmethod
    def _agent_reply(req: dict, result: dict) -> None:
        """Записать итог и разбудить серверный поток (ровно один раз)."""
        if req["event"].is_set():
            return
        req["result"] = result
        req["event"].set()

    # -- обработчики действий API -------------------------------------------
    def _agent_status(self, params: dict, req) -> dict:
        """Сводка: версия, профили, DNS, обход DPI. Только кэши и файлы — без тяжёлых сканов."""
        import gd_metrics
        import gd_power

        data = {
            "app": {"name": APP_NAME, "version": APP_VERSION, "api_protocol": gd_agent.PROTOCOL},
            "cpu": {
                "profile": self._power_page.current_profile(),
                "scheme": gd_power.scheme_name(gd_power.get_active_scheme()),
            },
            "gpu": {"profile": self._gpu_page.current_profile()},
            "dns": gd_metrics.dns(),
            "zapret": self._agent_zapret_data(),
        }
        return {"ok": True, "data": data}

    def _agent_zapret_data(self) -> dict:
        """Состояние обхода DPI без полного сканирования (файл состояния + PID)."""
        import gd_zapret

        ours = gd_zapret.our_state()
        alive = bool(ours) and gd_zapret.pid_alive(ours.get("pid"))
        return {
            "path": self._zapret_page.current_path(),
            "ours": ours if alive else None,
            "our_stale": bool(ours) and not alive,
        }

    def _agent_sensors(self, params: dict, req) -> dict:
        import gd_metrics

        cpu = gd_metrics.cpu()
        return {"ok": True, "data": {
            "cpu_mhz": cpu.get("mhz"),
            "cpu_perf_pct": cpu.get("perf_pct"),
            "cpu_load_pct": cpu.get("load_pct"),
            "ram_pct": gd_metrics.ram(),
            "temp_c": None,
            "note": i18n.t("температура CPU на этой системе недоступна без доп. ПО"),
        }}

    def _agent_journal(self, params: dict, req) -> dict:
        import gd_journal

        report = gd_journal.read_report(int(params.get("n") or 50))
        return {"ok": True, "data": report["items"], "note": gd_journal.state_note()}

    def _agent_rules(self, params: dict, req) -> dict:
        import gd_rules

        out = []
        for r in gd_rules.load_rules():
            try:
                trig, cond, act = gd_rules.describe(r)
            except Exception:  # noqa: BLE001
                trig = cond = act = ""
            out.append({"on": bool(r.get("on")), "trigger": trig,
                        "condition": cond, "action": act})
        return {"ok": True, "data": out}

    def _agent_zapret(self, params: dict, req) -> dict:
        import gd_zapret

        return {"ok": True, "data": gd_zapret.snapshot(self._zapret_page.current_path())}

    # -- изменяющие действия (после подтверждения) --------------------------
    def _agent_cpu_profile(self, params: dict, req):
        self._power_page.apply_profile(
            params["profile"], source=i18n.t("агент"),
            on_done=lambda resp: self._agent_reply(req, {
                "ok": bool(resp.get("ok")), "error": resp.get("error")}))
        return None            # итог придёт асинхронно через on_done

    def _agent_gpu_profile(self, params: dict, req):
        def done(resp):
            self._agent_reply(req, {"ok": bool(resp.get("ok")), "error": resp.get("error")})

        self._gpu_page.apply_profile(params["profile"], source=i18n.t("агент"), on_done=done)
        return None

    def _agent_gpu_limit(self, params: dict, req):
        def done(resp):
            self._agent_reply(req, {"ok": bool(resp.get("ok")), "error": resp.get("error")})

        self._gpu_page.apply_limit(int(params["watts"]), source=i18n.t("агент"), on_done=done)
        return None

    def _agent_dns_set(self, params: dict, req):
        self._dns_page.set_provider(
            params["provider"], source=i18n.t("агент"),
            on_done=lambda resp: self._agent_reply(req, {
                "ok": bool(resp.get("ok")), "error": resp.get("error")}))
        return None





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
            dns.activate()      # открытие вкладки и есть запрос на чтение DNS
        zapret = getattr(self, "_zapret_page", None)
        if zapret is not None:
            (zapret.activate() if page is zapret else zapret.deactivate())
        agent = getattr(self, "_agent_page", None)
        if agent is not None:
            (agent.activate() if page is agent else agent.deactivate())
        self._sync_metrics_activity()

    def open_dns_tab(self):
        """Открыть вкладку «DNS» в главном окне."""
        self.tabs.setCurrentWidget(self._dns_page)
        self.show_window()

    def apply_theme(self, name: str) -> str:
        """Переключить тёмную/светлую тему (ТЗ 5).

        Файл настроек пишем только если тема действительно изменилась: при старте
        окно применяет сохранённую тему, и перезапись presets.json «на всякий
        случай» каждый запуск — лишняя запись на диск.
        """
        global APP_STYLESHEET
        name = th.apply(name)
        APP_STYLESHEET = build_app_stylesheet()
        settings = self.config.setdefault("settings", {})
        if settings.get("theme") != name:
            settings["theme"] = name
            self._save_config()
        # один стиль на всё приложение: страницы больше не носят своих копий QSS,
        # иначе их правила перекрывали общие и расходились (см. gd_theme)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(APP_STYLESHEET)
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
            # через set_status: он же снимает цвет тревоги, оставшийся от отказа
            self.set_status(i18n.t("Готово"))
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
                # i18n: skip — тост показывает сообщение на новом языке, а не перевод
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
        self._foot_state = None          # подпись фута переводим заново
        self._sync_foot()
        # пункты меню пересоздаются, а Qt кладёт новые виджеты в конец кольца фокуса:
        # без перестройки Tab после смены языка уходил в конец меню (а кнопки
        # горячих клавиш — в начало), порядок «меню → страница → шапка» ломался
        self._fix_tab_order()

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
                # откат идёт в фоне (через компаньон), GUI не ждёт
                power.apply_profile("restore", source=i18n.t("утилиты"))
                done.append("питание CPU")
            except Exception:  # noqa: BLE001
                pass

        gpu = getattr(self, "_gpu_page", None)
        if gpu is not None and getattr(gpu, "client", None) is not None:
            try:
                gpu.apply_profile("restore", source=i18n.t("утилиты"))
                done.append("лимит GPU")
            except Exception:  # noqa: BLE001
                pass

        dns = getattr(self, "_dns_page", None)
        if dns is not None:
            try:
                dns._restore()
                done.append("DNS")
            except Exception:  # noqa: BLE001
                pass

        # обход DPI: остановить winws.exe, запущенный Gammadrilon (ТЗ 8, DoD)
        zapret = getattr(self, "_zapret_page", None)
        if zapret is not None:
            try:
                import gd_zapret

                state = gd_zapret.our_state()
                pid = (state or {}).get("pid")
                if pid and gd_zapret.pid_alive(pid):
                    zapret._off()
                    done.append(i18n.t("обход DPI"))
            except Exception:  # noqa: BLE001
                pass

        try:
            rules = gd_rules.load_rules()
            if any(r.get("enabled") for r in rules):
                for r in rules:
                    r["enabled"] = False
                if gd_rules.save_rules(rules):
                    done.append(i18n.t("правила ({v0})", v0=len(rules)))
                else:
                    # в окне выключены, но на диск не легли — в отчёте это видно
                    done.append(i18n.t("правила — не удалось сохранить"))
        except Exception:  # noqa: BLE001
            pass

        utils = getattr(self, "_utils_page", None)
        if utils is not None:
            try:
                utils._load_auto()
                if utils.chk_auto.isChecked():
                    utils.chk_auto.setChecked(False)
                    utils._save_auto()
                    done.append(i18n.t("автоочистка ОЗУ"))
            except Exception:  # noqa: BLE001
                pass

        try:
            if (gd_extra.timer_ms() or 99) < 1.0:
                gd_extra.set_timer_resolution(False)
                done.append(i18n.t("точность таймера"))
        except Exception:  # noqa: BLE001
            pass

        try:
            self.gamma_controller.reset()
            done.append(i18n.t("гамма"))
        except Exception:  # noqa: BLE001
            pass

        try:
            gd_journal.record("reset", i18n.t("все изменения Gammadrilon"), None,
                              ", ".join(done) or i18n.t("нечего"), why=i18n.t("сброс всех изменений"),
                              source=i18n.t("утилиты"))
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
        dim_ok = False
        try:
            self.apply_values(0.5, self._quiet_prev["contrast"],
                              self._quiet_prev["gamma"],
                              self._quiet_prev["temperature"], save=False)
            # apply_values ставит флаг факта: гамма может быть отклонена (HDR)
            dim_ok = bool(getattr(self, "_gamma_applied_ok", False))
        except Exception:  # noqa: BLE001
            dim_ok = False
        power = getattr(self, "_power_page", None)
        eco_ok = power is not None
        if power is not None:
            try:
                power.apply_profile("eco", source=i18n.t("тихий час"))
            except Exception:  # noqa: BLE001
                eco_ok = False
        # «активен» — значит попытка сделана: иначе тик будет пробовать каждые
        # несколько секунд и засыплет тревожными тостами ту же причину
        q["applied"] = True
        if not gd_profiles.save_quiet(q):
            self.set_status(i18n.t(
                "Тихий час: состояние не сохранено — файл quiet_hour.json недоступен для записи."),
                ok=False)
        if eco_ok and dim_ok:
            try:
                import gd_toast

                gd_toast.show_toast(i18n.t("toast.quiet_on"), "info")
            except Exception:  # noqa: BLE001
                pass
        else:
            # не врём про успех: тост сказал бы «ЭКО включён», которого нет
            self._report_failure(i18n.t(
                "toast.quiet_none") if not (eco_ok or dim_ok)
                else (i18n.t("toast.quiet_no_eco") if not eco_ok else i18n.t("toast.quiet_no_dim")))
        try:
            gd_journal.record("quiet_hour", f"{q.get('start')}–{q.get('end')}", None,
                              i18n.t("ЭКО CPU + минимум яркости"), why=i18n.t("тихий час"),
                              source=i18n.t("утилиты"))
        except Exception:  # noqa: BLE001
            pass

    def _quiet_release(self, q: dict):
        import gd_journal
        import gd_profiles

        prev = self._quiet_prev or {"brightness": 1.0, "contrast": 1.0,
                                    "gamma": 1.0, "temperature": 6500}
        restore_ok = False
        try:
            self.apply_values(prev["brightness"], prev["contrast"], prev["gamma"],
                              prev["temperature"], save=False)
            restore_ok = bool(getattr(self, "_gamma_applied_ok", False))
        except Exception:  # noqa: BLE001
            restore_ok = False
        power = getattr(self, "_power_page", None)
        if power is not None and getattr(power, "client", None) is not None:
            try:
                power.apply_profile("restore", source=i18n.t("тихий час"))
            except Exception:  # noqa: BLE001
                pass
        q["applied"] = False
        if not gd_profiles.save_quiet(q):
            self.set_status(i18n.t(
                "Тихий час: состояние не сохранено — файл quiet_hour.json недоступен для записи."),
                ok=False)
        self._quiet_prev = None
        if restore_ok:
            try:
                import gd_toast

                gd_toast.show_toast(i18n.t("toast.quiet_off"), "info")
            except Exception:  # noqa: BLE001
                pass
        else:
            # «состояние возвращено» — неправда, если яркость осталась сниженной
            self._report_failure(i18n.t("toast.quiet_keep_dim"))
        try:
            gd_journal.record("quiet_hour", i18n.t("выход из окна"), i18n.t("ЭКО + минимум яркости"),
                              None, why=i18n.t("тихий час завершён"), source=i18n.t("утилиты"))
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

    def _gpu_autostart_check(self):
        """Старт приложения: вернуть лимит GPU по настройке «применять при запуске».

        Вызывается один раз с задержкой (вкладке нужно пережить первый опрос
        nvidia-smi). Решение принимает вкладка GPU: план хранится в
        settings.gpu_auto_pl, галочка — в settings.gpu_apply_on_start
        (см. gd_gpu_ui.ensure_autostart_pl).
        """
        gpu = getattr(self, "_gpu_page", None)
        if gpu is None:
            return
        try:
            gpu.ensure_autostart_pl()
        except Exception:  # noqa: BLE001
            pass

    def bind_hotkeys(self):
        self.hotkey_manager.nextPreset.connect(self.next_preset)
        self.hotkey_manager.prevPreset.connect(self.prev_preset)
        self.hotkey_manager.resetRequested.connect(self.reset_all)
        self.hotkey_manager.catVisionRequested.connect(self.toggle_cat_vision)
        self.hotkey_manager.presetRequested.connect(self.apply_preset_by_index)
        self.hotkey_manager.paletteRequested.connect(self.open_palette)
        self.hotkey_manager.perfToggle.connect(self._on_perf_toggle)
        self.hotkey_manager.error.connect(self.set_status)

        self.hotkey_manager.register(self.config)

    def _on_perf_toggle(self):
        """Горячая клавиша: переключить ограничения CPU и GPU (пара профилей).

        Что применять — settings.perf_profiles (диалог «Глобальные клавиши»);
        пустой профиль в блоке = «этот блок не трогаем». Звук — характерный
        отклик на нажатие (settings.perf_sound): низкий — ограничение включено,
        высокий — снято.
        """
        import gd_journal

        report = gd_perf.toggle(self.config, getattr(self, "_power_page", None),
                                getattr(self, "_gpu_page", None))
        # галка трея — по факту, а не по намерению: клик по пункту меню сам
        # переключает галочку, а половина пары могла и не примениться
        tray = getattr(self, "tray", None)
        if tray is not None:
            try:
                tray.set_perf_limited(report["limited"] if report["ok"]
                                      else not report["limited"])
            except Exception:  # noqa: BLE001
                pass
        if not report["ok"]:
            fail_text = (i18n.t("Переключатель производительности: {v0}",
                                v0="; ".join(report["errors"])) if report["errors"]
                         else i18n.t("Переключатель производительности: профили не заданы"))
            self.set_status(fail_text, ok=False)
            # Тост: горячая клавиша и пункт трея срабатывают и при свёрнутом окне —
            # без него отклик был бы только звуком (а его можно выключить).
            import gd_toast

            gd_toast.show_toast(fail_text, "warn")
            return

        self._save_config()

        state_text = (i18n.t("ограничение включено") if report["limited"]
                      else i18n.t("ограничение снято"))
        self._play_perf_sound(report["limited"])

        status = i18n.t("Переключатель производительности: {v0}", v0=state_text)
        if report["errors"]:
            status += " — " + "; ".join(report["errors"])
        partial = bool(report["errors"])
        self.set_status(status, ok=not partial)
        import gd_toast

        gd_toast.show_toast(status, "warn" if partial else "ok")

        try:
            gd_journal.record(
                "perf_toggle", state_text,
                i18n.t("Ограничение") if not report["limited"] else i18n.t("Производительность"),
                i18n.t("Ограничение") if report["limited"] else i18n.t("Производительность"),
                why=i18n.t("горячая клавиша"), source=i18n.t("горячая клавиша"))
        except Exception:  # noqa: BLE001
            pass

    def _play_perf_sound(self, limited: bool) -> None:
        """Характерный звук переключателя (выключается настройкой perf_sound)."""
        try:
            if bool(self.config.get("settings", {}).get("perf_sound", True)):
                gd_perf.play_sound(limited)
        except Exception:  # noqa: BLE001
            pass

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
        # ручная правка при активном «кошачьем зрении» снимает режим без отката:
        # значения, которые пользователь только что выставил, — его решение,
        # а возврат к прежним при следующем клике был бы сюрпризом
        self._cancel_cat_vision(announce=True)

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
        self._gamma_applied_ok = bool(ok)
        # цель сторожа — что поставлено на экран, а не что в конфиге: «кошачье
        # зрение» меняет гамму без записи в конфиг (save=False), и сторож должен
        # защищать именно его, а не откатывать к сохранённому значению
        self._gamma_applied_values = (brightness, contrast, gamma, temperature)

        if save:
            self.config["settings"]["current"] = {
                "brightness": round(brightness, 4),
                "contrast": round(contrast, 4),
                "gamma": round(gamma, 4),
                "temperature": int(temperature),
            }
            self._save_config()

        if not ok:
            # отказ подсвечиваем тревогой: тост здесь не нужен — гамму двигают
            # слайдером, а он в момент отказа на экране (иначе спамил бы на каждый шаг)
            self.set_status(self._gamma_failure_status(), ok=False)

    def _gamma_failure_status(self) -> str:
        """Честно объясняем отказ: чаще всего Windows не даёт менять гамму при HDR."""
        if gd_display.hdr_active():
            return i18n.t(
                "Гамма не применяется: на мониторе включён HDR — Windows не разрешает менять гамму, пока HDR активен."
            )

        return i18n.t("Гамма не применяется: система или драйвер блокируют изменение гаммы.")


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

        # пресет — осознанный выбор значений: режим «кошачье зрение» снимаем
        # без отката, иначе он вернёт прежние значения и затрёт пресет
        self._cancel_cat_vision()

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
        self._save_config()

        self.refresh_preset_combo()
        self.preset_combo.setCurrentIndex(len(self.config["presets"]) - 1)

        self.hotkey_manager.register(self.config)
        self.set_status(i18n.t("Пресет сохранён: {v0}", v0=name))

    def delete_current_preset(self):
        index = self.preset_combo.currentIndex()

        if index < 0 or index >= len(self.config["presets"]):
            return

        if len(self.config["presets"]) <= 1:
            gd_dialog.notify(
                self,
                i18n.t("Невозможно удалить"),
                i18n.t("Нельзя удалить последний пресет."),
            )
            return

        name = self.config["presets"][index].get("name", "Unnamed")

        answer = gd_dialog.confirm(
            self,
            i18n.t("Удалить пресет"),
            i18n.t("Удалить пресет «{v0}»?", v0=name),
            confirm_text=i18n.t("Удалить"),
            destructive=True,
        )

        if not answer:
            return

        del self.config["presets"][index]

        self.current_preset_index = max(
            0,
            min(index, len(self.config["presets"]) - 1),
        )

        self._save_config()

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

        self._save_config()
        self.refresh_preset_combo()
        self.preset_combo.setCurrentIndex(index)

        self.hotkey_manager.register(self.config)
        self.set_status(i18n.t("Горячая клавиша пресета сохранена"))

    def edit_global_hotkeys(self):
        hotkeys = self.config["settings"].get("hotkeys", {})
        perf = self.config["settings"].get("perf_profiles")
        sound = bool(self.config["settings"].get("perf_sound", True))
        dialog = HotkeySettingsDialog(hotkeys, perf, sound, self)

        if dialog.exec_() == QDialog.Accepted:
            self.config["settings"]["hotkeys"] = dialog.result_hotkeys()
            self.config["settings"]["perf_profiles"] = dialog.result_perf()
            self.config["settings"]["perf_sound"] = dialog.result_sound()
            self._save_config()
            self.hotkey_manager.register(self.config)
            self.set_status(i18n.t("Глобальные клавиши обновлены"))

    def open_support_link(self):
        QDesktopServices.openUrl(QUrl(SUPPORT_URL))

    def open_second_link(self):
        QDesktopServices.openUrl(QUrl(SECOND_URL))

    def reset_all(self):
        self.cat_active = False
        self.cat_button.setChecked(False)
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

    def _cancel_cat_vision(self, announce: bool = False) -> None:
        """Снять «кошачье зрение» без отката значений (ручная правка/пресет).

        Возврат к прежним значениям был бы сюрпризом: пользователь уже выбрал
        новые — слайдером или пресетом. Вызывающий сам решает, писать ли статус.
        """
        if not self.cat_active:
            return

        self.cat_active = False
        self.cat_button.setChecked(False)
        self.cat_previous_values = None

        if announce:
            self.set_status(i18n.t("Кошачье зрение снято: значения изменены вручную."))

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


    def gamma_watch_tick(self):
        """Сторож гаммы: сверяет фактическую рампу с последней применённой.

        Сверка идёт с ``_gamma_applied_values`` (что приложение поставило на
        экран), а не с конфигом: «кошачье зрение» меняет гамму, не сохраняясь в
        настройки, — сторож обязан защищать и его. Работает, только когда гамма
        не дефолтная (её есть смысл защищать), последнее применение удалось, не
        идёт анимация и рампу вообще можно прочитать. Сброс системой возвращаем
        и говорим об этом в статусе.
        """
        if self.gamma_animation_timer.isActive() or not self._gamma_applied_ok:
            return

        applied = self._gamma_applied_values

        if applied is None:
            return

        brightness, contrast, gamma, temperature = applied
        temperature = int(temperature)

        if (abs(brightness - 1.0) < 0.001 and abs(contrast - 1.0) < 0.001
                and abs(gamma - 1.0) < 0.001 and temperature == 6500):
            return

        actual = self.gamma_controller.read_current()

        if actual is None:
            return

        target = self.gamma_controller.build_ramp(brightness, contrast, gamma, temperature)

        if self.gamma_controller.ramp_max_diff(actual, target) <= GAMMA_WATCH_TOLERANCE:
            return

        if self.gamma_controller.apply(brightness, contrast, gamma, temperature):
            self.set_status(i18n.t("Гамма восстановлена после сброса системой."))


    def set_status(self, text: str, ok: bool = True):
        """Строка статуса в шапке окна. `ok=False` — честный отказ: цветом тревоги.

        Раньше метод принимал только текст, а переключатель производительности
        передавал `ok=`: при незаданных профилях вместо «профили не заданы» из
        слота горячей клавиши летел необработанный TypeError.
        """
        self.status_label.setText(text)
        self.status_label.setStyleSheet("" if ok else f"color: {th.WARN};")

    def _config_recovery_note(self) -> None:
        """Честный старт: если конфиг пришлось заменить дефолтом — сказать об этом."""
        if CONFIG_RECOVERY == "backup":
            self.set_status(i18n.t(
                "presets.json был повреждён — загружены настройки по умолчанию; "
                "копия сохранена как presets.json.bak"))
        elif CONFIG_RECOVERY == "no_backup":
            self.set_status(i18n.t(
                "presets.json был повреждён — загружены настройки по умолчанию"))

    def _save_config(self, *, now: bool = False) -> None:
        """Сохранить конфиг: одним сбросом после паузы или сразу (now=True).

        Немедленная запись нужна выходу из приложения; всё остальное (слайдеры,
        переключатели, пресеты) откладывается и схлопывается в одну — иначе
        перетаскивание ручки писало бы presets.json десятки раз в секунду.
        """
        if now:
            self._config_save_timer.stop()
            self._config_pending = True
            self._flush_config_save()
            return

        self._config_pending = True
        self._config_save_timer.start()

    def _flush_config_save(self) -> None:
        """Записать отложенный конфиг; отказ — честно в статус, без исключений."""
        if not self._config_pending:
            return

        self._config_pending = False

        if save_config(self.config) is False:
            self.set_status(i18n.t(
                "Настройки не сохранены: presets.json недоступен для записи."))

    def on_close_on_x_changed(self, state):
        self.config["settings"]["close_on_x"] = state == Qt.Checked
        self._save_config()

        if state == Qt.Checked:
            self.set_status(i18n.t("Выход из приложения"))
        else:
            self.set_status(i18n.t("Свернуть в трей при закрытии окна"))

    # -- автозапуск с Windows (галочка живёт на вкладке «Утилиты») -----------

    def start_with_windows(self) -> bool:
        """Автозапуск включён фактически: файл в «Автозагрузке» есть.

        Смотрим на файл, а не на флаг в конфиге: ярлык могли удалить вручную
        или записать не удалось. Тогда честнее показать галочку снятой.
        """
        try:
            return get_startup_shortcut_path().exists()
        except Exception:  # noqa: BLE001
            return False

    def set_start_with_windows(self, on: bool) -> str:
        """Включить/выключить автозапуск: файл автозагрузки + флаг в конфиге.

        Возвращает текст ошибки или "" — вкладка «Утилиты» зовёт это из галочки.
        Флаг пишется только при успехе: обещать автозапуск, который не встал,
        нельзя.
        """
        on = bool(on)
        try:
            if on:
                enable_startup()
            else:
                disable_startup()
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            self.set_status(i18n.t("Автозапуск не изменён: {v0}", v0=err), ok=False)
            return err

        self.config.setdefault("settings", {})["start_with_windows"] = on
        self._save_config()
        self.set_status(i18n.t("Автозапуск включён") if on
                        else i18n.t("Автозапуск выключен"))
        return ""

    # -- перенос настроек и проверка обновлений (вкладка «Утилиты») ---------

    def import_app_settings(self, imported: dict) -> tuple[bool, str]:
        """Применить настройки приложения из файла переноса.

        Переносятся только знакомые разделы — «settings» и пресеты гаммы: окно,
        геометрия и служебные метки на другой машине смысла не имеют, а чужие
        ключи в конфиге — источник тихих поломок. Прежний файл сохраняется рядом
        как ``presets.before-import.json``: импорт должен быть отменяемым.

        Возврат ``(применено, текст для человека)`` — текст показывает панель
        «Утилиты», поэтому он же говорит и про перезапуск.
        """
        if not isinstance(imported, dict) or not imported:
            return False, i18n.t("в файле нет настроек")

        skipped: list[str] = []
        # Опорная структура: умолчания + рантайм-настройки, а поверх — то, что
        # уже стоит в конфиге: импорт не должен сбрасывать ключи, которых в
        # файле нет (язык этой машины, её путь zapret и т.п.).
        target = json.loads(json.dumps(DEFAULT_CONFIG["settings"]))
        for key, value in RUNTIME_SETTINGS.items():
            target.setdefault(key, json.loads(json.dumps(value)))
        for key, value in (self.config.get("settings") or {}).items():
            try:
                target[key] = json.loads(json.dumps(value))
            except (TypeError, ValueError):
                target[key] = value        # не JSON-значение — оставляем как есть
        incoming = {key: value for key, value in (imported.get("settings") or {}).items()
                    if key not in SETTINGS_NOT_TRANSFERABLE}
        merge_known_into(target, incoming, skipped)
        presets, dropped = self._sanitize_presets(imported.get("presets"))

        backup: Path | None = None
        source = config_path()
        try:
            if source.exists():
                import shutil

                backup = source.with_name("presets.before-import.json")
                shutil.copyfile(source, backup)
        except OSError:
            backup = None

        # Пишем копию, а не живой конфиг: если запись не удалась, состояние в
        # памяти остаётся прежним — иначе окно показывало бы одно, а на диске
        # лежало другое.
        try:
            candidate = json.loads(json.dumps(self.config))
        except (TypeError, ValueError) as exc:   # в конфиг попало не-JSON-значение
            return False, i18n.t("не удалось прочитать текущие настройки: {v0}", v0=exc)
        candidate["settings"] = target
        if presets is not None:
            candidate["presets"] = presets
        if not save_config(candidate):
            return False, i18n.t("не удалось записать файл настроек")
        self.config.clear()
        self.config.update(candidate)      # тот же dict: на него смотрят другие модули

        note = i18n.t("Перенесено: настройки и пресеты гаммы ({v0}).",
                      v0=len(candidate.get("presets") or []))
        if dropped:
            note += " " + i18n.t("Пресетов пропущено (битые записи): {v0}.", v0=dropped)
        if skipped:
            head = ", ".join(skipped[:4]) + ("…" if len(skipped) > 4 else "")
            note += " " + i18n.t("Незнакомые ключи пропущены: {v0}.", v0=head)
        if backup is not None:
            note += " " + i18n.t("Копия прежних настроек: {v0}.", v0=backup.name)
        note += " " + i18n.t("Настройки вступят в силу после перезапуска Gammadrilon.")
        try:
            import gd_journal

            gd_journal.record("settings", i18n.t("Импорт настроек"), None, note,
                              source=i18n.t("Утилиты"))
        except Exception:  # noqa: BLE001 — журнал не должен ронять импорт
            pass
        return True, note

    @staticmethod
    def _sanitize_presets(raw) -> tuple[list[dict] | None, int]:
        """Пресеты гаммы из чужого файла: понятные чиним, мусор считаем и отбрасываем."""
        if not isinstance(raw, list):
            return None, 0
        clean: list[dict] = []
        dropped = 0
        for item in raw:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                dropped += 1
                continue
            preset = {"name": str(item["name"]).strip()[:40]}
            for key in ("brightness", "contrast", "gamma"):
                preset[key] = clamp(safe_float(item.get(key), 1.0), 0.2, 3.0)
            preset["temperature"] = int(clamp(safe_float(item.get("temperature"), 6500), 3000, 10000))
            hotkey = item.get("hotkey")
            preset["hotkey"] = hotkey.strip() if isinstance(hotkey, str) else ""
            clean.append(preset)
        return (clean or None), dropped

    def set_check_updates(self, on: bool) -> None:
        """Галочка «проверять обновления при запуске»: по умолчанию выключена."""
        self.config.setdefault("settings", {})["check_updates"] = bool(on)
        self._save_config()

    def check_updates(self, silent: bool = False) -> None:
        """Спросить GitHub о последнем релизе — в фоне, и только по галочке.

        ``silent`` (проверка при запуске) молчит, когда новостей нет: человек не
        просил отчёт, он просил не пропустить новый релиз.
        """
        import gd_bg

        if gd_bg.is_running(getattr(self, "_updates_worker", None)):
            return
        utils = getattr(self, "_utils_page", None)
        if utils is not None:
            utils.set_update_status(i18n.t("Спрашиваю GitHub…"))
        self._updates_worker = gd_bg.submit(
            gd_update.fetch_latest, lambda res: self._updates_done(res, silent), parent=self)

    def _updates_done(self, res, silent: bool) -> None:
        """Результат проверки обновлений пришёл в GUI-поток: показать честно."""
        import gd_toast

        utils = getattr(self, "_utils_page", None)
        if not isinstance(res, dict) or not res.get("ok"):
            error = (res or {}).get("error") if isinstance(res, dict) else str(res)
            text = i18n.t("Проверка обновлений не удалась: {v0}",
                          v0=error or i18n.t("неизвестная причина"))
            if utils is not None:
                utils.set_update_status(text, ok=False)
            if not silent:
                gd_toast.show_toast(text, "warn")
            return
        tag = str(res.get("tag") or "")
        if gd_update.is_newer(tag, APP_VERSION):
            text = i18n.t("Доступна новая версия {v0} (установлена {v1}).", v0=tag, v1=APP_VERSION)
            if utils is not None:
                utils.set_update_status(text, ok=True, url=str(res.get("url") or ""))
            gd_toast.show_toast(i18n.t("toast.update_available", v0=tag), "info")
            try:
                import gd_journal

                gd_journal.record("update", i18n.t("Доступна новая версия"), None, tag,
                                  source=i18n.t("Проверка обновлений"))
            except Exception:  # noqa: BLE001
                pass
        else:
            text = i18n.t("Установлена последняя версия ({v0}).", v0=APP_VERSION)
            if utils is not None:
                utils.set_update_status(text, ok=True)
            if not silent:
                gd_toast.show_toast(text, "ok")

    def _tray_reset_everything(self) -> None:
        """«Вернуть всё как было» из трея — с тем же вопросом, что и в «Утилитах»."""
        answer = gd_dialog.confirm(
            None, i18n.t("Сбросить все изменения"),
            i18n.t("Вернуть питание CPU/GPU, DNS, правила, автоочистку, таймер и гамму "
                   "к состоянию системы по умолчанию?"),
            confirm_text=i18n.t("Сбросить всё"), destructive=True)
        if not answer:
            return
        self.reset_everything()

    # -- видимость окна и темп фоновой телеметрии --------------------------

    def _sync_metrics_activity(self) -> None:
        """Сказать сборщику метрик, что сейчас видит пользователь.

        Живые цифры стоят внешних процессов: `nvidia-smi` — ~40 мс CPU за запуск,
        чтение DNS — ~90 мс. Поэтому открытая вкладка GPU учащает опрос, а окно,
        ушедшее в трей, — замедляет (см. ``gd_metrics.set_activity``).
        """
        try:
            import gd_metrics

            visible = self.isVisible() and not self.isMinimized()
            gd_metrics.set_activity(visible=visible,
                                    gpu_focus=self._key_for_widget(self.tabs.currentWidget()) == "tab.gpu")
        except Exception:  # noqa: BLE001
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_metrics_activity()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_metrics_activity()

    def changeEvent(self, event):
        """Сворачивание меняет не видимость, а состояние окна — ловим и его."""
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._sync_metrics_activity()

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

        # отложенная запись конфига не должна потеряться при выходе
        self._save_config(now=True)

        try:
            self.hotkey_manager.clear()
        except Exception:
            pass

        self.gamma_watch_timer.stop()

        try:
            self.gamma_controller.reset()
        except Exception:
            pass

        # откат профиля питания (если вкладка «Питание CPU» использовалась)
        try:
            if getattr(self, "_power_page", None) is not None:
                # через вкладку: она снимает сторожа смерти компаньона — после
                # выхода «компаньона нет» не новость, а ожидаемый результат
                self._power_page.shutdown_for_exit()
        except Exception:
            pass

        # остановка GPU-хелпера: лимит остаётся по настройке (по умолчанию),
        # неподтверждённое изменение откатывается всегда
        try:
            if getattr(self, "_gpu_page", None) is not None:
                self._gpu_page.shutdown_for_exit()
        except Exception:
            pass

        # обход DPI: если хелпер поднимала только вкладка zapret — закрыть через её клиент
        try:
            zapret_page = getattr(self, "_zapret_page", None)
            if zapret_page is not None and getattr(zapret_page, "client", None) is not None:
                zapret_page.client.shutdown_async(restore=True)
        except Exception:
            pass

        # локальный API: сервер не должен пережить приложение
        try:
            self.stop_agent_api(remember=False)
        except Exception:
            pass

        # дождаться фоновых задач: иначе Qt удалит ещё работающий QThread
        try:
            import gd_bg

            gd_bg.wait_all(2000)
        except Exception:
            pass
        try:
            import gd_metrics

            gd_metrics.stop()
        except Exception:
            pass

        QApplication.quit()

# Единый стиль приложения живёт в gd_theme (токены + QSS): раньше здесь и в
# gd_theme.py лежали два похожих шаблона, и правила в них расходились (размеры
# чекбоксов, вылет ползунков, фон таблиц). Теперь источник один — правка стиля
# в одном месте видна и в главном окне, и на страницах, и в диалогах.
def build_app_stylesheet() -> str:
    """QSS приложения из активной палитры и токенов (см. gd_theme)."""
    return th.build_stylesheet()


APP_STYLESHEET = build_app_stylesheet()

def main():
    if sys.platform != "win32":
        print("This application works only on Windows.")
        return

    # язык нужен раньше окна: сообщение «уже запущено» тоже должно быть переведено
    i18n.init_for_process()

    if is_another_instance_running():
        _warn_already_running()
        return

    install_crash_handlers()

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    app.setQuitOnLastWindowClosed(False)

    # общий фоновой сборщик метрик: UI читает кэш и не дёргает nvidia-smi/netsh
    try:
        import gd_metrics

        gd_metrics.start()
        app.aboutToQuit.connect(gd_metrics.stop)
    except Exception:  # noqa: BLE001
        pass

    icon_path = resource_path(ICON_FILE)

    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()

    if window.config.get("settings", {}).get("check_updates", False):
        # тихая проверка: человек просил не пропустить новый релиз, а не отчёт
        window.check_updates(silent=True)

    if window.config.get("settings", {}).get("start_with_windows", False):
        try:
            enable_startup()
        except Exception as exc:  # noqa: BLE001
            # настройка обещает автозапуск — молчание здесь было бы ложью
            window.set_status(i18n.t(
                "Автозапуск включён в настройках, но ярлык создать не удалось: {v0}",
                v0=exc))
            try:
                import gd_journal

                gd_journal.record("startup", i18n.t("Автозапуск"), None,
                                  i18n.t("не создан"), why=str(exc), reversible=False)
            except Exception:  # noqa: BLE001
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
