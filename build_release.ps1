# Сборка Gammadrilon: портативная (onedir + zip) и релизная (onefile exe).
$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
Set-Location $projectRoot

$py = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "Не найден venv: $py"
}

# Версия берётся из единственного источника — APP_VERSION в main.py.
$mainSource = Get-Content (Join-Path $projectRoot "main.py") -Raw -Encoding UTF8
if ($mainSource -notmatch 'APP_VERSION\s*=\s*"([^"]+)"') {
    throw "В main.py не найден APP_VERSION"
}
$appVersion = $Matches[1]
Write-Host "Версия: $appVersion" -ForegroundColor Cyan

# Хелпер датчиков: C# + LibreHardwareMonitor. Собирается тут же, чтобы exe в
# vendor/lhm не мог разойтись с исходником.
Write-Host "== Хелпер датчиков (C#) ==" -ForegroundColor Cyan
# csc ищем, а не прописываем путём: номер версии .NET в тексте сборки
# ломает проверку «версия живёт только в APP_VERSION» (tests/test_meta.py).
$csc = (Get-ChildItem (Join-Path $env:WINDIR "Microsoft.NET/Framework64") -Filter csc.exe -Recurse -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1).FullName
$sensorSrc = Join-Path $projectRoot "sensors\GammadrilonSensors.cs"
$sensorOut = Join-Path $projectRoot "vendor/lhm\GammadrilonSensors.exe"
$sensorLib = Join-Path $projectRoot "vendor/lhm\LibreHardwareMonitorLib.dll"
if (-not (Test-Path $csc)) { throw "Не найден csc.exe (нужен .NET Framework 4.x)" }
if (-not (Test-Path $sensorSrc)) { throw "Не найден $sensorSrc" }
& $csc /nologo /target:exe /platform:x64 "/out:$sensorOut" "/r:$sensorLib" $sensorSrc
if ($LASTEXITCODE -ne 0) { throw "Хелпер датчиков не собрался" }

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

# Пакуем tar.exe (bsdtar, есть в Windows 10 1803+), а не Compress-Archive:
# тот падает, если антивирус или индексатор на секунду придержал свежескопированный
# файл (см. README, раздел «Сборка»).
$tar = Join-Path $env:SystemRoot "System32\tar.exe"
if (-not (Test-Path $tar)) { throw "Не найден tar.exe: $tar (нужен Windows 10 1803+)" }

& $tar -a -c -f $zip -C (Join-Path $projectRoot "dist") "Gammadrilon"
if ($LASTEXITCODE -ne 0) { throw "Упаковка zip не удалась (tar exit $LASTEXITCODE)" }

# Пустой или недособранный zip на релизе выглядел бы как «скачал и не работает».
if (-not ((& $tar -tf $zip) -match 'Gammadrilon/Gammadrilon\.exe$')) {
    throw "В zip нет Gammadrilon/Gammadrilon.exe - архив битый"
}

$exe = Join-Path $projectRoot "dist\Gammadrilon.exe"
$portableExe = Join-Path $portableDir "Gammadrilon.exe"
if (-not (Test-Path $exe)) { throw "Не найден релизный exe: $exe" }
if (-not (Test-Path $portableExe)) { throw "Не найден портативный exe: $portableExe" }

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
$zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)

# Хэши — в текст релиза: пользователь может сверить скачанное, не гадая.
$exeHash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
$zipHash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()

# Версионированные копии рядом с каноническими именами: удобно хранить
# несколько релизов и не перепутать скачанный архив.
$versionedExe = Join-Path $projectRoot "dist\Gammadrilon-$appVersion.exe"
$versionedZip = Join-Path $projectRoot "dist\Gammadrilon-$appVersion-portable.zip"
Copy-Item $exe $versionedExe -Force
Copy-Item $zip $versionedZip -Force

Write-Host ""
Write-Host "Готово:" -ForegroundColor Green
Write-Host "  портативная папка: $portableDir"
Write-Host "  zip:               $zip ($zipMb МБ)"
Write-Host "  zip с версией:     $versionedZip"
Write-Host "  релизный exe:      $exe ($sizeMb МБ)"
Write-Host "  exe с версией:     $versionedExe"
Write-Host "  sha256 exe:        $exeHash"
Write-Host "  sha256 zip:        $zipHash"
