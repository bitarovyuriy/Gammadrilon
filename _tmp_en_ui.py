# -*- coding: utf-8 -*-
"""EN-прогон интерфейса (offscreen): нет ли русских подписей в английском режиме.

Запуск:  .venv\\Scripts\\python.exe _tmp_en_ui.py

Ничего не меняет в системе: компаньоны не поднимаются (UAC выключен заглушкой),
вкладки только читают состояние. Русские строки в данных (имена процессов,
записи журнала, имена файлов стратегий) — не хардкод, а данные; они попадают
в отдельный список «данные».
"""
from __future__ import annotations

import io
import os
import re
import sys
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                              os.path.join(_qt_plugins, "platforms"))
except Exception:  # noqa: BLE001
    pass

if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import time  # noqa: E402

from PyQt5.QtGui import QFontDatabase  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QGroupBox, QLabel, QLineEdit, QListWidget,
    QPushButton, QRadioButton, QTabBar, QTableWidget,
)

import gd_helper  # noqa: E402
import gd_i18n as i18n  # noqa: E402
import main as gd_main  # noqa: E402

CYR = re.compile(r"[А-Яа-яЁё]")
OUT = os.path.join(HERE, "build", "shots")
os.makedirs(OUT, exist_ok=True)

app = QApplication([])
_fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
for _f in ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf"):
    _p = os.path.join(_fonts_dir, _f)
    if os.path.isfile(_p):
        QFontDatabase.addApplicationFont(_p)
app.setStyleSheet(gd_main.build_app_stylesheet())


def pump(ms: int) -> None:
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.02)


# --- заглушки: UAC не поднимаем, компаньоны не запускаем, файл настроек не пишем
gd_helper.HelperClient.ensure = lambda self: False          # noqa: ARG005
gd_helper.HelperClient.has_session = lambda self: False     # noqa: ARG005

_CFG = os.path.join(HERE, "presets.json")


def _cfg_state() -> tuple[float, str]:
    try:
        with io.open(_CFG, encoding="utf-8") as fh:
            text = fh.read()
        return os.path.getmtime(_CFG), text
    except OSError:
        return 0.0, ""


_cfg_before = _cfg_state()
_saves: list[str] = []


def _no_save(data):        # noqa: ARG001
    """Прогон проверяет подписи, а не запись: файл настроек не трогаем.

    Каждую попытку запоминаем вместе со стеком вызовов — иначе в отчёте видно
    «писали 1 раз», но не видно, кто и зачем.
    """
    frames = [f"{os.path.basename(fr.filename)}:{fr.lineno} {fr.name}"
              for fr in traceback.extract_stack()[:-1]]
    _saves.append(" ← ".join(reversed(frames[-3:])))


gd_main.save_config = _no_save

i18n.set_language("en")

# окно читает язык из presets.json — подставляем EN в памяти, файл не трогаем
_real_load_config = gd_main.load_config


def _en_config():
    cfg = _real_load_config()
    cfg.setdefault("settings", {})["language"] = "en"
    return cfg


gd_main.load_config = _en_config

win = gd_main.MainWindow()
i18n.set_language("en")          # окно могло выставить язык конфига — держим EN
win.retranslate_all() if hasattr(win, "retranslate_all") else None
win.resize(win.compact_size)
win.show()
pump(1200)

found: list[tuple[str, str, str]] = []      # (вкладка, класс#имя, текст) — хардкод
data: list[tuple[str, str, str]] = []       # данные: журнал, имена схем Windows и т.п.


def is_data(tab: str, name: str, text: str) -> bool:
    """Русские строки в данных — не хардкод: их пишет система или прошлые сессии."""
    if tab == "tab.agent" and name.startswith("QListWidget"):
        return True                      # записи аудита: язык момента записи
    if "scheme «" in text:
        return True                      # имя схемы питания даёт сама Windows
    if "°C" in text or "PID " in text:
        return True                      # метрики и имена процессов
    return False


