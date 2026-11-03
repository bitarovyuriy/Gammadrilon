# Синхронизация сборочной копии C:\gd_build с репозиторием.
#
# Зачем отдельный скрипт: PyInstaller не умеет собирать проект из пути с
# кириллицей (Qt-плагины не находятся), поэтому сборка идёт из ASCII-копии.
# /MIR зеркалит копию: файлы, удалённые из репозитория, исчезают и здесь — иначе
# в сборку тихо попадают вырезанные модули (так было с gd_agent.py).
#
# .venv, dist, build и кеши исключены: их нельзя ни копировать, ни удалять —
# в C:\gd_build свой venv с PyInstaller, а dist/build пересобираются на месте.

$ErrorActionPreference = "Stop"
$source = $PSScriptRoot
$target = "C:\gd_build"

if ($source -eq $target) {
    throw "Скрипт запускается из репозитория, а не из сборочной копии"
}
if (-not (Test-Path $target)) {
    New-Item -ItemType Directory -Path $target | Out-Null
}

robocopy $source $target /MIR `
    /XD .venv .git dist build __pycache__ .pytest_cache .ruff_cache `
    /XF presets.json /NFL /NDL /NJH /NJS /NP

# Коды robocopy: 0–7 — успех (1 = файлы скопированы, 2 = лишние удалены и т.п.).
if ($LASTEXITCODE -ge 8) {
    throw "robocopy завершился с кодом $LASTEXITCODE"
}

$missing = @("gd_paths.py", "main.py", "Gammadrilon.spec") | Where-Object {
    -not (Test-Path (Join-Path $target $_))
}
if ($missing) {
    throw "В сборочной копии нет обязательных файлов: $($missing -join ', ')"
}

Write-Host "Сборочная копия синхронизирована: $target" -ForegroundColor Green
