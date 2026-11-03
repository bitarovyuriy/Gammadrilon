# -*- mode: python ; coding: utf-8 -*-
"""Портативная сборка (onedir): dist/Gammadrilon/Gammadrilon.exe.

Список модулей собирается сам из gd_*.py: раньше он был вписан руками и тихо
устаревал — из исходников всё работало, а в exe падал импорт модуля, который
забыли добавить сюда.
"""

from pathlib import Path

SPEC_DIR = Path(SPECPATH)   # каталог spec-файла = корень проекта
MODULES = sorted(p.stem for p in SPEC_DIR.glob("gd_*.py"))
if len(MODULES) < 20 or "gd_power" not in MODULES:
    raise SystemExit(
        f"gd_*.py не найдены в {SPEC_DIR}: spec должен лежать в корне проекта"
    )
MODULES.append("psutil")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('icon.ico', '.'), ('vendor/lhm', 'vendor/lhm')],
    hiddenimports=MODULES,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'PySide6', 'PyQt6'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# QML и Quick в программе не используются (интерфейс на виджетах), но PyInstaller
# тянет их как зависимости PyQt5 — 7,5 МБ в каждой сборке. Выкидываем по имени:
# список проверяется при сборке, и `build_release.ps1` всё равно гоняет --selftest,
# так что забытая зависимость всплывёт сразу, а не у пользователя.
_UNUSED_QT = ("Qt5Quick.dll", "Qt5Qml.dll", "Qt5QmlModels.dll", "Qt5QuickWidgets.dll")


def _drop_unused_qt(entries):
    kept = []
    for entry in entries:
        name = entry[0].replace("\\", "/")
        if any(name.endswith(item) for item in _UNUSED_QT):
            continue
        if "/qml/" in name:
            continue
        kept.append(entry)
    return kept


a.binaries = _drop_unused_qt(a.binaries)
a.datas = _drop_unused_qt(a.datas)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Gammadrilon',
    icon='icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX выключен: упаковка Qt-DLL — классическая причина ложных срабатываний
    # антивирусов и лишних предупреждений SmartScreen. Экономия размера того не стоит.
    upx=False,
    # метаданные версии: build_release.ps1 генерирует файл из APP_VERSION
    version=version_file if (version_file := os.path.join('build', 'version_info.txt')) and os.path.isfile(version_file) else None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    # UPX выключен: упаковка Qt-DLL — классическая причина ложных срабатываний
    # антивирусов и лишних предупреждений SmartScreen. Экономия размера того не стоит.
    upx=False,
    upx_exclude=[],
    name='Gammadrilon',
)
