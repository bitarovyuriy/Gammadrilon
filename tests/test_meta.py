# -*- coding: utf-8 -*-
"""Быстрые проверки договорённостей проекта: без Qt, без системы, за секунды.

Здесь ловится то, что раньше находил только человек: номер версии, вписанный
мимо ``APP_VERSION``, русский текст без английского перевода, ручной список
модулей в spec, способ упаковки zip. Тесты гоняет CI на каждый пуш.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"
GD_MODULES = sorted(ROOT.glob("gd_*.py"))

sys.path.insert(0, str(ROOT))


def _app_version() -> str:
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', MAIN.read_text(encoding="utf-8"), re.M)
    assert match, "в main.py не найден APP_VERSION"
    return match.group(1)


def test_version_has_single_source():
    """Своих «vX.Y» в модулях быть не должно: версия живёт в APP_VERSION.

    Так уже было: сайдбар показывал v1.0.1, титлбар — v3.1, и никто не замечал.
    Документы и сборка тоже под контролем: номер версии в README, чеклисте,
    spec, CI или скрипте сборки разошёлся бы с приложением молча.
    """
    version = _app_version()
    pattern = re.compile(r'"v\d+(?:\.\d+)+"')

    # Документы и сборка: здесь версия пишется без кавычек (в тексте и YAML)
    doc_pattern = re.compile(r"\bv\d+(?:\.\d+)+\b")
    docs = [
        ROOT / "README.md",
        ROOT / "RELEASE_CHECKLIST.md",
        ROOT / "build_release.ps1",
        ROOT / ".github" / "workflows" / "ci.yml",
        *sorted(ROOT.glob("*.spec")),
    ]

    offenders = []
    for path in [MAIN, *GD_MODULES]:
        if not path.exists():
            continue
        for found in pattern.findall(path.read_text(encoding="utf-8", errors="replace")):
            if found != f'"{version}"':
                offenders.append(f"{path.name}: {found}")
    for path in docs:
        if not path.exists():
            offenders.append(f"{path.name}: файл пропал")
            continue
        for found in doc_pattern.findall(path.read_text(encoding="utf-8", errors="replace")):
            if found != version:
                offenders.append(f"{path.name}: {found}")
    assert not offenders, "версия вписана мимо APP_VERSION — " + ", ".join(offenders)


def test_i18n_english_complete():
    """У каждого русского ключа есть английский текст — иначе в EN всплывает RU."""
    import gd_i18n

    ru = gd_i18n.STRINGS["ru"]
    en = gd_i18n.STRINGS["en"]
    direct = gd_i18n.DIRECT

    missing = sorted(key for key in ru if key not in en and key not in direct)
    assert not missing, "нет английского перевода: " + "; ".join(missing[:10])

    orphan = sorted(key for key in en if key not in ru)
    assert not orphan, "в en есть ключи без русского оригинала: " + "; ".join(orphan[:10])


def test_i18n_placeholders_match():
    """Плейсхолдеры {v0} в ru и en совпадают: иначе .format() теряет текст."""
    import gd_i18n

    broken = []
    for key, ru_text in gd_i18n.STRINGS["ru"].items():
        en_text = gd_i18n.STRINGS["en"].get(key)
        if en_text is None:
            continue
        if set(re.findall(r"\{(\w+)", ru_text)) != set(re.findall(r"\{(\w+)", en_text)):
            broken.append(key)
    assert not broken, "плейсхолдеры ru/en расходятся: " + "; ".join(broken[:10])


def test_specs_autodiscover_modules():
    """Spec собирает gd_*.py сам: ручной список устаревал молча (ловится только в exe)."""
    for name in ("Gammadrilon.spec", "Gammadrilon-onefile.spec"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert 'glob("gd_*.py")' in text, (
            f"{name}: список модулей больше не собирается из gd_*.py"
        )
        assert '"gd_proc"' not in text, f"{name}: вернулся ручной список модулей"


def test_spec_head_yields_every_module():
    """Шапка spec выполняется как обычный Python (PyInstaller делает то же самое).

    Проверяем то, что увидит сборщик: все gd_*.py на месте и сам spec не сломан.
    Без этой проверки ошибка в шапке спеки всплывает только на релизе.
    """
    expected = {path.stem for path in GD_MODULES} | {"psutil"}
    for name in ("Gammadrilon.spec", "Gammadrilon-onefile.spec"):
        head = (ROOT / name).read_text(encoding="utf-8").split("a = Analysis")[0]
        namespace: dict = {"SPECPATH": str(ROOT)}
        exec(compile(head, name, "exec"), namespace)   # noqa: S102 - это и есть контракт spec
        assert set(namespace["MODULES"]) == expected, f"{name}: список модулей разошёлся с gd_*.py"


def test_build_script_packs_zip_with_tar():
    """README называет Compress-Archive нерабочим — сборка обязана делать так же."""
    text = (ROOT / "build_release.ps1").read_text(encoding="utf-8", errors="replace")
    assert "tar.exe" in text, "build_release.ps1 больше не пакует zip через tar.exe"
    # упоминание в комментарии — это объяснение, а вызов командлета — регрессия
    assert not re.search(r"^\s*Compress-Archive", text, re.M), (
        "build_release.ps1 снова пакует zip через Compress-Archive"
    )


def test_requirements_are_pinned():
    """Без версий сборка невоспроизводима: у пользователя и в CI разные пакеты."""
    for name in ("requirements.txt", "requirements-dev.txt"):
        for raw in (ROOT / name).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-r"):
                continue
            assert "==" in line, f"{name}: «{line}» без версии"


def test_readme_documents_checks():
    """Проверки должны быть описаны: иначе о них знает только тот, кто их написал."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "smoke_check.py" in readme, "в README нет команды смоук-проверки"
    assert "pytest" in readme, "в README нет команды тестов"
    assert "ruff" in readme, "в README нет команды линтера"


