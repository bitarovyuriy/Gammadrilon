# -*- coding: utf-8 -*-
"""Пустой конфиг не роняет ни одну вкладку.

Зачем: первый запуск на чужом ПК — это пустые пресеты, нет zapret, нет
истории. Любой слот, который ждёт готовых данных, падает именно там, а у
разработчика всё зелёное, потому что конфиг богатый.

Что делает: подсовывает пустую папку данных (`LOCALAPPDATA` до импорта
`main`), поднимает окно offscreen, по очереди открывает все вкладки и ловит
необработанные исключения.

Запуск:
  python tools/pages_check.py            # русский интерфейс
  python tools/pages_check.py --lang en  # английский: у него свои строки и формат
  python tools/pages_check.py --no-sensors  # как на машине без драйвера датчиков
Код 1, если что-то упало.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

# пустой профиль: приложение решит, что его только что установили. Ставим до
# импорта main — конфиг читается на импорте
_TEMP = Path(tempfile.mkdtemp(prefix="gd_pages_"))
os.environ["LOCALAPPDATA"] = str(_TEMP)
os.environ["GD_PORTABLE"] = "0"

# Профиль всё равно пустой (нет пресетов, нет zapret, нет истории), но
# `first_run_done` ставим: иначе приложение честно открывает диалог первого
# запуска, а его `exec_()` в offscreen блокирует проверку навсегда.
_cfg = _TEMP / "Gammadrilon"
_cfg.mkdir(parents=True, exist_ok=True)
_settings = {"first_run_done": True}
if "--no-sensors" in sys.argv:
    # профиль «чужой ПК без драйвера PawnIO»: пустые температуры не должны
    # ни падать, ни врать нулями
    _settings["sensors"] = False
(_cfg / "presets.json").write_text(json.dumps({"settings": _settings}), encoding="utf-8")

import main as gd_main  # noqa: E402  (импорт после подмены окружения)

_APP = None
PROBLEMS: list[str] = []


def app():
    global _APP
    from PyQt5.QtWidgets import QApplication

    _APP = QApplication.instance() or QApplication([])
    return _APP


def _hook(exc_type, exc_value, exc_tb):
    PROBLEMS.append("".join(traceback.format_exception_only(exc_type, exc_value)).strip())
    traceback.print_exception(exc_type, exc_value, exc_tb)


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        _APP.processEvents()
        time.sleep(0.02)


def main() -> int:
    sys.excepthook = _hook
    app().setStyleSheet(gd_main.build_app_stylesheet())
    if "--lang" in sys.argv:
        index = sys.argv.index("--lang")
        if index + 1 < len(sys.argv):
            language = sys.argv[index + 1]
            gd_main.i18n.set_language(language)
            print(f"язык: {language}")

    window = gd_main.MainWindow()
    window.resize(window.compact_size)
    window.show()
    pump(1500)

    mapping = getattr(window, "_key_to_index", {}) or {}
    pages = sorted(((key, index) for key, index in mapping.items()
                    if isinstance(index, int)), key=lambda item: item[1])
    print(f"профиль: {_TEMP}")
    print(f"вкладок: {len(pages)}")
    for key, index in pages:
        before = len(PROBLEMS)
        try:
            window.tabs.setCurrentIndex(index)
            pump(500)
        except Exception as exc:  # noqa: BLE001
            PROBLEMS.append(f"{key}: {type(exc).__name__}: {exc}")
        mark = "ок " if len(PROBLEMS) == before else "ПАДЕНИЕ"
        print(f"{mark:<10} {key}")

    window.close()
    if PROBLEMS:
        print("\nОШИБКИ:")
        for line in PROBLEMS:
            print(f"  - {line}")
        return 1
    print("\nвсе вкладки открылись на пустом конфиге")
    return 0


if __name__ == "__main__":
    sys.exit(main())
