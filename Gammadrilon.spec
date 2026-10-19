# -*- mode: python ; coding: utf-8 -*-
"""Портативная сборка (onedir): dist/Gammadrilon/Gammadrilon.exe.

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
    datas=[('icon.ico', '.')],
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
