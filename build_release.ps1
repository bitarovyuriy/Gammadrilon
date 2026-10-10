# Сборка Gammadrilon: портативная (onedir + zip) и релизная (onefile exe).
$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
Set-Location $projectRoot

$py = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "Не найден venv: $py"
}

Write-Host "== Портативная сборка (onedir) ==" -ForegroundColor Cyan
& $py -m PyInstaller --clean --noconfirm Gammadrilon.spec
if ($LASTEXITCODE -ne 0) { throw "Сборка onedir завершилась с ошибкой" }

Write-Host "== Релизная сборка (onefile) ==" -ForegroundColor Cyan
& $py -m PyInstaller --clean --noconfirm Gammadrilon-onefile.spec
if ($LASTEXITCODE -ne 0) { throw "Сборка onefile завершилась с ошибкой" }

$portableDir = Join-Path $projectRoot "dist\Gammadrilon"
foreach ($extra in @("icon.ico", "README.md", "LICENSE")) {
    Copy-Item (Join-Path $projectRoot $extra) (Join-Path $portableDir $extra) -Force
}

$zip = Join-Path $projectRoot "dist\Gammadrilon-portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $portableDir -DestinationPath $zip -CompressionLevel Optimal

$exe = Join-Path $projectRoot "dist\Gammadrilon.exe"
$portableExe = Join-Path $portableDir "Gammadrilon.exe"
if (-not (Test-Path $exe)) { throw "Не найден релизный exe: $exe" }
if (-not (Test-Path $portableExe)) { throw "Не найден портативный exe: $portableExe" }

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
$zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)

Write-Host ""
Write-Host "Готово:" -ForegroundColor Green
Write-Host "  портативная папка: $portableDir"
Write-Host "  zip:               $zip ($zipMb МБ)"
Write-Host "  релизный exe:      $exe ($sizeMb МБ)"
