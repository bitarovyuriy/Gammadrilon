import ctypes
import json
import math
import os
import shutil
import sys
import time
from ctypes import wintypes
from pathlib import Path

import gd_boot
import gd_paths
import gd_proc


# --- PyQt5 и не-ASCII путь ----------------------------------------------------
# Встроенный в PyQt5 файл qt.conf Qt разбирает как Latin-1 INI: если путь к
# окружению содержит не-ASCII символы (например, D:\Сlaude\...), путь к
# плагинам превращается в мусор и QApplication падает без сообщения об ошибке.
# Переменную окружения QT_PLUGIN_PATH Qt читает корректно — выставляем заранее.
# В сборке PyInstaller то же самое случается в onefile: он распаковывается в
# %TEMP%\_MEIxxxx, а имя пользователя у людей часто русское. Свои плагины
# важнее чужих: переменная процесса — только наше приложение.
try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_candidates = []
    if _site:
        _qt_candidates.append(os.path.join(_site, "PyQt5", "Qt5", "plugins"))
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        _qt_candidates.append(os.path.join(_meipass, "PyQt5", "Qt5", "plugins"))
    for _qt_plugins in _qt_candidates:
        if not _qt_plugins.isascii() and os.path.isdir(_qt_plugins):
            os.environ["QT_PLUGIN_PATH"] = _qt_plugins
            _qt_platforms = os.path.join(_qt_plugins, "platforms")
            if os.path.isdir(_qt_platforms):
                os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = _qt_platforms
            break
except Exception:  # noqa: BLE001
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
    QListWidget,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QSystemTrayIcon,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)


APP_NAME = gd_paths.APP_NAME
APP_VERSION = "v3.5"
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
import gd_perf  # noqa: E402
import gd_display  # noqa: E402
import gd_update  # noqa: E402
import gd_hotkeys  # noqa: E402
import gd_winwatch  # noqa: E402
import gd_ddc  # noqa: E402
import gd_hz  # noqa: E402
import gd_rollback  # noqa: E402
import gd_bg  # noqa: E402

SLIDER_GROOVE_HEIGHT = 6
SLIDER_HANDLE_SIZE = 22

# сторож гаммы: система может сбросить рампу (сон, блокировка, смена режима) —
# раз в несколько секунд сверяем факт с целью и возвращаем своё. Порог в
# единицах канала (0..65535): сброс к «ровной» кривой даёт тысячи, квантование
# драйвера — десятки.
GAMMA_WATCH_INTERVAL_MS = 4000
#: WM_QUERYENDSESSION — Windows спрашивает, можно ли завершать сеанс
WM_QUERYENDSESSION = 0x0011
#: сколько ждать после смены окна: игра применяет свой цветовой профиль не мгновенно
GAMMA_EVENT_DELAY_MS = 500
GAMMA_WATCH_TOLERANCE = 2000

# запись конфига откладывается и схлопывается: перетаскивание слайдера иначе
# писало бы файл на каждый тик (диск + антивирус на каждое переименование).
# Выход из приложения сбрасывает отложенное немедленно.
CONFIG_SAVE_DELAY_MS = 400

# иконки пунктов сайдбара (gd_icons)
SIDE_ICONS = {
    "tab.home": "home",
    "tab.cpu": "cpu",
    "tab.gpu": "gpu",
    "tab.gamma": "gamma",
    "tab.rules": "rules",
    "tab.utils": "utils",
    "tab.dns": "dns",
    "tab.zapret": "zapret",
    "tab.cs2": "cs2",
    "tab.diagnose": "journal",
}

# --- устойчивость: лог падений и глобальные обработчики -----------------------
_CRASH_KEEP = []   # держим файл-дескриптор живым: faulthandler пишет в него
_CRASH_LOG_MAX_BYTES = 512 * 1024   # лог не растёт бесконечно: обрезаем при старте
_INSTANCE_MUTEX = []   # хендл мьютекса «один экземпляр»: держим живым


def _crash_log_path() -> Path:
    base = gd_paths.ensure(gd_paths.log_dir())
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
    # путь к приложению живёт в gd_paths: там же решается портативный режим
    return gd_paths.app_dir()


