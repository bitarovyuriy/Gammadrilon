# -*- coding: utf-8 -*-
"""Сгенерировать метаданные версии для PyInstaller из APP_VERSION.

Версия живёт в одном месте — `APP_VERSION` в `main.py`. PyInstaller ждёт файл
`VSVersionInfo(...)`, поэтому его делает этот скрипт перед сборкой: не держать
номер второй раз в spec или в PowerShell-строке.

Запуск: python tools/make_version_info.py [куда]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "build" / "version_info.txt"

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Yuri Bitarov'),
        StringStruct('FileDescription', 'Gammadrilon'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'Gammadrilon'),
        StringStruct('OriginalFilename', 'Gammadrilon.exe'),
        StringStruct('ProductName', 'Gammadrilon'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def app_version(source: Path | None = None) -> str:
    """Версия из main.py — строка вида 3.5 или v3.5."""
    text = (source or ROOT / "main.py").read_text(encoding="utf-8", errors="replace")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("в main.py не найден APP_VERSION")
    return match.group(1)


def numbers(version: str) -> tuple[int, int, int]:
    """3.5 → (3, 5, 0): PyInstaller ждёт четыре числа, добиваем нулями."""
    parts = [item for item in version.lstrip("v").split(".") if item.isdigit()]
    values = [int(item) for item in parts[:3]]
    while len(values) < 3:
        values.append(0)
    return values[0], values[1], values[2]


def render(version: str) -> str:
    major, minor, patch = numbers(version)
    return TEMPLATE.format(major=major, minor=minor, patch=patch, version=version.lstrip("v"))


def main(argv: list[str]) -> int:
    output = Path(argv[0]) if argv else DEFAULT_OUTPUT
    version = app_version()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(version), encoding="utf-8")
    print(f"метаданные версии: {version} -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
