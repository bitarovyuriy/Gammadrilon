# -*- mode: python ; coding: utf-8 -*-
"""Портативная сборка (onedir): dist/Gammadrilon/Gammadrilon.exe."""

MODULES = [
    "gd_icons", "gd_i18n", "gd_theme", "gd_toast", "gd_journal", "gd_journal_ui",
    "gd_palette", "gd_tray", "gd_onboarding", "gd_profiles", "gd_sensors",
    "gd_power", "gd_power_ui", "gd_power_service",
    "gd_gpu", "gd_gpu_ui", "gd_gpu_service",
    "gd_rules", "gd_rules_ui", "gd_memory", "gd_utils_ui",
    "gd_dns", "gd_dns_ui", "gd_extra", "gd_extra_ui",
]

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
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name='Gammadrilon',
)
