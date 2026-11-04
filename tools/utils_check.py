# -*- coding: utf-8 -*-
"""Проверка разделов «Утилит» на пустом профиле.

Вкладки внутри «Утилит» собираются лениво, и обычный `pages_check` их не
видит: он открывает только верхние вкладки. Скрипт заходит в «Утилиты» и
переключает каждый раздел, ловя исключения.

Запуск: `.venv/Scripts/python.exe tools/utils_check.py [--lang en]`
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# пустой профиль до импорта main: конфиг читается на импорте
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="gd_utils_")
os.environ["GD_PORTABLE"] = "0"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

profile = Path(os.environ["LOCALAPPDATA"]) / "Gammadrilon"
profile.mkdir(parents=True, exist_ok=True)
# онбординг вызывает exec_() и в offscreen висит навсегда
(profile / "presets.json").write_text(
    '{"settings": {"first_run_done": true}}', encoding="utf-8"
)

failures: list[str] = []


def main() -> int:
    import main as gd_main
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    win = gd_main.MainWindow()
    win.show()
    app.processEvents()

    if "--lang" in sys.argv:
        lang = sys.argv[sys.argv.index("--lang") + 1]
        from gd_i18n import set_language

        set_language(lang, win.config)
        app.processEvents()

    win._ensure_page("tab.utils")
    utils = getattr(win, "_utils_page", None)
    if utils is None:
        print("ПРОВАЛ: вкладка «Утилиты» не собралась")
        return 1

    for name, label in (
        ("mem", "Память"),
        ("sys", "Система"),
        ("games", "Игры"),
        ("rules", "Правила"),
        ("keys", "Клавиши"),
    ):
        try:
            utils.show_section(name)
            app.processEvents()
            widget = utils.section_widget(name)
            if name in ("games", "rules", "keys") and widget is None:
                failures.append(f"{name}: раздел не создан")
                continue
            print(f"ок        раздел {name} ({label})")
        except Exception:  # noqa: BLE001
            failures.append(f"{name}: {traceback.format_exc(limit=2)}")
            print(f"ПРОВАЛ    раздел {name}")

    # старые пути плиток должны вести в те же разделы, а не в пустоту
    for key, name in (("tab.cs2", "games"), ("tab.rules", "rules")):
        try:
            widget = win._ensure_page(key)
            if widget is None:
                failures.append(f"{key}: старый путь не открывает раздел")
            else:
                print(f"ок        старый путь {key} → {name}")
        except Exception:  # noqa: BLE001
            failures.append(f"{key}: {traceback.format_exc(limit=2)}")

    print()
    if failures:
        for item in failures:
            print("ПРОВАЛ:", item)
        return 1
    print("все разделы «Утилит» открылись")
    return 0


if __name__ == "__main__":
    sys.exit(main())