def resource_path(relative: str) -> Path:
    """
    Путь к ресурсам внутри PyInstaller onefile.
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative

    return app_dir() / relative


def config_path() -> Path:
    """Настройки живут в gd_paths: портативно — рядом, иначе в ProgramData.

    Обёртка осталась для тестов и для кода, который импортирует её из main:
    единое правило путей должно быть одно на все процессы.
    """
    return gd_paths.config_path()

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
            "shadow": 0,
        },
        "hotkeys": {
            "next_preset": "page up",
            "prev_preset": "page down",
            "reset": "ctrl+alt+z",
            "cat_vision": "scroll lock",
            "perf_toggle": "ctrl+alt+p",
            # стоп-кран: вернуть всё как было (питание, DNS, правила, таймер, гамма).
            # Буква R — рядом с аварийным сбросом гаммы Z, но другой рукой не спутать
            "reset_everything": "ctrl+alt+r",
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
        # автопрофили гаммы: [{"exe": "cs2.exe", "preset": 2}] — при переходе
        # в окно игры включается пресет, при выходе возвращается прежний
        "auto_profiles": [],
        # автопрофили выключены целиком, пока пользователь не добавит правило
        "auto_profiles_on": True,
        # автолимит GPU: план (последнее подтверждённое значение) и галочка
        # «применять при запуске» (приложение вернёт план после перезагрузки)
        "gpu_auto_pl": None,
        "gpu_apply_on_start": False,
        # проверка обновлений ходит в сеть — по умолчанию выключена (обещание
        # «без облака и телеметрии» остаётся в силе, пока галочку не поставят)
        "check_updates": False,
        # обход DPI (zapret): авто-проверка релиза Flowseal не чаще суток и
        # отметка последней проверки, чтобы вкладка не дёргала GitHub API
        "zapret_auto_update_check": True,
        "zapret_update_last_check": 0,
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
    # температуры через LibreHardwareMonitor: хелпер поднимается отдельно
    "sensors": True,
    # какой канал платы считать VRM (0 — не показывать)
    "sensors_vrm_channel": 0,
    "zapret_path": "",
}

#: Ключи, которые переносить нельзя: это состояние этой машины и этого
#: человека, а не настройка («первый запуск пройден здесь» — и должен остаться;
#: «когда последний раз стучались в GitHub» — тоже: на новой машине проверка
#: должна пройти сама, а не ждать сутки из-за чужой отметки).
SETTINGS_NOT_TRANSFERABLE = ("first_run_done", "updates_last_check", "last_boot_ms",
                             # выбор монитора — про конкретную машину: на другой
                             # индекс укажет в пустоту
                             "ddc_monitor")


def _good_copy_path(path: Path) -> Path:
    return path.with_name(path.name + ".good")


def usable_monitors(ddc) -> list:
    """Мониторы, которые реально умеют яркость по DDC/CI.

    Отдельной функцией: список нужен и выпадашке, и обработчику выбора, а
    проверять его в тесте без Qt проще, чем виджет.
    """
    monitors = getattr(ddc, "monitors", None) or []
    return [item for item in monitors if getattr(item, "supported", False)]


_TASKBAR_CREATED: int | None = None


def _taskbar_created_message() -> int:
    """Номер сообщения, которое Windows рассылает после перезапуска Explorer.

    Регистрируется один раз на процесс; 0 — если система не ответила (тогда
    проверка просто не сработает, а не уронит окно).
    """
    global _TASKBAR_CREATED
    if _TASKBAR_CREATED is None:
        try:
            _TASKBAR_CREATED = int(ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated"))
        except Exception:  # noqa: BLE001
            _TASKBAR_CREATED = 0
    return _TASKBAR_CREATED


def save_config(data: dict) -> bool:
    """Записать конфиг атомарно: обрыв записи не оставит обрезанный JSON.

    Пишем во временный файл рядом и заменяем им presets.json (`os.replace` на
    NTFS атомарен), с fsync — чтобы переименование не обогнало данные при
    внезапном выключении. Ошибки файловой системы не бросаются наружу: из слота
    Qt необработанное исключение завершает приложение (PyQt5), а занятый чужим
    редактором файл — не повод падать. Честный ответ — False; вызывающий сам
    решает, что сказать пользователю.

    Перед заменой текущий файл копируется в `presets.json.good` — одна
    последняя заведомо целая версия. Из неё восстанавливаются настройки, если
    файл окажется битым (ручная правка, оборванная запись, антивирус).
    """
    path = config_path()
    tmp = path.with_name(path.name + ".tmp")

    try:
        with tmp.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        try:
            if path.is_file() and path.stat().st_size > 0:
                shutil.copy2(path, _good_copy_path(path))
        except OSError:
            pass          # копия — страховка, а не обязательный шаг записи
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def load_config() -> dict:
    # Старые сборки держали presets.json рядом с exe. Перед чтением переносим
    # его в data_dir один раз — иначе установленная версия начинает с дефолтов.
    gd_paths.migrate_legacy_config()
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
        # «тихо исчезают». Сначала пробуем восстановить из последней целой
        # копии: потерять наборы пресетов из-за одного битого байта обидно.
        global CONFIG_RECOVERY
        try:
            path.replace(path.with_name(path.name + ".bak"))
            CONFIG_RECOVERY = "backup"
        except OSError:
            CONFIG_RECOVERY = "no_backup"

        restored = None
        try:
            good = _good_copy_path(path)
            if good.is_file():
                with good.open("r", encoding="utf-8") as file:
                    candidate = json.load(file)
                if isinstance(candidate, dict):
                    restored = candidate
        except Exception:  # noqa: BLE001 — копия тоже может быть битой
            restored = None

        if restored is not None:
            CONFIG_RECOVERY = "restored"
            data = restored
        else:
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
    settings.setdefault("zapret_auto_update_check", True)
    settings.setdefault("zapret_update_last_check", 0)

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
        preset.setdefault("shadow", 0)
        preset.setdefault("ddc", -1)
        preset.setdefault("hotkey", "")

        if not isinstance(preset["name"], str) or not preset["name"].strip():
            preset["name"] = "Unnamed"
        if not isinstance(preset["hotkey"], str):
            preset["hotkey"] = ""

        preset["brightness"] = clamp(safe_float(preset["brightness"], 1.0), 0.5, 2.0)
        preset["contrast"] = clamp(safe_float(preset["contrast"], 1.0), 0.3, 2.0)
        preset["gamma"] = clamp(safe_float(preset["gamma"], 1.0), 0.5, 2.5)
        preset["temperature"] = int(clamp(safe_int(preset["temperature"], 6500), 3000, 10000))
        preset["shadow"] = int(clamp(safe_int(preset["shadow"], 0), 0, 100))
        preset["ddc"] = int(clamp(safe_int(preset["ddc"], -1), -1, 100))

    current = settings["current"]
    current["brightness"] = clamp(safe_float(current.get("brightness", 1.0), 1.0), 0.5, 2.0)
    current["contrast"] = clamp(safe_float(current.get("contrast", 1.0), 1.0), 0.3, 2.0)
    current["gamma"] = clamp(safe_float(current.get("gamma", 1.0), 1.0), 0.5, 2.5)
    current["temperature"] = int(clamp(safe_int(current.get("temperature", 6500), 6500), 3000, 10000))
    current["shadow"] = int(clamp(safe_int(current.get("shadow", 0), 0), 0, 100))

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
        shadow: float = 0.0,
    ) -> tuple[list[int], list[int], list[int]]:
        brightness = clamp(brightness, 0.5, 2.0)
        contrast = clamp(contrast, 0.3, 2.0)
        gamma = clamp(gamma, 0.5, 2.5)
        temperature = int(clamp(temperature, 3000, 10000))
        shadow = clamp(float(shadow), 0.0, 0.25)

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

            # 4) Shadow Boost — поднимаем только тени, света не трогаем.
            # Подъём затухает кубом к белому и потому не даёт изломов и
            # «серой пелены»: (1-x)^3 на x=0.75 даёт всего 1.5% подъёма.
            # Максимум 0.25 держит кривую монотонной (производная ≥ 0.25).
            if shadow > 0.0 and 0.0 <= x < 1.0:
                x = x + shadow * (1.0 - x) ** 3

            # 5) Температура — каждый канал отдельно
            r_val = x * red_multiplier
            g_val = x * green_multiplier
            b_val = x * blue_multiplier

            # 6) Финальный клип — только сейчас
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
        shadow: float = 0.0,
    ) -> bool:
        gamma_array = self.ramp_array(
            *self.build_ramp(brightness, contrast, gamma, temperature, shadow))

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
    resetEverythingRequested = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.hotkeys = gd_hotkeys.Registrar()

    def set_window(self, hwnd: int):
        """Окно обязательно: WM_HOTKEY приходит именно ему, а не процессу."""
        self.hotkeys.set_window(hwnd)

    def clear(self):
        self.hotkeys.clear()

    def register(self, config: dict):
        self.clear()

        hotkeys = config.get("settings", {}).get("hotkeys", {})
        presets = config.get("presets", [])

        def safe_add(hotkey: str, callback):
            hotkey = str(hotkey).strip()

            if not hotkey:
                return

            self.hotkeys.register(hotkey, callback)

        safe_add(hotkeys.get("next_preset", "page up"), lambda: self.nextPreset.emit())
        safe_add(hotkeys.get("prev_preset", "page down"), lambda: self.prevPreset.emit())
        safe_add(hotkeys.get("reset", "ctrl+alt+z"), lambda: self.resetRequested.emit())
        safe_add(hotkeys.get("cat_vision", "scroll lock"), lambda: self.catVisionRequested.emit())
        safe_add(hotkeys.get("palette", "ctrl+alt+g"), lambda: self.paletteRequested.emit())
        safe_add(hotkeys.get("perf_toggle", "ctrl+alt+p"), lambda: self.perfToggle.emit())
        safe_add(hotkeys.get("reset_everything", "ctrl+alt+r"),
                 lambda: self.resetEverythingRequested.emit())

        for index, preset in enumerate(presets):
            preset_hotkey = str(preset.get("hotkey", "")).strip()

            if preset_hotkey:
                safe_add(preset_hotkey, lambda i=index: self.presetRequested.emit(i))

        for message in self.hotkeys.errors:
            self.error.emit(f"Hotkey error: {message}")
        self.hotkeys.errors.clear()

    def dispatch(self, hotkey_id: int) -> bool:
        return self.hotkeys.dispatch(hotkey_id)


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
        self.shadow = 0.0

        self.setMinimumHeight(135)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_values(self, brightness: float, contrast: float, gamma: float, temperature: int,
                   shadow: float = 0.0):
        self.brightness = brightness
        self.contrast = contrast
        self.gamma = gamma
        self.temperature = temperature
        self.shadow = shadow
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
            self.shadow,
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


# --------------------------------------------------------------------------
# Запись горячих клавиш: нажал сочетание — оно и записалось
# --------------------------------------------------------------------------
# Раньше в полях надо было вручную писать «ctrl+alt+1»: опечатка молча
# превращалась в неработающую клавишу. Здесь Qt-нажатие переводится в ту же
# строку, которую понимает библиотека keyboard (add_hotkey).

_MODIFIER_NAMES = (
    (Qt.ControlModifier, "ctrl"),
    (Qt.AltModifier, "alt"),
    (Qt.ShiftModifier, "shift"),
    (Qt.MetaModifier, "windows"),
)

_MODIFIER_KEYS = {Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr}

_SPECIAL_KEY_NAMES = {
    Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right",
    Qt.Key_Home: "home", Qt.Key_End: "end",
    Qt.Key_PageUp: "page up", Qt.Key_PageDown: "page down",
    Qt.Key_Insert: "insert", Qt.Key_Delete: "delete", Qt.Key_Backspace: "backspace",
    Qt.Key_Tab: "tab", Qt.Key_Return: "enter", Qt.Key_Enter: "enter",
    Qt.Key_Space: "space", Qt.Key_Escape: "esc",
    Qt.Key_Pause: "pause", Qt.Key_Print: "print screen", Qt.Key_SysReq: "print screen",
    Qt.Key_ScrollLock: "scroll lock", Qt.Key_CapsLock: "caps lock",
    Qt.Key_NumLock: "num lock", Qt.Key_Menu: "menu", Qt.Key_Help: "help",
}

_PUNCT_KEYS = {
    Qt.Key_Comma: ",", Qt.Key_Period: ".", Qt.Key_Semicolon: ";",
    Qt.Key_Slash: "/", Qt.Key_Backslash: "\\", Qt.Key_Minus: "-",
    Qt.Key_Equal: "=", Qt.Key_QuoteLeft: "`", Qt.Key_Apostrophe: "'",
    Qt.Key_QuoteDbl: "'", Qt.Key_BracketLeft: "[", Qt.Key_BracketRight: "]",
}

# с Shift Qt отдаёт не базовую клавишу, а её символ: shift+1 -> «!» -> «shift+1»
_SHIFTED_PUNCT_KEYS = {
    Qt.Key_Exclam: "1", Qt.Key_At: "2", Qt.Key_NumberSign: "3",
    Qt.Key_Dollar: "4", Qt.Key_Percent: "5", Qt.Key_AsciiCircum: "6",
    Qt.Key_Ampersand: "7", Qt.Key_Asterisk: "8", Qt.Key_ParenLeft: "9",
    Qt.Key_ParenRight: "0", Qt.Key_Underscore: "-", Qt.Key_Plus: "=",
    Qt.Key_BraceLeft: "[", Qt.Key_BraceRight: "]", Qt.Key_Bar: "\\",
    Qt.Key_Colon: ";", Qt.Key_QuoteDbl: "'", Qt.Key_Less: ",",
    Qt.Key_Greater: ".", Qt.Key_Question: "/", Qt.Key_AsciiTilde: "`",
}


def hotkey_modifier_names(modifiers) -> list:
    """Имена модификаторов в порядке, привычном для keyboard: ctrl+alt+shift."""
    return [name for flag, name in _MODIFIER_NAMES if modifiers & flag]


def _hotkeys_understand(token: str) -> bool:
    """Разбирает ли наша регистрация такое имя клавиши."""
    return gd_hotkeys.parse(token) is not None


def hotkey_key_name(key: int, text: str = "") -> str:
    """Имя клавиши для keyboard по Qt-коду. "" — клавиша не распознана."""
    name = _SPECIAL_KEY_NAMES.get(key)
    if name:
        return name
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        return f"f{key - Qt.Key_F1 + 1}"
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(key).lower()
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key)

    candidate = _SHIFTED_PUNCT_KEYS.get(key) or _PUNCT_KEYS.get(key)
    if not candidate:
        return ""
    if _hotkeys_understand(candidate):
        return candidate
    # на не-английской раскладке ASCII-пунктуация может не отдавать Vk-код —
    # берём символ, который реально напечатался («[» на RU — это «х»)
    typed = (text or "").strip().lower()
    if len(typed) == 1 and typed.isprintable() and _hotkeys_understand(typed):
        return typed
    return ""


def hotkey_from_key(key: int, modifiers, text: str = "") -> str:
    """Собрать строку горячей клавиши из Qt-нажатия. "" — только модификаторы."""
    name = hotkey_key_name(key, text)
    if not name:
        return ""
    return "+".join(hotkey_modifier_names(modifiers) + [name])


class HotkeyEdit(QLineEdit):
    """Поле горячей клавиши: нажми сочетание — оно запишется само.

    Поле только для чтения: ручной ввод названий убран, Esc отменяет запись,
    Backspace/Delete очищает. Крестик справа делает то же, что Backspace.
    """

    def __init__(self, value: str = "", parent=None):
        super().__init__(parent)
        self._value = str(value or "").strip()
        self._backup = self._value
        self._recording = False

        self.setReadOnly(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip(i18n.t(
            "Нажмите сочетание — оно запишется само. Esc — отмена, Backspace — очистить."))

        self._clear_action = self.addAction(
            self.style().standardIcon(QStyle.SP_LineEditClearButton),
            QLineEdit.TrailingPosition,
        )
        self._clear_action.setToolTip(i18n.t("Очистить"))
        self._clear_action.triggered.connect(self.clear_hotkey)
        self._show_value()

    def text(self) -> str:
        return self._value

    def setText(self, value: str) -> None:
        self._value = str(value or "").strip()
        self._backup = self._value
        self._show_value()

    def clear_hotkey(self) -> None:
        self._value = ""
        self._backup = ""
        self._recording = False
        self._show_value()

    def _show_value(self) -> None:
        super().setText(self._value)
        self.setPlaceholderText(i18n.t("Нажмите сочетание клавиш"))

    def _begin(self) -> None:
        self._recording = True
        self._backup = self._value
        super().setText("")
        self.setPlaceholderText(i18n.t("Нажмите сочетание клавиш…"))

    def _end(self) -> None:
        self._recording = False
        self._show_value()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if not self._recording:
            self._begin()

    def focusOutEvent(self, event):
        self._recording = False
        self._show_value()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if not self._recording:
            self._begin()

        key = event.key()
        mods = event.modifiers()

        # без модификаторов Esc и Backspace/Delete — управление, а не клавиша
        if key == Qt.Key_Escape and mods == Qt.NoModifier:
            self._value = self._backup
            self._end()
            self.clearFocus()
            event.accept()
            return
        if key in (Qt.Key_Backspace, Qt.Key_Delete) and mods == Qt.NoModifier:
            self._value = ""
            self._backup = ""
            self._end()
            self.clearFocus()
            event.accept()
            return

        if key in _MODIFIER_KEYS:
            names = hotkey_modifier_names(mods)
            super().setText("+".join(names) + ("+" if names else "") + "…")
            event.accept()
            return

        combo = hotkey_from_key(key, mods, event.text())
        if not combo:
            super().setText("")
            self.setPlaceholderText(i18n.t("Клавиша не распознана — попробуйте другую"))
            event.accept()
            return

        self._value = combo
        self._backup = combo
        self._end()
        self.clearFocus()
        event.accept()

    def keyReleaseEvent(self, event):
        # отпустил модификатор, ничего больше не нажав — возвращаем подсказку
        if self._recording and event.key() in _MODIFIER_KEYS and not event.modifiers():
            super().setText("")
            self.setPlaceholderText(i18n.t("Нажмите сочетание клавиш…"))
        super().keyReleaseEvent(event)


class HotkeyPromptDialog(QDialog):
    """Запись одной горячей клавиши (например, для пресета гаммы)."""

    def __init__(self, title: str, prompt: str, value: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(380)

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.DIALOG_MARGINS)
        root.setSpacing(th.DIALOG_SPACING)

        label = QLabel(prompt)
        label.setWordWrap(True)
        root.addWidget(label)

        self.edit = HotkeyEdit(value)
        root.addWidget(self.edit)

        hint = QLabel(i18n.t(
            "Нажмите сочетание — оно запишется само. Esc — отмена, Backspace — очистить."))
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

    def hotkey(self) -> str:
        return self.edit.text().strip()


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
            ("reset_everything", i18n.t("Вернуть всё как было (стоп-кран)")),
            ("cat_vision", i18n.t("Кошачье зрение")),
            ("perf_toggle", i18n.t("Переключить производительность")),
        ]

        for row, (key, label_text) in enumerate(items):
            label = QLabel(label_text)
            edit = HotkeyEdit(hotkeys.get(key, ""))
            self.inputs[key] = edit

            form.addWidget(label, row, 0)
            form.addWidget(edit, row, 1)

        hint = QLabel(
            i18n.t("Нажмите сочетание прямо в поле — оно запишется само. "
                   "Esc — отмена, Backspace — очистить.")
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

        # смена полноэкранного окна: игра выходит вперёд и сбрасывает рампу
        # своим профилем — ждать очередного тика таймера незачем
        self._gamma_event_pending = False
        self._fg_watcher = None
        self._fg_pid = 0
        # пресет, который был на экране до входа в игру: его вернём на выходе
        self._auto_prev_index = None
        # и частота, которая была до игры
        self._auto_prev_hz = None

        # подсветка матрицы: dxva2 на первом вызове молчит секунды, поэтому
        # поиск монитора — в фоне, а запись яркости — с задержкой
        self._ddc = None
        self._ddc_monitor = None
        self._ddc_loading = False
        self._ddc_pending = 0
        self._ddc_last_ok = 0
        self._ddc_timer = QTimer(self)
        self._ddc_timer.setSingleShot(True)
        self._ddc_timer.setInterval(300)
        self._ddc_timer.timeout.connect(self._ddc_apply)

        self.setWindowTitle(APP_NAME)
        self.resize(self.compact_size)
        self.setMinimumSize(self.min_window_size)
        self._setup_frameless()
        self._text_bindings = []
        self._tooltip_bindings = []

        icon_path = resource_path(ICON_FILE)

        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # стартовый экран — всегда «Главный» (просьба Юрия 27.10.2026): это пульт,
        # ради него приложение и открывают. Прежняя вкладка не запоминается —
        # ключ last_page убран из настроек, чтобы не копить мёртвое значение.
        self.build_ui()
        self.build_tray()
        # RegisterHotKey привязывает сочетания к HWND: без окна WM_HOTKEY не
        # придёт. winId() создаёт нативное окно, если его ещё нет.
        self.hotkey_manager.set_window(int(self.winId()))
        gd_boot.mark("init:tray")
        self.start_rules()
        self.start_quiet_hour()
        gd_boot.mark("init:rules")
        # тема уже применена до сборки окна (см. main()): страницы собраны со
        # своим QSS. Повторно применяем, только если настройка разошлась с
        # активной палитрой (правка файла между двумя чтениями — редкость).
        want_theme = self.config.get("settings", {}).get("theme", "dark")
        if th.active_name() != want_theme:
            try:
                self.apply_theme(want_theme)
            except Exception:  # noqa: BLE001
                pass
        try:
            import gd_onboarding

            if gd_onboarding.should_show(self.config):
                QTimer.singleShot(
                    500, lambda: gd_onboarding.run(self.config, save_config, self,
                                     startup_fn=self.set_start_with_windows))
        except Exception:  # noqa: BLE001
            pass
        self.bind_hotkeys()
        gd_boot.mark("init:hotkeys")
        self._fix_tab_order()
        gd_boot.mark("init:taborder")

        # стартовый экран — «Главный»: индекс 0 задан порядком _add_page, а
        # прежняя вкладка больше не восстанавливается (см. __init__)
        home_idx = self._key_to_index.get("tab.home")
        if home_idx is not None and home_idx != self.tabs.currentIndex():
            self.tabs.setCurrentIndex(home_idx)
        gd_boot.mark("init:sensors")
        self._start_sensor_helper()
        gd_boot.mark("init:sensor-helper")

        # автолимит GPU: после старта вернуть лимит по настройке (фон, один раз)
        QTimer.singleShot(1500, self._gpu_autostart_check)
        # локальный API: поднимаем только если пользователь включал его сам

        gd_boot.mark("init:setui-before")
        current = self.config["settings"]["current"]

        self.set_ui_values(
            current.get("brightness", 1.0),
            current.get("contrast", 1.0),
            current.get("gamma", 1.0),
            current.get("temperature", 6500),
            apply_now=True,
            save=False,
            shadow=current.get("shadow", 0),
        )

        gd_boot.mark("init:watch")
        self.gamma_watch_timer.start()
        gd_boot.mark("init:fgwatch")
        self._start_foreground_watch()
        gd_boot.mark("init:ddc")
        self._start_ddc_probe()
        gd_boot.mark("init:rollback")
        self._rollback_after_crash()

        # честный старт: если конфиг пришлось заменить дефолтом, об этом говорят
        self._config_recovery_note()
        self._write_rights_note()
        gd_boot.mark("init:done")

        # время старта сохраняем: «тормозит на старте» — это факт, а не ощущение
        try:
            self.config.setdefault("settings", {})["last_boot_ms"] = gd_boot.elapsed_ms()
            self._save_config(now=True)
        except Exception:  # noqa: BLE001 — замер не повод не запуститься
            pass

    def _sync_startup(self) -> None:
        """Привести файл автозапуска в соответствие с настройкой и текущим путём.

        Бывает два расхождения, и оба тихие: галочка выключена, а файл в
        «Автозагрузке» остался от прошлой версии; галочка включена, а файл
        ссылается на старую сборку (`C:/gd_build/dist/...`), потому что
        программу установили в другое место. `enable_startup` идемпотентен —
        переписывает файл под текущий exe.
        """
        settings = self.config.get("settings") or {}
        wanted = bool(settings.get("start_with_windows"))
        try:
            if wanted:
                enable_startup()
            else:
                disable_startup()
        except Exception:  # noqa: BLE001 — автозапуск не повод не запуститься
            pass

    def _write_rights_note(self) -> None:
        """Настройки, которые некуда сохранить, — тихая беда: сказать вслух.

        Бывает при установке в `Program Files` без прав администратора:
        слайдеры двигаются, пресеты «сохраняются», после перезапуска пусто.
        """
        if CONFIG_RECOVERY == "restored":
            return                      # уже сказали про восстановление
        self._sync_startup()
        ok, detail = gd_paths.can_write_data()
        if not ok:
            self.set_status(i18n.t(
                "Папка настроек недоступна для записи ({v0}). Включите портативный "
                "режим в «Утилитах» или запустите программу от администратора.",
                v0=detail))

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

    def nativeEvent(self, event_type, message):
        """WM_HOTKEY от RegisterHotKey: система присылает его окну, а не процессу."""
        if event_type != b"windows_generic_MSG":
            return super().nativeEvent(event_type, message)
        try:
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == gd_hotkeys.WM_HOTKEY:
                self.hotkey_manager.dispatch(int(msg.wParam))
                return True, 0
            taskbar = _taskbar_created_message()
            if taskbar and msg.message == taskbar:
                # Explorer перезапустился (обновление, сбой) — трей о нём
                # забыл, и значок исчез: показываем заново
                self._restore_tray()
                return True, 0
            if msg.message == WM_QUERYENDSESSION:
                # сеанс завершают (выключение/перезагрузка): временные изменения
                # лучше вернуть самим — ждать следующего старта некому
                self._restore_before_shutdown()
                return True, 1
        except Exception:  # noqa: BLE001 — не наше сообщение или окно уже закрыто
            pass
        return super().nativeEvent(event_type, message)

    def _restore_tray(self) -> None:
        """Вернуть значок в трей после перезапуска Explorer."""
        tray = getattr(self, "tray", None)
        if tray is None:
            return
        try:
            tray.hide()
            tray.show()
        except Exception:  # noqa: BLE001 — значок не повод падать
            return
        self.set_status(i18n.t("Explorer перезапустился — значок вернулся в трей"))

    def build_ui(self):
        self.setStyleSheet(APP_STYLESHEET)
        gd_boot.mark("ui:start")

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
        self._page_factories: dict = {}
        self._page_slots: dict = {}
        self._page_layouts: dict = {}

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

        # Shadow Boost: поднимает только тёмные уровни, света не трогает.
        # Шкала в процентах силы, в кривой это 0..0.25.
        self.shadow_row, self.shadow_slider, self.shadow_value = self.make_slider_row(
            "Тени",
            0,
            100,
            0,
            "%",
        )
        self.shadow_slider.setToolTip(i18n.t(
            "Вытягивает детали в тёмных сценах, не поднимая светлые участки."))

        self.brightness_slider.valueChanged.connect(self.on_slider_changed)
        self.contrast_slider.valueChanged.connect(self.on_slider_changed)
        self.shadow_slider.valueChanged.connect(self.on_slider_changed)

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
        advanced_layout.addWidget(self.shadow_row)
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

        # ---------- подсветка монитора (DDC/CI) ----------
        # это яркость самой матрицы, а не картинки: гамма может быть сколь угодно
        # тёмной, а подсветка всё равно светит в глаза
        ddc_card, ddc_layout = self.make_card()

        ddc_head = QHBoxLayout()
        ddc_head.setContentsMargins(0, 0, 0, 0)
        ddc_head.setSpacing(th.SPACE_S)

        ddc_title = QLabel()
        self.bind_text(ddc_title, "Подсветка монитора")
        ddc_title.setObjectName("SectionTitle")

        self.ddc_value = QLabel()
        self.ddc_value.setObjectName("RowValue")
        self.set_value_text(self.ddc_value, 0, "%")

        ddc_head.addWidget(ddc_title)
        ddc_head.addStretch()
        ddc_head.addWidget(self.ddc_value, 0, Qt.AlignBottom)

        ddc_layout.addLayout(ddc_head)

        self.ddc_hint = QLabel()
        self.bind_text(self.ddc_hint, "ищем монитор…")
        self.ddc_hint.setObjectName("CardHint")
        self.ddc_hint.setWordWrap(True)
        ddc_layout.addWidget(self.ddc_hint)

        # у кого два монитора — выбираем, какой регулировать: DDC/CI работает
        # с каждым отдельно, а первый по списку может быть не тем
        self.ddc_pick = QComboBox()
        self.ddc_pick.setObjectName("Segment")
        self.ddc_pick.setVisible(False)
        self.ddc_pick.setToolTip(i18n.t("Какой монитор регулировать — DDC/CI работает с каждым отдельно"))
        self.ddc_pick.currentIndexChanged.connect(self.on_ddc_monitor_picked)
        ddc_layout.addWidget(self.ddc_pick)

        self.ddc_slider = QSlider(Qt.Horizontal)
        self.ddc_slider.setMinimum(0)
        self.ddc_slider.setMaximum(100)
        self.ddc_slider.setValue(0)
        self.ddc_slider.setMinimumHeight(SLIDER_HANDLE_SIZE)
        self.ddc_slider.setCursor(Qt.PointingHandCursor)
        self.ddc_slider.setEnabled(False)
        self.ddc_slider.valueChanged.connect(self.on_ddc_changed)
        ddc_layout.addWidget(self.ddc_slider)

        left_layout.addWidget(ddc_card)

        # ---------- автопрофиль по игре ----------
        auto_card, auto_layout = self.make_card()

        auto_head = QHBoxLayout()
        auto_head.setContentsMargins(0, 0, 0, 0)
        auto_head.setSpacing(th.SPACE_S)

        auto_title = QLabel()
        self.bind_text(auto_title, "Автопрофиль по игре")
        auto_title.setObjectName("SectionTitle")

        self.auto_enabled = QCheckBox()
        self.bind_text(self.auto_enabled, "включено")
        self.auto_enabled.setChecked(bool(
            self.config.get("settings", {}).get("auto_profiles_on", True)))
        self.auto_enabled.toggled.connect(self.set_auto_profiles_on)

        auto_head.addWidget(auto_title)
        auto_head.addStretch()
        auto_head.addWidget(self.auto_enabled, 0, Qt.AlignBottom)

        auto_layout.addLayout(auto_head)

        self.auto_hint = QLabel()
        self.bind_text(self.auto_hint, "Профиль включается, когда окно игры выходит вперёд.")
        self.auto_hint.setObjectName("CardHint")
        self.auto_hint.setWordWrap(True)
        auto_layout.addWidget(self.auto_hint)

        self.auto_list = QListWidget()
        self.auto_list.setObjectName("AutoList")
        self.auto_list.setMaximumHeight(96)
        auto_layout.addWidget(self.auto_list)

        auto_buttons = QHBoxLayout()
        auto_buttons.setContentsMargins(0, 0, 0, 0)
        auto_buttons.setSpacing(th.SPACE_S)

        self.auto_add_button = QPushButton()
        self.bind_text(self.auto_add_button, "Добавить…")
        self.tune_button(self.auto_add_button)
        self.auto_add_button.clicked.connect(self.add_auto_profile)

        self.auto_remove_button = QPushButton()
        self.bind_text(self.auto_remove_button, "Удалить")
        self.tune_button(self.auto_remove_button)
        self.auto_remove_button.clicked.connect(self.remove_auto_profile)

        auto_buttons.addWidget(self.auto_add_button)
        auto_buttons.addWidget(self.auto_remove_button)
        auto_buttons.addStretch()

        auto_layout.addLayout(auto_buttons)

        left_layout.addWidget(auto_card)
        self.refresh_auto_list()

        # ---------- частота обновления ----------
        hz_card, hz_layout = self.make_card()

        hz_head = QHBoxLayout()
        hz_head.setContentsMargins(0, 0, 0, 0)
        hz_head.setSpacing(th.SPACE_S)

        hz_title = QLabel()
        self.bind_text(hz_title, "Частота обновления")
        hz_title.setObjectName("SectionTitle")

        self.hz_hint = QLabel()
        self.hz_hint.setObjectName("RowValue")
        self.hz_hint.setText("…")

        hz_head.addWidget(hz_title)
        hz_head.addStretch()
        hz_head.addWidget(self.hz_hint, 0, Qt.AlignBottom)

        hz_layout.addLayout(hz_head)

        hz_row = QHBoxLayout()
        hz_row.setContentsMargins(0, 0, 0, 0)
        hz_row.setSpacing(th.SPACE_S)

        self.hz_combo = QComboBox()
        self.hz_combo.setAccessibleName(i18n.t("Частота обновления"))
        self.hz_combo.setMinimumWidth(120)

        self.hz_apply_button = QPushButton()
        self.bind_text(self.hz_apply_button, "Применить")
        self.tune_button(self.hz_apply_button)
        self.hz_apply_button.clicked.connect(self.apply_refresh_rate)

        hz_row.addWidget(self.hz_combo, 1)
        hz_row.addWidget(self.hz_apply_button)

        hz_layout.addLayout(hz_row)

        self.hz_note = QLabel()
        self.bind_text(self.hz_note, "Меняется только частота: разрешение и цвет остаются прежними.")
        self.hz_note.setObjectName("CardHint")
        self.hz_note.setWordWrap(True)
        hz_layout.addWidget(self.hz_note)

        left_layout.addWidget(hz_card)
        self._start_hz_probe()

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
        # главный экран — первым: он стартовый, а порядок вызовов _add_page
        # задаёт индекс в стеке
        self._add_page(None, "tab.home", factory=self._make_home_page)
        gd_boot.mark("page:home")

        self._add_page(gamma_page, "tab.gamma")
        gd_boot.mark("page:gamma")

        if str(app_dir()) not in sys.path:
            sys.path.insert(0, str(app_dir()))
        from gd_power_ui import PowerWindow

        self._power_page = PowerWindow(standalone=False)
        self._add_page(self._power_page, "tab.cpu")
        gd_boot.mark("page:cpu")

        from gd_gpu_ui import GpuWidget

        self._gpu_page = GpuWidget(standalone=False, config=self.config,
                                   save_fn=save_config)
        self._add_page(self._gpu_page, "tab.gpu")
        gd_boot.mark("page:gpu")

        self._add_page(None, "tab.cs2", factory=self._make_cs2_page)

        # Остальные страницы ленивые: модуль вкладки импортируется и виджеты
        # строятся при первом открытии вкладки (или первом действии, которому
        # нужен её объект), а не на старте окна — см. `_ensure_page`.
        self._add_page(None, "tab.rules", factory=self._make_rules_page)
        self._add_page(None, "tab.utils", factory=self._make_utils_page)
        self._add_page(None, "tab.dns", factory=self._make_dns_page)
        self._add_page(None, "tab.zapret", factory=self._make_zapret_page)
        self._add_page(None, "tab.diagnose", factory=self._make_diagnose_page)

        self._retranslate_tabs()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(self.tabs.currentIndex())
        gd_boot.mark("ui:pages-done")

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
        gd_boot.mark("ui:done")

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
            ("page", "tab.home"),
            ("grp", "side.power"), ("page", "tab.cpu"), ("page", "tab.gpu"),
            ("page", "tab.cs2"),
            ("grp", "side.system"), ("page", "tab.gamma"), ("page", "tab.rules"),
            ("page", "tab.utils"), ("page", "tab.dns"), ("page", "tab.zapret"),
            ("page", "tab.diagnose"),
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
            # пункты меню пересозданы — цепочку Tab надо собрать заново
            self._fix_tab_order()
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
            orphan = bool(page is not None and page.active_is_clone())
        except Exception:  # noqa: BLE001
            return
        if (alive, profile, orphan) == self._foot_state:
            return
        self._foot_state = (alive, profile, orphan)

        if alive:
            names = {"eco": i18n.t("ЭКО"), "normal": i18n.t("НОРМАЛ"), "boost": i18n.t("БУСТ")}
            text = i18n.t("Служба питания активна")
            tip = i18n.t("Компаньон питания работает: профили применяются через него.")
            if profile:
                tip += " " + i18n.t("Профиль: {v0}.", v0=names.get(profile, str(profile).upper()))
            elif orphan:
                tip += " " + i18n.t("Активна наша копия схемы без профиля.")
            else:
                tip += " " + i18n.t("Активна исходная схема.")
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

    def _add_page(self, widget, key: str, factory=None):
        """Страница в стек с шапкой (заголовок + подпись) — как в макете A.

        `widget=None, factory=...` — ленивая страница: шапка и место в стеке
        создаются сразу (порядок вкладок и индексы стабильны), а сам экран
        собирает `_ensure_page` при первом открытии вкладки.
        """
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
        sub.setWordWrap(True)       # длинное имя схемы не обрезается о край окна
        head_lay.addWidget(title)
        head_lay.addWidget(sub)
        outer.addWidget(head)
        slot = None             # место экрана: у ленивых — пустая прокрутка
        if widget is not None and isinstance(widget, QScrollArea):
            outer.addWidget(widget, 1)
        else:
            scroll = QScrollArea()
            scroll.setObjectName("PageScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            if widget is not None:
                scroll.setWidget(widget)
            outer.addWidget(scroll, 1)
            slot = scroll

        self._page_layouts[key] = outer
        self._page_slots[key] = slot
        self._page_factories[key] = factory
        self._page_heads[key] = (title, sub)
        self._page_widgets[key] = widget
        self._key_to_index[key] = self.tabs.count()
        self.tabs.addWidget(shell)

    def _ensure_page(self, key: str):
        """Собрать ленивую страницу (один раз) и вернуть её виджет.

        Вызывается при первом открытии вкладки и во всех действиях, которым
        нужен объект экрана (сброс всех изменений, локальный API, палитра).
        Страницы, которых действие не касается, не строятся.
        """
        widget = self._page_widgets.get(key)
        if widget is not None:
            return widget
        factory = self._page_factories.get(key)
        if factory is None:
            return None
        gd_boot.mark(f"lazy:{key}")
        widget = factory()
        if widget is None:
            return None
        outer = self._page_layouts.get(key)
        slot = self._page_slots.get(key)
        if slot is not None and outer is not None and isinstance(widget, QScrollArea):
            # у «Утилит» своя обёртка прокрутки: она встаёт на место заготовки,
            # а не второй прокруткой внутри неё
            outer.replaceWidget(slot, widget)
            slot.deleteLater()
        elif slot is not None:
            slot.setWidget(widget)
        self._page_widgets[key] = widget
        gd_boot.mark(f"lazy:{key}:done")
        return widget

    # -- фабрики ленивых страниц: импорт модуля вкладки и сборка экрана ----
    def _make_home_page(self):
        """Главный экран: сетка виджетов поверх подсистем (gd_dashboard_ui)."""
        from gd_dashboard_ui import DashboardWidget

        self._home_page = DashboardWidget(host=self)
        return self._home_page

    def _make_rules_page(self):
        from gd_rules_ui import RulesWidget

        self._rules_page = RulesWidget(standalone=False)
        return self._rules_page

    def _make_utils_page(self):
        from gd_utils_ui import UtilsWidget

        self._utils_page = UtilsWidget(standalone=False, host=self)
        pending = getattr(self, "_updates_pending_status", None)
        if pending:
            # проверка обновлений стартовала до сборки ленивой вкладки:
            # не теряем её статус, когда страница наконец открылась
            self._utils_page.set_update_status(*pending)
        utils_scroll = QScrollArea()
        utils_scroll.setWidgetResizable(True)
        utils_scroll.setFrameShape(QScrollArea.NoFrame)
        utils_scroll.setWidget(self._utils_page)
        utils_scroll.setObjectName("UtilitiesScrollArea")
        self._utils_scroll = utils_scroll
        return utils_scroll

    def _make_dns_page(self):
        from gd_dns_ui import DnsWidget

        self._dns_page = DnsWidget(standalone=False)
        return self._dns_page

    def _make_zapret_page(self):
        from gd_zapret_ui import ZapretWidget

        self._zapret_page = ZapretWidget(standalone=False, host=self)
        return self._zapret_page

    def _make_diagnose_page(self):
        from gd_diagnose_ui import DiagnoseWidget

        self._diagnose_page = DiagnoseWidget(standalone=False, host=self)
        return self._diagnose_page

    def _make_cs2_page(self):
        from gd_cs2_ui import Cs2Widget

        self._cs2_page = Cs2Widget(standalone=False, host=self, config=self.config,
                                   save_fn=save_config)
        return self._cs2_page

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
        # дети «невидимы». isVisibleTo учитывает только явное скрытие.
        # виджеты невидимых страниц стека тоже входят в кольцо: иначе порядок
        # чинится только у стартовой вкладки, а у остальных Tab уходит в кнопки окна
        stops = [x for x in ring
                 if x.focusPolicy() != Qt.NoFocus
                 and (self.tabs.isAncestorOf(x) or x.isVisibleTo(self))]
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
                # наша копия могла накопить метку от старых версий: в шапке
                # показываем имя схемы без повторяющегося «Gammadrilon — …»
                scheme = gd_power.display_scheme_name(cache[0])
                scheme = scheme or i18n.t("наша копия")
                if len(scheme) > 60:
                    scheme = scheme[:59].rstrip() + "…"
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
        if s.get("temp_c"):
            cpu += f" · {s['temp_c']:.0f} °C"
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
        self.tray.diagnoseRequested.connect(self.open_diagnose_tab)
        self.tray.rulesRequested.connect(self.open_rules_tab)
        self.tray.utilsRequested.connect(self.open_utils_tab)
        self.tray.themeRequested.connect(self._tray_theme)
        self.tray.resetEverythingRequested.connect(self.stop_crane)
        self.tray.quitRequested.connect(self.quit_app)
        # галка переключателя производительности — из конфига (переживает запуск)
        try:
            limited = bool((self.config.get("settings") or {}).get("perf_limited", False))
        except Exception:  # noqa: BLE001
            limited = False
        self.tray.set_perf_limited(limited)
        self.tray.show()
        gd_boot.mark("tray-shown")

        # живой тултип (ТЗ 5.1): раз в секунду, без всплывающих окон
        self._gpu_cache = None
        self._tray_timer = QTimer(self)
        self._tray_timer.setInterval(1000)
        self._tray_timer.timeout.connect(self._tray_tick)
        self._tray_timer.start()
        self._tray_tick()
        gd_boot.mark("tray-ready")

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
            if kind == "gamma_preset":
                index = safe_int(action.get("preset"), -1)
                presets = self.config.get("presets") or []
                if not 0 <= index < len(presets):
                    raise ValueError(i18n.t("пресет №{v0} не найден", v0=index + 1))
                self.apply_preset_by_index(index, auto=True)
                return i18n.t("«{v0}»: пресет гаммы «{v1}» включён", v0=name,
                              v1=presets[index].get("name", f"#{index + 1}"))
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

    def open_palette(self):
        from gd_palette import CommandPalette

        self._palette = CommandPalette(self)
        self._palette.exec_()

    def open_journal(self):
        from gd_journal_ui import JournalDialog

        dlg = JournalDialog(self)
        dlg.exec_()

    def open_calibration(self):
        """Калибровка теней по шагам: итог сразу применяется и сохраняется."""
        import gd_calibrate

        def apply(gamma: float, shadow: float) -> None:
            strength = int(round(shadow / 0.25 * 100))
            self.shadow_slider.blockSignals(True)
            self.shadow_slider.setValue(strength)
            self.shadow_slider.blockSignals(False)
            self.gamma_slider.blockSignals(True)
            self.gamma_slider.setValue(int(round(gamma * 100)))
            self.gamma_slider.blockSignals(False)
            self.update_value_labels()
            brightness, contrast, _gamma, temperature = self.values_from_ui()
            self.apply_values(brightness, contrast, gamma, temperature,
                              save=True, shadow=strength)

        gd_calibrate.run(apply, parent=self,
                         gamma=self.gamma_slider.value() / 100.0,
                         shadow=self.shadow_strength() / 100.0 * 0.25)

    def open_white_point(self):
        """Подбор температуры по серой плитке: нейтраль вместо «на глаз»."""
        import gd_calibrate

        def apply(temperature: int) -> None:
            self.temperature_slider.blockSignals(True)
            self.temperature_slider.setValue(int(temperature))
            self.temperature_slider.blockSignals(False)
            self.update_value_labels()
            brightness, contrast, gamma, _temperature = self.values_from_ui()
            self.apply_values(brightness, contrast, gamma, int(temperature), save=True)

        gd_calibrate.run_white_point(apply, parent=self,
                                     temperature=self.temperature_slider.value())

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
        if key:
            # ленивая вкладка собирается ровно в момент первого открытия
            self._ensure_page(key)
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
        cs2 = getattr(self, "_cs2_page", None)
        if cs2 is not None:
            (cs2.activate() if page is cs2 else cs2.deactivate())
        self._sync_metrics_activity()

    def open_dns_tab(self):
        """Открыть вкладку «DNS» в главном окне."""
        idx = self._key_to_index.get("tab.dns")
        if idx is not None:
            self.tabs.setCurrentIndex(idx)      # открытие соберёт ленивую вкладку
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

        gd_onboarding.run(self.config, save_config, self,
                          startup_fn=self.set_start_with_windows)

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
        idx = self._key_to_index.get("tab.utils")
        if idx is not None:
            self.tabs.setCurrentIndex(idx)      # открытие соберёт ленивую вкладку
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

        dns = self._ensure_page("tab.dns")     # ленивый экран собирается под действие
        if dns is not None:
            try:
                dns._restore()
                done.append("DNS")
            except Exception:  # noqa: BLE001
                pass

        # обход DPI: остановить winws.exe, запущенный Gammadrilon (ТЗ 8, DoD)
        try:
            import gd_zapret

            state = gd_zapret.our_state()
            pid = (state or {}).get("pid")
            if pid and gd_zapret.pid_alive(pid):
                zapret = self._ensure_page("tab.zapret")
                if zapret is not None:
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

        # автоочистка: состояние живёт в autoclean.json (пишет вкладка «Утилиты»),
        # читаем его напрямую — ленивый экран ради галочки не собираем
        try:
            import gd_profiles

            auto = gd_profiles._read_autoclean()
            if auto.get("enabled") and gd_profiles._write_autoclean(
                    {**auto, "enabled": False}):
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
        idx = self._key_to_index.get("tab.rules")
        if idx is not None:
            self.tabs.setCurrentIndex(idx)      # открытие соберёт ленивую вкладку
        self.show_window()

    def open_diagnose_tab(self):
        """Открыть вкладку «Диагностика» — из трея, когда «что-то не работает»."""
        idx = self._key_to_index.get("tab.diagnose")
        if idx is not None:
            self.tabs.setCurrentIndex(idx)
        self.show_window()

    def open_gpu_tab(self):
        """Открыть вкладку «Питание GPU» в главном окне."""
        idx = self._key_to_index.get("tab.gpu")
        if idx is not None:
            self.tabs.setCurrentIndex(idx)
        self.show_window()

    def open_power_window(self):
        """Открыть вкладку «Питание CPU» в главном окне."""
        idx = self._key_to_index.get("tab.cpu")
        if idx is not None:
            self.tabs.setCurrentIndex(idx)
        self.show_window()

    def _start_sensor_helper(self):
        """Поднять хелпер температур (LibreHardwareMonitor + драйвер PawnIO).

        Отдельный процесс, потому что драйвер требует прав администратора, а окно
        работает обычным пользователем: хелпер сам уходит, когда процесс окна
        завершится (получает наш PID через `--parent`). Если драйвера нет или
        настройка выключена — молча живём без температур, CPU-плитка покажет «—».
        """
        settings = self.config.get("settings", {}) or {}
        try:
            import gd_sensors

            gd_sensors.set_vrm_channel(int(settings.get("sensors_vrm_channel") or 0))
            if not bool(settings.get("sensors", True)):
                return
            if not gd_sensors.driver_present():
                gd_boot.mark("sensors:no-driver")
                return
            # запуск хелпера — до трёх секунд ожидания драйвера: в фоне, иначе
            # окно уже нарисовано, но не отвечает, и старт «долгий» на пустом месте
            gd_boot.mark("sensors:submit")
            gd_bg.submit(gd_sensors.start, self._sensor_helper_done)
        except Exception:  # noqa: BLE001 — без температур приложение обязано работать
            gd_boot.mark("sensors:error")

    def _sensor_helper_done(self, started) -> None:
        """Хелпер температур поднялся (или нет) — только отметка, окно не трогаем."""
        gd_boot.mark("sensors:started" if started else "sensors:failed")

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
        self.hotkey_manager.resetEverythingRequested.connect(self.stop_crane)
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

    def shadow_strength(self) -> int:
        """Сила Shadow Boost в процентах шкалы (0..100), не в единицах кривой."""
        slider = getattr(self, "shadow_slider", None)
        return int(slider.value()) if slider is not None else 0

    def shadow_from_ui(self) -> float:
        """Значение для кривой: 0..0.25."""
        return self.shadow_strength() / 100.0 * 0.25

    def set_ui_values(
        self,
        brightness: float,
        contrast: float,
        gamma: float,
        temperature: int,
        apply_now: bool = True,
        save: bool = True,
        shadow: int | None = None,
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
            self.shadow_slider,
        )

        for slider in sliders:
            slider.blockSignals(True)

        self.brightness_slider.setValue(int(round(brightness * 100)))
        self.contrast_slider.setValue(int(round(contrast * 100)))
        self.gamma_slider.setValue(int(round(gamma * 100)))
        self.temperature_slider.setValue(int(temperature))
        if shadow is not None:
            self.shadow_slider.setValue(int(clamp(int(shadow), 0, 100)))

        for slider in sliders:
            slider.blockSignals(False)

        self.update_value_labels()

        if apply_now:
            self.apply_values(brightness, contrast, gamma, temperature, save=save)

    def update_value_labels(self):
        brightness, contrast, gamma, temperature = self.values_from_ui()

        self.set_value_text(self.brightness_value, int(round(brightness * 100)), "%")
        self.set_value_text(self.contrast_value, int(round(contrast * 100)), "%")
        self.set_value_text(self.shadow_value, self.shadow_strength(), "%")
        self.set_value_text(self.gamma_value, int(round(gamma * 100)), "%", big=True)
        self.set_value_text(self.temperature_value, int(temperature), "K")

        self.gamma_scale.set_current(int(round(gamma * 100)))
        self.curve.set_values(brightness, contrast, gamma, temperature, self.shadow_from_ui())

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
        shadow: int | None = None,
    ):
        strength = self.shadow_strength() if shadow is None else int(clamp(shadow, 0, 100))
        ok = self.gamma_controller.apply(brightness, contrast, gamma, temperature,
                                         strength / 100.0 * 0.25)
        self._gamma_applied_ok = bool(ok)
        # цель сторожа — что поставлено на экран, а не что в конфиге: «кошачье
        # зрение» меняет гамму без записи в конфиг (save=False), и сторож должен
        # защищать именно его, а не откатывать к сохранённому значению
        self._gamma_applied_values = (brightness, contrast, gamma, temperature, strength)

        if save:
            self.config["settings"]["current"] = {
                "brightness": round(brightness, 4),
                "contrast": round(contrast, 4),
                "gamma": round(gamma, 4),
                "temperature": int(temperature),
                "shadow": int(strength),
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

    def apply_preset_by_index(self, index: int, auto: bool = False):
        presets = self.config["presets"]

        if not presets:
            return

        if index < 0 or index >= len(presets):
            return

        # ручной выбор пресета отменяет авто-возврат: следующая смена окна
        # не должна «откатывать» то, что человек выбрал сам
        if not auto:
            self._auto_prev_index = None

        self.current_preset_index = index

        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)

        # пресет — осознанный выбор значений: режим «кошачье зрение» снимаем
        # без отката, иначе он вернёт прежние значения и затрёт пресет
        self._cancel_cat_vision()

        preset = presets[index]

        # тень — часть пресета: ставим до анимации, чтобы кривая сразу шла
        # к нужной форме, а не «догоняла» слайдер в конце
        self.shadow_slider.blockSignals(True)
        self.shadow_slider.setValue(int(clamp(safe_int(preset.get("shadow", 0), 0), 0, 100)))
        self.shadow_slider.blockSignals(False)

        # подсветка матрицы из пресета: -1 значит «не трогать» (монитор может
        # не уметь DDC/CI, а число в пресете всё равно осталось бы)
        ddc_level = safe_int(preset.get("ddc"), -1)
        if ddc_level >= 0 and self._ddc_monitor is not None:
            self.ddc_slider.blockSignals(True)
            self.ddc_slider.setValue(int(clamp(ddc_level, 0, 100)))
            self.ddc_slider.blockSignals(False)
            self._ddc_pending = int(clamp(ddc_level, 0, 100))
            self._ddc_timer.start()

        self.animate_to(
            preset.get("brightness", 1.0),
            preset.get("contrast", 1.0),
            preset.get("gamma", 1.0),
            preset.get("temperature", 6500),
            duration_ms=700,
            save_at_end=not auto,
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
            "shadow": self.shadow_strength(),
            # -1 — «подсветку не трогать»: на мониторе без DDC/CI
            # уровень остался бы выдуманным числом
            "ddc": (self.ddc_slider.value()
                    if self._ddc_monitor is not None else -1),
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

        dialog = HotkeyPromptDialog(
            i18n.t("Клавиша пресета"),
            i18n.t("Нажмите сочетание для «{v0}».", v0=preset.get("name", "Unnamed")),
            preset.get("hotkey", ""),
            self,
        )

        if dialog.exec_() != QDialog.Accepted:
            return

        preset["hotkey"] = dialog.hotkey()

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

        # тени тоже возвращаем: «сброс» обязан означать ровную кривую
        self.shadow_slider.blockSignals(True)
        self.shadow_slider.setValue(0)
        self.shadow_slider.blockSignals(False)

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

        brightness, contrast, gamma, temperature, shadow = applied
        temperature = int(temperature)
        shadow = float(shadow)

        if (abs(brightness - 1.0) < 0.001 and abs(contrast - 1.0) < 0.001
                and abs(gamma - 1.0) < 0.001 and temperature == 6500 and shadow <= 0.0):
            return

        actual = self.gamma_controller.read_current()

        if actual is None:
            return

        target = self.gamma_controller.build_ramp(brightness, contrast, gamma, temperature,
                                                  shadow / 100.0 * 0.25)

        if self.gamma_controller.ramp_max_diff(actual, target) <= GAMMA_WATCH_TOLERANCE:
            return

        if self.gamma_controller.apply(brightness, contrast, gamma, temperature,
                                       shadow / 100.0 * 0.25):
            self.set_status(i18n.t("Гамма восстановлена после сброса системой."))

    # -- смена активного окна ------------------------------------------------
    def _start_foreground_watch(self):
        """Подписка на смену активного окна: гамму возвращаем сразу, без тика.

        Хук ставится из GUI-потока — WinAPI доставляет события в его очередь.
        Ошибка подписки не повод падать: таймер-сторож продолжает работать.
        """
        if self._fg_watcher is not None:
            return
        watcher = gd_winwatch.ForegroundWatcher(self._on_foreground_changed)
        self._fg_watcher = watcher if watcher.start() else None

    # -- автопрофили: список на экране ---------------------------------------
    def refresh_auto_list(self):
        if not hasattr(self, "auto_list"):
            return
        self.auto_list.clear()
        presets = self.config.get("presets") or []
        for rule in self._auto_profiles():
            index = safe_int(rule.get("preset"), -1)
            name = presets[index].get("name", "Unnamed") if 0 <= index < len(presets) else "—"
            hertz = safe_int(rule.get("hz"), 0)
            suffix = f", {hertz} Гц" if hertz else ""
            self.auto_list.addItem(f"{rule.get('exe', '?')} → {name}{suffix}")

    def _running_exe_choices(self) -> list[str]:
        """Запущенные процессы для подсказки: имя игры проще выбрать, чем вспомнить."""
        names: set[str] = set()
        try:
            import psutil

            for proc in psutil.process_iter(["name"]):
                name = (proc.info.get("name") or "").strip()
                if not name or name.lower() in {"system", "registry", "memcompression",
                                                "system idle process"}:
                    continue
                if name.lower().startswith("gammadrilon"):
                    continue
                names.add(name)
        except Exception:  # noqa: BLE001 — подсказка не обязана работать
            pass
        return sorted(names, key=str.lower)

    def add_auto_profile(self):
        presets = self.config.get("presets") or []
        if not presets:
            self.set_status(i18n.t("сначала сохраните пресет"), ok=False)
            return

        exe, ok = QInputDialog.getItem(
            self, i18n.t("Автопрофиль по игре"), i18n.t("Процесс игры:"),
            self._running_exe_choices(), 0, True)

        if not ok:
            return

        exe = (exe or "").strip().rsplit("\\", 1)[-1]
        if not exe:
            return
        if "." not in exe:
            exe += ".exe"

        labels = [preset.get("name", f"#{i + 1}") for i, preset in enumerate(presets)]
        label, ok = QInputDialog.getItem(
            self, i18n.t("Автопрофиль по игре"), i18n.t("Пресет:"), labels, 0, False)

        if not ok:
            return

        index = labels.index(label)

        rates = ["не менять"] + [f"{value} Гц" for value in gd_hz.supported_rates()]
        hz_label, ok = QInputDialog.getItem(
            self, i18n.t("Автопрофиль по игре"), i18n.t("Частота в игре:"),
            rates, 0, False)

        if not ok:
            return

        hertz = 0 if hz_label == "не менять" else int(hz_label.split()[0])

        rules = self._auto_profiles()
        for rule in rules:
            if str(rule.get("exe", "")).lower() == exe.lower():
                rule["preset"] = index
                rule["hz"] = hertz
                break
        else:
            rules.append({"exe": exe.lower(), "preset": index, "hz": hertz})

        self.config.setdefault("settings", {})["auto_profiles"] = rules
        self._save_config()
        self.refresh_auto_list()
        self.set_status(i18n.t("автопрофиль: {v0} → {v1}", v0=exe, v1=label))

    def remove_auto_profile(self):
        row = self.auto_list.currentRow()
        rules = self._auto_profiles()
        if row < 0 or row >= len(rules):
            return
        removed = rules.pop(row)
        self.config.setdefault("settings", {})["auto_profiles"] = rules
        self._save_config()
        self.refresh_auto_list()
        self.set_status(i18n.t("автопрофиль убран: {v0}", v0=removed.get("exe", "")))

    def set_auto_profiles_on(self, on: bool):
        self.config.setdefault("settings", {})["auto_profiles_on"] = bool(on)
        self._save_config()

    # -- частота обновления ---------------------------------------------------
    def _start_hz_probe(self):
        if getattr(self, "_hz_loading", False):
            return
        self._hz_loading = True
        gd_bg.submit(self._hz_probe, self._hz_probe_done, parent=self)

    def _hz_probe(self):
        return (gd_hz.current_mode_summary(), gd_hz.refresh_rate(),
                gd_hz.supported_rates())

    def _hz_probe_done(self, result):
        self._hz_loading = False
        if isinstance(result, Exception) or not result:
            self.bind_text(self.hz_note, "частоту прочитать не удалось")
            return
        summary, rate, rates = result
        self.hz_hint.setText(summary)
        self.hz_combo.clear()
        for value in rates:
            self.hz_combo.addItem(f"{value} Гц", value)
        if rate in rates:
            self.hz_combo.setCurrentIndex(rates.index(rate))
        self._hz_current = rate

    def apply_refresh_rate(self):
        value = self.hz_combo.currentData()
        if value is None:
            return
        self.hz_apply_button.setEnabled(False)
        gd_bg.submit(lambda: gd_hz.set_refresh(None, int(value)), self._hz_applied,
                     parent=self)

    def _hz_applied(self, result):
        self.hz_apply_button.setEnabled(True)
        if isinstance(result, Exception):
            self.set_status(i18n.t("частота: {v0}", v0=result), ok=False)
            return
        ok, message = result
        if not ok:
            self.set_status(i18n.t("частота: {v0}", v0=message), ok=False)
            return
        self._hz_current = self.hz_combo.currentData()
        self.hz_hint.setText(gd_hz.current_mode_summary())
        self.set_status(i18n.t("частота: {v0}", v0=message))

    # -- откат временных изменений -------------------------------------------
    def _restore_before_shutdown(self):
        """Windows завершает сеанс: вернуть частоту, которую поставил автопрофиль."""
        data = gd_rollback.load()
        if not data:
            return
        hertz = safe_int(data.get("hz"), 0)
        gd_rollback.clear()
        if hertz > 0:
            try:
                gd_hz.set_refresh(None, hertz)
            except Exception:  # noqa: BLE001 — на выключении уже всё равно
                pass

    def _rollback_after_crash(self):
        """Прошлый запуск не успел вернуть изменения — доделываем сейчас."""
        data = gd_rollback.orphaned()
        if not data:
            return
        hertz = safe_int(data.get("hz"), 0)
        gd_rollback.clear()
        if hertz <= 0:
            return
        gd_bg.submit(lambda: gd_hz.set_refresh(None, hertz), self._rollback_done,
                     parent=self)

    def _rollback_done(self, result):
        if isinstance(result, Exception):
            return
        ok, message = result
        if ok:
            self.set_status(i18n.t("после сбоя возвращена частота: {v0}", v0=message))

    def _stop_foreground_watch(self):
        if self._fg_watcher is not None:
            self._fg_watcher.stop()
            self._fg_watcher = None

    def _on_foreground_changed(self, _hwnd: int, pid: int):
        """Пришла смена окна: одна проверка на пачку событий Alt+Tab."""
        self._fg_pid = int(pid or 0)
        if self._gamma_event_pending:
            return
        self._gamma_event_pending = True
        QTimer.singleShot(GAMMA_EVENT_DELAY_MS, self._gamma_event_check)

    def _gamma_event_check(self):
        self._gamma_event_pending = False
        self.gamma_watch_tick()
        self._auto_profile_tick()

    # -- автопрофили по игре -------------------------------------------------
    def _auto_profiles(self) -> list:
        value = self.config.get("settings", {}).get("auto_profiles")
        return value if isinstance(value, list) else []

    def _auto_profile_tick(self):
        """Пресет по имени процесса в фокусе; на выходе — прежний.

        Работает только когда правило реально есть: иначе хук фокуса ничего
        не делает и не мешает сторожу гаммы.
        """
        if not bool(self.config.get("settings", {}).get("auto_profiles_on", True)):
            return
        rules = self._auto_profiles()
        if not rules:
            return

        pid = getattr(self, "_fg_pid", 0)
        exe = gd_winwatch.process_path(pid).rsplit("\\", 1)[-1].lower() if pid else ""
        presets = self.config.get("presets") or []
        match = next((rule for rule in rules
                      if exe and str(rule.get("exe", "")).lower() == exe), None)

        if match is not None:
            index = safe_int(match.get("preset"), -1)
            if 0 <= index < len(presets) and index != self.current_preset_index:
                if self._auto_prev_index is None:
                    self._auto_prev_index = self.current_preset_index
                self.apply_preset_by_index(index, auto=True)

            hertz = safe_int(match.get("hz"), 0)
            now_hz = gd_hz.refresh_rate()
            if hertz > 0 and now_hz is not None and hertz != now_hz:
                if self._auto_prev_hz is None:
                    self._auto_prev_hz = now_hz
                gd_bg.submit(lambda value=hertz: gd_hz.set_refresh(None, value),
                             lambda _result: None, parent=self)

            # снимок «как было»: если окно упадёт или выключат ПК, следующий
            # старт вернёт частоту, а не оставит игровую
            if self._auto_prev_hz:
                gd_rollback.merge("hz", {"hz": self._auto_prev_hz})
            return

        # игра больше не в фокусе — возвращаем пресет и частоту, что были до неё
        if self._auto_prev_hz is not None:
            previous_hz = self._auto_prev_hz
            self._auto_prev_hz = None
            gd_bg.submit(lambda value=previous_hz: gd_hz.set_refresh(None, value),
                         lambda _result: None, parent=self)
            gd_rollback.clear()
        if self._auto_prev_index is not None:
            previous = self._auto_prev_index
            self._auto_prev_index = None
            if 0 <= previous < len(presets):
                self.apply_preset_by_index(previous, auto=True)

    # -- подсветка монитора (DDC/CI) -----------------------------------------
    def _start_ddc_probe(self):
        """Поиск монитора в фоне: первый вызов dxva2 занимает секунды."""
        if self._ddc_loading or self._ddc_monitor is not None:
            return
        self._ddc_loading = True
        gd_bg.submit(self._ddc_probe, self._ddc_probe_done, parent=self)

    def _ddc_probe(self):
        ddc = gd_ddc.Ddc()
        if not ddc.load():
            return ddc, None, None
        monitor = ddc.main()
        level = monitor.brightness() if monitor is not None else None
        return ddc, monitor, level

    def _ddc_probe_done(self, result):
        self._ddc_loading = False
        if isinstance(result, Exception) or result is None:
            self._ddc_failed(str(result or ""))
            return
        ddc, monitor, level = result
        self._ddc = ddc
        if monitor is None or level is None:
            self._ddc_failed(ddc.problem)
            return
        self._fill_ddc_pick(ddc)
        self._ddc_monitor = monitor
        self._ddc_last_ok = int(level)
        self.ddc_slider.blockSignals(True)
        self.ddc_slider.setValue(int(level))
        self.ddc_slider.blockSignals(False)
        self.set_value_text(self.ddc_value, int(level), "%")
        self.ddc_slider.setEnabled(True)
        if monitor.description:
            self.bind_text(self.ddc_hint, monitor.description)
        else:
            self.bind_text(self.ddc_hint, "яркость самой матрицы по DDC/CI")

    def _fill_ddc_pick(self, ddc) -> None:
        """Заполнить выбор монитора: только те, что реально умеют яркость.

        Один монитор — список прячем: выбирать нечего.
        """
        usable = usable_monitors(ddc)
        self.ddc_pick.blockSignals(True)
        self.ddc_pick.clear()
        for monitor in usable:
            self.ddc_pick.addItem(monitor.description or i18n.t("Монитор {v0}", v0=len(usable)))
        self.ddc_pick.blockSignals(False)
        if len(usable) > 1:
            saved = int((self.config.get("settings") or {}).get("ddc_monitor") or 0)
            index = saved if 0 <= saved < len(usable) else 0
            self.ddc_pick.setCurrentIndex(index)
            self.ddc_pick.setVisible(True)
        else:
            self.ddc_pick.setVisible(False)

    def on_ddc_monitor_picked(self, index: int) -> None:
        """Переключились на другой монитор: читаем его яркость, не гадаем."""
        monitors = usable_monitors(self._ddc)
        if not (0 <= int(index) < len(monitors)):
            return
        self.config.setdefault("settings", {})["ddc_monitor"] = int(index)
        self._save_config()
        monitor = monitors[int(index)]
        self._ddc_monitor = monitor
        self.ddc_slider.setEnabled(False)
        self.bind_text(self.ddc_hint, "читаем яркость…")
        gd_bg.submit(monitor.brightness, self._ddc_level_read, parent=self)

    def _ddc_level_read(self, level) -> None:
        if isinstance(level, Exception) or level is None:
            self._ddc_failed(str(level or ""))
            return
        self._ddc_last_ok = int(level)
        self.ddc_slider.blockSignals(True)
        self.ddc_slider.setValue(int(level))
        self.ddc_slider.blockSignals(False)
        self.set_value_text(self.ddc_value, int(level), "%")
        self.ddc_slider.setEnabled(True)
        monitor = self._ddc_monitor
        self.bind_text(self.ddc_hint,
                       monitor.description if monitor and monitor.description
                       else "яркость самой матрицы по DDC/CI")

    def _ddc_failed(self, reason: str):
        self.ddc_slider.setEnabled(False)
        self.bind_text(self.ddc_hint, "монитор не поддерживает DDC/CI")
        if reason:
            self.ddc_hint.setToolTip(reason)

    def on_ddc_changed(self, value: int):
        self.set_value_text(self.ddc_value, int(value), "%")
        if self._ddc_monitor is None:
            return
        self._ddc_pending = int(value)
        self._ddc_timer.start()

    def _ddc_apply(self):
        monitor = self._ddc_monitor
        if monitor is None or self._ddc_pending == self._ddc_last_ok:
            return
        value = self._ddc_pending
        gd_bg.submit(lambda: monitor.set_brightness(value), self._ddc_applied, parent=self)

    def _ddc_applied(self, result):
        if isinstance(result, Exception) or result is False:
            # монитор не принял — возвращаем ползунок к тому, что реально стоит
            self.ddc_slider.blockSignals(True)
            self.ddc_slider.setValue(self._ddc_last_ok)
            self.ddc_slider.blockSignals(False)
            self.set_value_text(self.ddc_value, self._ddc_last_ok, "%")
            self.set_status(i18n.t("монитор не принял яркость"), ok=False)
            return
        self._ddc_last_ok = self._ddc_pending


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
        if CONFIG_RECOVERY == "restored":
            self.set_status(i18n.t(
                "presets.json был повреждён — настройки восстановлены из последней "
                "целой копии; битый файл сохранён как presets.json.bak"))
        elif CONFIG_RECOVERY == "backup":
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

    def reset_app_settings(self) -> tuple[bool, str]:
        """Вернуть заводские настройки. Копия прежнего файла остаётся рядом."""
        import shutil

        source = config_path()
        backup: Path | None = None
        try:
            if source.exists():
                backup = source.with_name("presets.before-reset.json")
                shutil.copyfile(source, backup)
        except OSError:
            backup = None

        fresh = json.loads(json.dumps(DEFAULT_CONFIG))
        # тема и язык — из RUNTIME_SETTINGS: сброс обязан вернуть их к «как при
        # первом запуске», иначе окно останется в старой теме до перезапуска
        fresh["settings"]["theme"] = "dark"
        fresh["settings"]["language"] = None
        if not save_config(fresh):
            return False, i18n.t("не удалось записать файл настроек")
        self.config.clear()
        self.config.update(fresh)      # тот же dict: на него смотрят другие модули

        note = i18n.t("Настройки сброшены к заводским. Перезапустите Gammadrilon, "
                      "чтобы применить всё без остатка.")
        if backup is not None:
            note += " " + i18n.t("Копия прежних настроек: {v0}.", v0=backup.name)
        try:
            import gd_journal

            gd_journal.record("settings", i18n.t("Сброс настроек"), None, note,
                              source=i18n.t("Утилиты"))
        except Exception:  # noqa: BLE001 — журнал не должен ронять сброс
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
            preset: dict = {"name": str(item["name"]).strip()[:40]}
            for key in ("brightness", "contrast", "gamma"):
                preset[key] = clamp(safe_float(item.get(key), 1.0), 0.2, 3.0)
            preset["temperature"] = int(clamp(safe_float(item.get("temperature"), 6500), 3000, 10000))
            preset["shadow"] = int(clamp(safe_float(item.get("shadow"), 0), 0, 100))
            preset["ddc"] = int(clamp(safe_float(item.get("ddc"), -1), -1, 100))
            hotkey = item.get("hotkey")
            preset["hotkey"] = hotkey.strip() if isinstance(hotkey, str) else ""
            clean.append(preset)
        return (clean or None), dropped

    def set_check_updates(self, on: bool) -> None:
        """Галочка «проверять обновления при запуске»: по умолчанию выключена."""
        self.config.setdefault("settings", {})["check_updates"] = bool(on)
        self._save_config()

    def _set_update_status(self, text: str, ok: bool = True, url: str = "",
                           can_update: bool = False) -> None:
        """Статус проверки обновлений: в карточку или в отложенный слот.

        Вкладка «Утилиты» ленивая: тихая проверка при запуске может начаться
        раньше, чем страница собрана. Раньше статус в этом случае терялся.
        ``can_update`` показывает кнопку установки только там, где она уместна.
        """
        self._updates_pending_status = (text, ok, url, can_update)
        utils = getattr(self, "_utils_page", None)
        if utils is not None:
            utils.set_update_status(text, ok=ok, url=url, can_update=can_update)

    def check_updates(self, silent: bool = False) -> None:
        """Спросить GitHub о последнем релизе — в фоне, и только по галочке.

        ``silent`` (проверка при запуске) молчит, когда новостей нет: человек не
        просил отчёт, он просил не пропустить новый релиз. Тихая проверка ходит
        в сеть не чаще раза в сутки — метка лежит в настройках
        (``updates_last_check``), поэтому перезапуски подряд её не обходят.
        Ручная проверка («Проверить сейчас») не тормозится никогда.
        """
        import gd_bg

        settings = self.config.get("settings", {}) or {}
        if silent and not gd_update.due(settings.get("updates_last_check")):
            return
        if gd_bg.is_running(getattr(self, "_updates_worker", None)):
            return
        self._set_update_status(i18n.t("Спрашиваю GitHub…"))
        self._updates_worker = gd_bg.submit(
            gd_update.fetch_latest, lambda res: self._updates_done(res, silent), parent=self)

    def _updates_done(self, res, silent: bool) -> None:
        """Результат проверки обновлений пришёл в GUI-поток: показать честно."""
        import gd_toast

        # Отметку ставим и при неудаче: следующий тихий запуск не должен
        # немедленно повторять тот же запрос (без сети — каждые пару минут).
        self.config.setdefault("settings", {})["updates_last_check"] = gd_update.stamp()
        self._save_config()

        # карточка обновлений живёт на ленивой вкладке «Утилиты»: результат
        # проверки попадает в неё так же, как до ленивой сборки
        self._ensure_page("tab.utils")
        if not isinstance(res, dict) or not res.get("ok"):
            error = (res or {}).get("error") if isinstance(res, dict) else str(res)
            text = i18n.t("Проверка обновлений не удалась: {v0}",
                          v0=error or i18n.t("неизвестная причина"))
            self._set_update_status(text, ok=False)
            if not silent:
                gd_toast.show_toast(text, "warn")
            return
        tag = str(res.get("tag") or "")
        self._latest_release = res if res.get("ok") else None
        if gd_update.is_newer(tag, APP_VERSION):
            import gd_autoupdate

            mode = gd_autoupdate.install_mode()
            asset = gd_autoupdate.asset_for(mode, res.get("assets"))
            can_update = (mode in ("onedir", "onefile")
                          and asset is not None
                          and bool(gd_autoupdate.digest_hex(asset)))
            text = i18n.t("Доступна новая версия {v0} (установлена {v1}).", v0=tag, v1=APP_VERSION)
            self._set_update_status(text, ok=True, url=str(res.get("url") or ""),
                                    can_update=can_update)
            gd_toast.show_toast(i18n.t("toast.update_available", v0=tag), "info")
            try:
                import gd_journal

                gd_journal.record("update", i18n.t("Доступна новая версия"), None, tag,
                                  source=i18n.t("Проверка обновлений"))
            except Exception:  # noqa: BLE001
                pass
        else:
            self._latest_release = None
            text = i18n.t("Установлена последняя версия ({v0}).", v0=APP_VERSION)
            self._set_update_status(text, ok=True)
            if not silent:
                gd_toast.show_toast(text, "ok")

    def install_update(self) -> None:
        """Скачать новый релиз и перезапуститься через helper.

        Настройки не трогаются: helper меняет только exe и ``_internal``.
        В исходниках кнопки нет — там обновление нечем применять.
        """
        import gd_autoupdate
        import gd_bg
        import gd_dialog

        info = getattr(self, "_latest_release", None)
        if not isinstance(info, dict) or not info.get("ok"):
            return

        mode = gd_autoupdate.install_mode()
        if mode == "dev":
            gd_dialog.notify(
                self, i18n.t("Обновление Gammadrilon"),
                i18n.t("Автообновление работает только в собранной версии. "
                       "Скачайте архив со страницы релиза."))
            return

        asset = gd_autoupdate.asset_for(mode, info.get("assets"))
        if asset is None:
            gd_dialog.notify(
                self, i18n.t("Обновление Gammadrilon"),
                i18n.t("В релизе нет файла для автообновления — скачайте вручную."))
            return
        if not gd_autoupdate.digest_hex(asset):
            gd_dialog.notify(
                self, i18n.t("Обновление Gammadrilon"),
                i18n.t("В релизе нет контрольной суммы sha256 — автообновление отключено."))
            return

        version = str(info.get("tag") or "")
        size_mb = max(0, int(asset.get("size") or 0)) // (1024 * 1024)
        if size_mb:
            question = i18n.t(
                "Скачать обновление {v0} ({v1} МБ) и перезапустить приложение? "
                "Пресеты, горячие клавиши и настройки сохранятся.",
                v0=version, v1=size_mb)
        else:
            question = i18n.t(
                "Скачать обновление {v0} и перезапустить приложение? "
                "Пресеты, горячие клавиши и настройки сохранятся.", v0=version)

        # что именно ставим: описание релиза показываем до подтверждения
        notes = gd_update.release_notes(info)
        if notes:
            question += "\n\n" + i18n.t("Что нового:\n{v0}", v0=notes)

        if not gd_dialog.confirm(
                self, i18n.t("Обновление Gammadrilon"), question,
                confirm_text=i18n.t("Обновить")):
            return

        if gd_bg.is_running(getattr(self, "_update_worker", None)):
            return
        self._set_update_status(
            i18n.t("Скачиваю обновление {v0}…", v0=version), can_update=False)
        self._update_worker = gd_bg.submit(
            lambda: gd_autoupdate.prepare_update(info, mode),
            self._update_prepared, parent=self)

    def _update_prepared(self, res) -> None:
        """Обновление скачано и проверено: запустить helper и выйти."""
        import gd_autoupdate

        if not isinstance(res, dict) or not res.get("ok"):
            error = (res or {}).get("error") if isinstance(res, dict) else str(res)
            text = i18n.t("Не удалось скачать обновление: {v0}",
                          v0=error or i18n.t("неизвестная причина"))
            self._set_update_status(text, ok=False, can_update=True)
            try:
                import gd_dialog

                gd_dialog.notify(self, i18n.t("Обновление Gammadrilon"), text)
            except Exception:  # noqa: BLE001
                pass
            return

        plan = res.get("plan") or {}
        try:
            gd_autoupdate.launch_helper(plan)
        except Exception as exc:  # noqa: BLE001
            text = i18n.t("Не удалось запустить обновление: {v0}", v0=exc)
            self._set_update_status(text, ok=False, can_update=True)
            try:
                import gd_dialog

                gd_dialog.notify(self, i18n.t("Обновление Gammadrilon"), text)
            except Exception:  # noqa: BLE001
                pass
            return

        version = str(res.get("version") or "")
        self._set_update_status(
            i18n.t("Обновление {v0} готово. Перезапускаюсь…", v0=version))
        try:
            import gd_journal

            gd_journal.record("update", i18n.t("Обновление приложения"), None,
                              version, source=i18n.t("Утилиты"))
        except Exception:  # noqa: BLE001
            pass
        # Даём helper-у запуститься и только потом закрываем окно. Он ждёт PID,
        # поэтому гонки «приложение ещё живо» здесь нет.
        QTimer.singleShot(500, self.quit_app)

    def stop_crane(self) -> None:
        """«Вернуть всё как было» — один вход для трея, палитры и горячей клавиши.

        У кнопки в «Утилитах» свой вопрос (она рядом с описанием), а у стоп-крана
        с клавиатуры и из трея — этот: что именно откатывается, человек может не
        помнить, а действие необратимое (обратно — только вручную).
        """
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
        gd_boot.mark("showEvent")

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
        self._stop_foreground_watch()
        self._ddc_timer.stop()
        try:
            if self._ddc is not None:
                self._ddc.close()
        except Exception:
            pass

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
gd_boot.mark("module")


def main():
    if sys.platform != "win32":
        print("This application works only on Windows.")
        return

    gd_boot.mark("main")

    # --selftest: проверка установки без единого окна. Нужна и при сборке
    # (релиз не уходит, пока провалы), и человеку, у которого «не работает»:
    # отчёт печатается в консоль, код возврата — 1 при провале.
    if "--selftest" in sys.argv:
        import gd_diagnose

        items = gd_diagnose.checks()
        # консоль Windows часто в cp1251/cp866: печатаем только ASCII-маркеры,
        # иначе сам отчёт падает с UnicodeEncodeError
        try:
            print(gd_diagnose.report_text(items, ascii_only=True))
            print(f"\nпровалов: {len([i for i in items if i.get('status') == 'fail'])} из {len(items)}")
        except Exception:  # noqa: BLE001 — отчёт не должен мешать коду возврата
            pass
        failed = [item for item in items if str(item.get("status")) == "fail"]
        sys.exit(1 if failed else 0)

    # язык нужен раньше окна: сообщение «уже запущено» тоже должно быть переведено
    i18n.init_for_process()

    # Тема — до сборки окна: страницы создаются уже со своим QSS, и повторная
    # полировка всего дерева при старте (замер: ~330 мс) больше не нужна.
    global APP_STYLESHEET
    try:
        _boot_cfg = load_config()
        th.apply((_boot_cfg.get("settings") or {}).get("theme", "dark"))
        # Приложение могло упасть с включённым игровым бустом: приоритеты
        # фоновых процессов остались изменёнными. Возвращаем до показа окна.
        try:
            import gd_boost

            gd_boost.GameBoost().restore_from_disk()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    APP_STYLESHEET = build_app_stylesheet()

    # пробам замеров (GD_ALLOW_SECOND_INSTANCE=1) нужно поднять второе окно
    # рядом с работающим приложением: мьютекс для них пропускаем
    if not os.environ.get("GD_ALLOW_SECOND_INSTANCE") and is_another_instance_running():
        _warn_already_running()
        return

    install_crash_handlers()

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    app.setQuitOnLastWindowClosed(False)
    gd_boot.mark("app")

    # общий фоновой сборщик метрик: UI читает кэш и не дёргает nvidia-smi/netsh
    try:
        import gd_metrics

        gd_metrics.start()
        app.aboutToQuit.connect(gd_metrics.stop)
    except Exception:  # noqa: BLE001
        pass
    gd_boot.mark("metrics")

    icon_path = resource_path(ICON_FILE)

    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    gd_boot.mark("window")
    window.show()
    gd_boot.mark("shown")
    # первый проход цикла событий: к этому моменту окно уже отрисовано
    QTimer.singleShot(0, lambda: gd_boot.mark("loop"))

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