def test_settings_keys_are_known_to_import():
    """Каждый ключ settings из кода должен быть в схеме импорта настроек.

    Схема — это ``DEFAULT_CONFIG["settings"]`` плюс ``RUNTIME_SETTINGS`` (ключи,
    которые приложение дописывает уже по ходу работы). Ключ вне схемы при
    переносе на другую машину молча пропал бы — так чуть не потерялся язык
    интерфейса: он живёт вне умолчаний.
    """
    import main as gd_main

    patterns = (
        r'settings", \{\}\)\.get\("(\w+)"',
        r'settings"\]\.get\("(\w+)"',
        r'settings"\]\["(\w+)"\]',
        r'setdefault\("settings", \{\}\)\["(\w+)"\]',
        r'settings", \{\}\)\["(\w+)"\]',
        r'settings"\]\.setdefault\("(\w+)"',
    )
    used: set[str] = set()
    for path in [MAIN, *GD_MODULES]:
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            used.update(re.findall(pattern, text))

    known = (set(gd_main.DEFAULT_CONFIG["settings"])
             | set(gd_main.RUNTIME_SETTINGS)
             | set(gd_main.SETTINGS_NOT_TRANSFERABLE))
    unknown = sorted(key for key in used if key not in known)
    assert not unknown, "ключи settings вне схемы (перенос их потеряет): " + ", ".join(unknown)

    overlap = sorted(set(gd_main.DEFAULT_CONFIG["settings"]) & set(gd_main.RUNTIME_SETTINGS))
    assert not overlap, "ключ в двух схемах сразу: " + ", ".join(overlap)

    mixed = sorted(set(gd_main.SETTINGS_NOT_TRANSFERABLE)
                   & (set(gd_main.DEFAULT_CONFIG["settings"]) | set(gd_main.RUNTIME_SETTINGS)))
    assert not mixed, ("ключ и переносимый, и запрещённый к переносу сразу: "
                       + ", ".join(mixed))


def test_second_instance_warning_reaches_user(monkeypatch):
    """Второй экземпляр не должен выходить молча.

    Qt в этот момент ещё не поднят, поэтому предупреждение идёт нативным
    MessageBox. Подменяем его заглушкой: проверяем, что вызов вообще есть,
    текст переведён и окно просят показать поверх других.
    """
    import ctypes
    import types

    import main as gd_main

    calls: list[tuple] = []

    def _message_box(hwnd, text, title, flags):
        calls.append((text, title, flags))
        return 1

    _message_box.argtypes = None
    _message_box.restype = None
    monkeypatch.setattr(
        ctypes, "windll", types.SimpleNamespace(user32=types.SimpleNamespace(MessageBoxW=_message_box))
    )

    gd_main._warn_already_running()

    assert calls, "предупреждение «уже запущено» не показано"
    text, title, flags = calls[0]
    assert "Gammadrilon" in text, text
    assert gd_main.APP_VERSION in title, title
    assert flags & 0x40, "окно без иконки: сообщение выглядит как ошибка"
    assert flags & 0x10000, "окно не просят показать поверх остальных"