def texts_of(widget, tab_name: str) -> None:
    for child in widget.findChildren(object):
        text = None
        try:
            if isinstance(child, (QLabel, QPushButton, QCheckBox, QRadioButton, QGroupBox)):
                text = child.text() if hasattr(child, "text") else None
                if isinstance(child, QGroupBox):
                    text = child.title()
            elif isinstance(child, QLineEdit):
                text = child.placeholderText()
        except RuntimeError:            # виджет уже удалён
            continue
        if not text or not CYR.search(text):
            continue
        name = f"{type(child).__name__}#{child.objectName() or '-'}"
        row = (tab_name, name, text.replace("\n", " ⏎ "))
        (data if is_data(tab_name, name, text) else found).append(row)


for key, index in sorted(win._key_to_index.items(), key=lambda kv: kv[1]):
    win.tabs.setCurrentIndex(index)
    pump(900)
    texts_of(win.tabs.currentWidget(), key)
    for bar in win.tabs.currentWidget().findChildren(QTabBar):
        for i in range(bar.count()):
            t = bar.tabText(i)
            if CYR.search(t):
                found.append((key, "QTabBar", t))
    for table in win.tabs.currentWidget().findChildren(QTableWidget):
        for col in range(table.columnCount()):
            item = table.horizontalHeaderItem(col)
            if item is not None and CYR.search(item.text()):
                found.append((key, "QHeaderView", item.text()))
    for lst in win.tabs.currentWidget().findChildren(QListWidget):
        for row in range(min(lst.count(), 40)):
            t = lst.item(row).text()
            if CYR.search(t):
                item = (key, "QListWidget(строка)", t)
                (data if is_data(key, "QListWidget(строка)", t) else found).append(item)

# сайдбар, статусбар, трей
texts_of(win, "оболочка")
for _action in win.tray.contextMenu().actions():
    if CYR.search(_action.text()):
        row = ("трей", "QAction", _action.text())
        (data if is_data("трей", "QAction", _action.text()) else found).append(row)

lines = [f"проверено вкладок: {len(win._key_to_index)}"]
if found:
    lines.append("хардкод (русский текст в EN):")
    for tab, name, text in found:
        lines.append(f"   {tab:18} {name:28} {text[:90]}")
if data:
    lines.append(f"данные (русский текст из системы/прошлых сессий): {len(data)}")
    for tab, name, text in data[:6]:
        lines.append(f"   {tab:18} {name:28} {text[:90]}")

# если конфиг разрешал автостарт API, окно подняло сервер — гасим за собой
# (remember=False: флаг в конфиге не трогаем, файл настроек не пишем)
try:
    if win.agent_state().get("running"):
        win.stop_agent_api(remember=False)
except Exception:  # noqa: BLE001
    pass

_cfg_after = _cfg_state()
cfg_untouched = _cfg_before == _cfg_after
if _saves:
    lines.append(f"presets.json: окно писало конфиг {len(_saves)} раз(а) — лишняя запись на диск")
    for _site in _saves:
        lines.append(f"   {_site}")
else:
    lines.append("presets.json: попыток записи от окна 0"
                 + ("" if cfg_untouched
                    else " (файл изменил другой процесс — инструмент запускался параллельно)"))
if not cfg_untouched:
    with io.open(os.path.join(OUT, "cfg_before.txt"), "w", encoding="utf-8") as fh:
        fh.write(_cfg_before[1])
    with io.open(os.path.join(OUT, "cfg_after.txt"), "w", encoding="utf-8") as fh:
        fh.write(_cfg_after[1])
report = "\n".join(lines)
print(report)
with io.open(os.path.join(OUT, "en_ui.txt"), "w", encoding="utf-8") as fh:
    fh.write(report + "\n")
ok = not found and not _saves
print("RESULT:", "OK" if ok else f"НАЙДЕНО {len(found)}, записей конфига {len(_saves)}")
sys.exit(0 if ok else 1)
