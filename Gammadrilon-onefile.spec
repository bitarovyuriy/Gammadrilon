# -*- mode: python ; coding: utf-8 -*-
"""Релизная сборка (onefile): dist/Gammadrilon.exe — единый файл для GitHub Releases.

Список модулей собирается сам из gd_*.py: раньше он был вписан руками и тихо
устаревал — из исходников всё работало, а в exe падал импорт модуля, который
забыли добавить сюда.
"""

from pathlib import Path

SPEC_DIR = Path(SPECPATH)   # каталог spec-файла = корень проекта
MODULES = sorted(p.stem for p in SPEC_DIR.glob("gd_*.py"))
if len(MODULES) < 20 or "gd_agent" not in MODULES:
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Gammadrilon',
    icon='icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX выключен: упаковка Qt-DLL — классическая причина ложных срабатываний
    # антивирусов и лишних предупреждений SmartScreen. Экономия размера того не стоит.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
