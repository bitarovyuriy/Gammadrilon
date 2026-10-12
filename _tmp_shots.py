# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ скрипт визуального аудита UX/UI (удаляется после прогона).

Что делает:
  * offscreen-рендер 6 вкладок в двух темах (окно + полный контент длинных страниц);
  * снимки меню трея, диалогов (первый запуск, журнал, палитра, правило, ProcessPicker),
    тостов (ok/error), подтверждения «Сбросить всё»;
  * замеры: цвета вне палитры, обрезка текста, нарушение минимальных размеров.

Чего НЕ делает: не применяет профили CPU/GPU, не пишет presets.json
(тема переключается напрямую th.apply), не трогает правила/тихий час.
"""
from __future__ import annotations

import os
import sys
import time
import collections

os.environ["QT_QPA_PLATFORM"] = "offscreen"

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
        os.environ.setdefault(
            "QT_QPA_PLATFORM_PLUGIN_PATH", os.path.join(_qt_plugins, "platforms")
        )
except Exception:  # noqa: BLE001
    pass

if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:  # кириллица в выводе PowerShell (cp1251) ломает print
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

OUT = os.path.join(HERE, "build", "shots")
os.makedirs(OUT, exist_ok=True)

TAIL_ONLY = "--tail" in sys.argv  # только оверлеи+анализ, снимки вкладок из кэша

if not TAIL_ONLY:  # чистая папка, чтобы отчёт не смешивался со старыми снимками
    for _fn in list(os.listdir(OUT)):
        if _fn.endswith(".png"):
            try:
                os.remove(os.path.join(OUT, _fn))
            except OSError:
                pass

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QFontDatabase  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyleFactory,
    QTableWidget,
)

import gd_theme as th  # noqa: E402
import main as gd_main  # noqa: E402

app = QApplication([])
# offscreen-плагин по умолчанию берёт Fusion, а рабочая сборка идёт на windowsvista:
# без этого стиль нативных частей (стрелки спинбоксов и т.п.) не совпадёт с реальностью.
if "windowsvista" in QStyleFactory.keys():
    app.setStyle("windowsvista")
print("STYLE:", app.style().objectName(), flush=True)
# offscreen-плагин не видит системные шрифты (путь с кириллицей) — подключаем вручную,
# иначе текст на снимках не рендерится вовсе.
_fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
for _fname in ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf", "bahnschrift.ttf",
               "arial.ttf", "consola.ttf", "seguisym.ttf"):
    _fpath = os.path.join(_fonts_dir, _fname)
    if os.path.isfile(_fpath):
        QFontDatabase.addApplicationFont(_fpath)
app.setStyleSheet(gd_main.build_app_stylesheet())

SHOTS: list[tuple] = []
CLIP: list[tuple] = []
SMALL: list[tuple] = []
COLORS: dict[str, dict] = {}


def pump(ms: int) -> None:
    """Прокрутить цикл событий заданное время (фоновые задачи успевают)."""
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.02)


def shot(widget, name: str) -> None:
    path = os.path.join(OUT, name + ".png")
    pm = widget.grab()
    pm.save(path)
    SHOTS.append((name, pm.width(), pm.height(), os.path.getsize(path)))


def set_theme(name: str) -> None:
    """Переключить тему без записи в presets.json (в отличие от win.apply_theme)."""
    th.apply(name)
    gd_main.APP_STYLESHEET = gd_main.build_app_stylesheet()
    app.setStyleSheet(gd_main.APP_STYLESHEET)
    win.setStyleSheet(gd_main.APP_STYLESHEET)
    for attr in ("_power_page", "_gpu_page", "_rules_page", "_utils_page", "_dns_page"):
        page = getattr(win, attr, None)
        if page is not None:
            page.setStyleSheet(th.STYLESHEET)
    win.update()


def analyze_colors(name: str, theme: str) -> None:
    """Цвета вне палитры темы (градации серого допустимы, цветные — нет)."""
    from PyQt5.QtGui import QImage

    img = QImage(os.path.join(OUT, name + ".png"))
    img = img.convertToFormat(QImage.Format_RGB32)
    data = img.constBits().asstring(img.byteCount())
    pixels = list(zip(data[0::4], data[1::4], data[2::4]))
    counter = collections.Counter(pixels)
    coloured = collections.Counter(p for p in counter if max(p) - min(p) > 6)
    COLORS[name] = {
        "theme": theme,
        "unique": len(counter),
        "top": [("#%02X%02X%02X" % c, counter[c]) for c in [p for p, _ in counter.most_common(6)]],
        "coloured": [("#%02X%02X%02X" % c, counter[c]) for c in [p for p, _ in coloured.most_common(6)]],
        "coloured_total": sum(counter[p] for p in coloured),
    }


TEXT_WIDGETS = (QLabel, QPushButton, QCheckBox, QComboBox)


def check_layout(name: str, root=None) -> None:
    """Обрезка текста и нарушение минимальных размеров внутри виджета."""
    page = root if root is not None else win.tabs.currentWidget()
    if page is None:
        return
    for w in page.findChildren(TEXT_WIDGETS):
        if not w.isVisible() or w.width() <= 1:
            continue
        text = (w.text() if hasattr(w, "text") else "") or w.objectName()
        if not text.strip():
            continue
        if not isinstance(w, QLabel) or not w.wordWrap():
            if w.sizeHint().width() > w.width() + 1:
                CLIP.append((name, type(w).__name__, text[:60], w.width(),
                             w.sizeHint().width()))
        if w.minimumSizeHint().width() > w.width() + 1:
            CLIP.append((name + " [min]", type(w).__name__, text[:60], w.width(),
                         w.minimumSizeHint().width()))
        if w.height() + 1 < w.minimumSizeHint().height():
            SMALL.append((name, type(w).__name__, text[:60], w.height(),
                          w.minimumSizeHint().height()))


win = gd_main.MainWindow()
win.resize(win.compact_size)
win.show()
pump(2000)  # первый снимок метрик CPU требует ~1 c
print("MAIN WINDOW:", win.width(), "x", win.height(), flush=True)

if TAIL_ONLY:  # снимки вкладок уже на диске — берём их в отчёт без повторного рендера
    from PyQt5.QtGui import QImage

    for fn in sorted(os.listdir(OUT)):
        if not fn.endswith(".png") or fn.startswith("_"):
            continue
        path = os.path.join(OUT, fn)
        img = QImage(path)
        SHOTS.append((fn[:-4], img.width(), img.height(), os.path.getsize(path)))
    print("TAIL ONLY:", len(SHOTS), "снимков из кэша", flush=True)

# --- вкладки: ключ, slug, атрибут «полного контента» -------------------------
PAGES = [
    ("tab.gamma", "gamma", None),
    ("tab.cpu", "cpu", "_power_page"),
    ("tab.gpu", "gpu", "_gpu_page"),
    ("tab.rules", "rules", "_rules_page"),
    ("tab.utils", "utils", "_utils_page"),
    ("tab.dns", "dns", "_dns_page"),
    ("tab.zapret", "zapret", "_zapret_page"),
]

for theme in (() if TAIL_ONLY else ("dark", "light")):
    set_theme(theme)
    for key, slug, full_attr in PAGES:
        idx = win._key_to_index.get(key)
        if idx is None:
            print("!! нет страницы", key, flush=True)
            continue
        win.tabs.setCurrentIndex(idx)
        pump(1400)
        shot(win, f"{theme}-{slug}-window")
        check_layout(f"{theme}-{slug}")
        if full_attr:
            page = getattr(win, full_attr, None)
            if page is not None:
                shot(page, f"{theme}-{slug}-full")
print("TABS DONE", flush=True)

# --- меню трея, диалоги, оверлеи (тёмная тема) ------------------------------
set_theme("dark")
pump(200)

menu = getattr(win.tray, "_menu", None)
if menu is not None:
    try:
        menu.popup(win.mapToGlobal(win.rect().topLeft()))
        pump(300)
        shot(menu, "dark-tray-menu")
        menu.close()
        pump(80)
    except Exception as exc:  # noqa: BLE001
        print("!! меню трея:", type(exc).__name__, exc, flush=True)


def dialog_shot(name: str, factory, wait: int = 900) -> None:
    dlg = None
    try:
        dlg = factory()
        dlg.show()
        pump(wait)
        shot(dlg, name)
        check_layout(name, dlg)
    except Exception as exc:  # noqa: BLE001
        print("!! диалог", name, type(exc).__name__, exc, flush=True)
    finally:
        if dlg is not None:
            try:
                dlg.close()  # deleteLater не вызываем: у диалогов живут фоновые воркеры
            except Exception:  # noqa: BLE001
                pass
            pump(100)


try:
    from gd_onboarding import FirstRunDialog
    from gd_journal_ui import JournalDialog
    from gd_palette import CommandPalette
    from gd_rules_ui import RuleDialog
    from gd_utils_ui import ProcessPicker

    dialog_shot("dark-dialog-onboarding", lambda: FirstRunDialog(win))
    dialog_shot("dark-dialog-journal", lambda: JournalDialog(win))
    dialog_shot("dark-dialog-palette", lambda: CommandPalette(win))
    dialog_shot("dark-dialog-rule", lambda: RuleDialog(None, win))
    dialog_shot("dark-dialog-processpicker", lambda: ProcessPicker(win), wait=2600)
except Exception as exc:  # noqa: BLE001
    print("!! импорт диалогов:", type(exc).__name__, exc, flush=True)

import gd_i18n as i18n  # noqa: E402

try:
    box = QMessageBox(win)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(i18n.t("Сбросить все изменения"))
    box.setText(i18n.t(
        "Вернуть питание CPU/GPU, DNS, правила, автоочистку, таймер и гамму "
        "к состоянию системы по умолчанию?"))
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    # ВАЖНО: box.show() в offscreen-плагине падает (Access Violation) — рендерим без показа
    box.adjustSize()
    box.resize(box.sizeHint())
    pump(200)
    shot(box, "dark-dialog-reset-confirm")
    box.close()
    pump(100)
except Exception as exc:  # noqa: BLE001
    print("!! reset-confirm:", type(exc).__name__, exc, flush=True)

import gd_toast  # noqa: E402

for kind in ("ok", "error"):
    try:
        gd_toast.show_toast("DNS переключён на Cloudflare (1.1.1.1)", kind=kind)
        pump(300)
        if gd_toast._stack:
            shot(gd_toast._stack[-1], f"dark-overlay-toast-{kind}")
    except Exception as exc:  # noqa: BLE001
        print("!! toст", kind, type(exc).__name__, exc, flush=True)
print("OVERLAYS DONE", flush=True)

# --- анализ снимков ---------------------------------------------------------
for name, _w, _h, _size in SHOTS:
    analyze_colors(name, name.split("-", 1)[0])

print("\n========== СНИМКИ (%d) ==========" % len(SHOTS))
for name, w, h, size in SHOTS:
    print(f"  {name}: {w}x{h}, {size // 1024} KB")

print("\n========== ЦВЕТ ВНЕ ПАЛИТРЫ ==========")
bad = 0
for name, data in COLORS.items():
    if data["coloured_total"]:
        bad += 1
        print(f"  {name} ({data['theme']}): {data['coloured_total']} px",
              data["coloured"])
print("  снимков с цветными пикселями:", bad, "из", len(COLORS))

print("\n========== ОБРЕЗКА ТЕКСТА (%d) ==========" % len(CLIP))
seen: set = set()
for item in CLIP:
    key = (item[0], item[2])
    if key in seen:
        continue
    seen.add(key)
    print("  ", " | ".join(str(x) for x in item))

print("\n========== ВЫСОТА МЕНЬШЕ МИНИМУМА (%d) ==========" % len(SMALL))
seen2: set = set()
for item in SMALL:
    key = (item[0], item[2])
    if key in seen2:
        continue
    seen2.add(key)
    print("  ", " | ".join(str(x) for x in item))

import json  # noqa: E402

with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as fh:
    json.dump({"shots": SHOTS, "colours": COLORS, "clip": CLIP, "small": SMALL},
              fh, ensure_ascii=False, indent=1)

print("\nOK: снимки в", OUT, flush=True)

try:
    import gd_bg
    import gd_metrics

    gd_metrics.stop()
    gd_bg.wait_all(1500)
except Exception:  # noqa: BLE001
    pass
try:
    win.tray.hide()
except Exception:  # noqa: BLE001
    pass
win.close()
sys.stdout.flush()
os._exit(0)
