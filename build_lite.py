"""Make a one-process Windows folder build and a self-contained ZIP."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from main import APP_VERSION

ROOT = Path(__file__).resolve().parent
NAME = 'GammadrilonLite'
DIST = ROOT / 'dist' / 'monochrome'


def main() -> None:
    if sys.platform != 'win32':
        raise SystemExit('Build requires Windows.')
    csc = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    vendor = ROOT / 'vendor/lhm'
    helper = ROOT / 'build/sensor-run/LiteSensors.exe'
    helper.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([csc, '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
                    f'/reference:{vendor / "LibreHardwareMonitorLib.dll"}', f'/out:{helper}',
                    ROOT / 'sensors/LiteSensors.cs'], check=True)
    assets = []
    for name in ('LibreHardwareMonitorLib.dll', 'System.Memory.dll', 'System.Runtime.CompilerServices.Unsafe.dll', 'LICENSE'):
        shutil.copy2(vendor / name, helper.parent / name)
        assets += ['--add-data', f'{vendor / name}:vendor/lhm']
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
                    '--name', NAME, '--icon', str(ROOT / 'icon-lite.ico'), '--add-data', f'{ROOT / "icon-lite.ico"}:.',
                    '--exclude-module', 'PyQt5', '--exclude-module', 'psutil',
                    '--add-data', f'{helper}:vendor/lhm', *assets,
                    '--specpath', 'build', '--workpath', 'build/work', '--distpath', str(DIST), 'main.py'],
                   cwd=ROOT, check=True)
    dist = DIST
    bundle = dist / NAME
    subprocess.run([bundle / f'{NAME}.exe', '--smoke'], cwd=bundle, timeout=30, check=True)
    subprocess.run([bundle / f'{NAME}.exe', '--diagnose'], cwd=bundle, timeout=45, check=True)
    for document in ('README.md', 'LICENSE'):
        shutil.copy2(ROOT / document, bundle / document)
    shutil.copy2(vendor / 'README.md', bundle / '_internal/vendor/lhm/README.md')
    archive_base = dist / f'Gammadrilon-Lite-v{APP_VERSION}-portable'
    archive = Path(shutil.make_archive(str(archive_base), 'zip', root_dir=dist, base_dir=NAME))
    with archive.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    (dist / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='ascii', newline='\n')
    print(f'{archive}: {archive.stat().st_size:,} bytes / SHA-256 {digest}')


if __name__ == '__main__':
    main()
