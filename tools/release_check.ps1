# Проверка установщика целиком: тихая установка -> самопроверка -> удаление.
#
# Зачем: «собралось» и «устанавливается» — разные вещи. Скрипт проходит путь
# пользователя на чистой машине: ставит без окон, запускает --selftest
# собранного exe, проверяет код возврата и удаляет за собой.
#
# Запуск: powershell -NoProfile -ExecutionPolicy Bypass -File tools\release_check.ps1
# Коды возврата: 0 — всё прошло, 1 — провал на конкретном шаге (он в выводе).

param(
    [string]$SetupPath = "",
    [switch]$KeepInstalled
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Fail([string]$message) {
    Write-Host "ПРОВАЛ: $message" -ForegroundColor Red
    exit 1
}

function Step([string]$text) {
    Write-Host ""
    Write-Host "== $text ==" -ForegroundColor Cyan
}

Step "Поиск установщика"
if (-not $SetupPath) {
    $candidates = Get-ChildItem -Path (Join-Path $root "dist") -Filter "Gammadrilon-Setup-*.exe" |
        Sort-Object LastWriteTime -Descending
    if (-not $candidates) { Fail "в dist нет Gammadrilon-Setup-*.exe — сначала соберите релиз" }
    $SetupPath = $candidates[0].FullName
}
if (-not (Test-Path $SetupPath)) { Fail "установщик не найден: $SetupPath" }
$sizeMb = [math]::Round((Get-Item $SetupPath).Length / 1MB, 1)
Write-Host "установщик: $SetupPath ($sizeMb МБ)"

Step "Тихая установка"
$installDir = Join-Path $env:LOCALAPPDATA "Programs\Gammadrilon"
if (Test-Path $installDir) { Write-Host "папка уже есть — установка поверх: $installDir" }
$p = Start-Process -FilePath $SetupPath -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/CURRENTUSER" -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "установщик вернул код $($p.ExitCode)" }

$exe = Join-Path $installDir "Gammadrilon.exe"
if (-not (Test-Path $exe)) { Fail "после установки нет $exe" }
Write-Host "установлен: $exe"

Step "Самопроверка установленного exe"
& $exe --selftest | ForEach-Object { Write-Host "   $_" }
$code = $LASTEXITCODE
if ($code -ne 0) { Fail "самопроверка установленного приложения вернула код $code" }

Step "Проверка ярлыков и метаданных"
$unins = Join-Path $installDir "unins000.exe"
if (-not (Test-Path $unins)) { Fail "нет unins000.exe — установка неполная" }
$version = (Get-Item $exe).VersionInfo.ProductVersion
Write-Host "версия файла: $version"
if ([string]::IsNullOrWhiteSpace($version)) {
    throw "у exe пустые метаданные версии - сборка без version_info.txt"
}

if ($KeepInstalled) {
    Write-Host ""
    Write-Host "Оставлено установленным по -KeepInstalled." -ForegroundColor Yellow
    exit 0
}

Step "Удаление"
$p = Start-Process -FilePath $unins -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART" -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "деинсталлятор вернул код $($p.ExitCode)" }

if (Test-Path (Join-Path $installDir "Gammadrilon.exe")) {
    Fail "после удаления exe остался на месте"
}

Write-Host ""
Write-Host "Всё прошло: установка, самопроверка, удаление." -ForegroundColor Green
exit 0
