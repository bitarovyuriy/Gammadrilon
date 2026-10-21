# -*- coding: utf-8 -*-
"""Автообновление портативной сборки: скачать, проверить, заменить, откатить.

Идея простая и намеренно скучная. Настройки (пресеты, горячие клавиши, тема)
лежат в ``presets.json`` **рядом с exe**. Обновление не трогает папку целиком:
оно заменяет только ``Gammadrilon.exe`` и ``_internal`` (или один exe в
onefile-сборке). Поэтому настройки, журнал, правила и state-файлы остаются на
месте — даже если что-то пойдёт не так.

Запущенный exe нельзя заменить на Windows. Поэтому приложение скачивает и
распаковывает обновление во временную папку, пишет маленький PowerShell-helper
и выходит. Helper ждёт смерти процесса, делает резервные копии, подменяет
файлы, запускает новую версию и откатывается, если она упала на старте.

Модуль не знает про Qt: его можно звать из фонового потока и тестировать без
окна.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

import gd_i18n as i18n

APP_EXE = "Gammadrilon.exe"
CONFIG_NAME = "presets.json"
CONFIG_BACKUP = "presets.before-update.json"
INTERNAL = "_internal"
BACKUP_INTERNAL = "_internal.old"
BACKUP_SUFFIX = ".old"
LOG_NAME = "Gammadrilon-update.log"

#: сколько ждать выхода приложения перед заменой файлов
WAIT_SECONDS = 90


class UpdateError(Exception):
    """Ошибка, текст которой можно показать человеку как есть."""


# --- окружение -------------------------------------------------------------

def app_root() -> Path:
    """Папка приложения: рядом с exe в сборке, папка проекта из исходников."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def install_mode(root: Path | None = None) -> str:
    """``onedir`` / ``onefile`` / ``dev`` — от этого зависит способ замены."""
    frozen = bool(getattr(sys, "frozen", False))
    if not frozen and root is None:
        return "dev"
    base = Path(root) if root is not None else app_root()
    return "onedir" if (base / INTERNAL).is_dir() else "onefile"


def exe_name() -> str:
    """Имя запущенного exe: пользователь мог переименовать файл."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).name
    return APP_EXE


# --- выбор файла релиза ----------------------------------------------------

def asset_for(mode: str, assets) -> dict | None:
    """Выбрать из ассетов релиза тот, которым обновляется эта сборка.

    Стабильные имена (``Gammadrilon-portable.zip`` / ``Gammadrilon.exe``)
    важнее версионированных: их проще прикреплять к каждому релизу.
    """
    if not isinstance(assets, list):
        return None

    clean = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if not name or not url.lower().startswith("https://"):
            continue
        clean.append(dict(item, name=name, url=url))

    if mode == "onefile":
        ordered = ("gammadrilon.exe",)
        candidates = [a for a in clean if a["name"].lower().endswith(".exe")
                      and a["name"].lower().startswith("gammadrilon")]
    elif mode == "onedir":
        ordered = ("gammadrilon-portable.zip", "gammadrilon.zip")
        candidates = [a for a in clean if a["name"].lower().endswith(".zip")]
    else:
        return None

    for wanted in ordered:
        for item in candidates:
            if item["name"].lower() == wanted:
                return item
    # версионированный вариант: Gammadrilon-v3.4-portable.zip
    for item in candidates:
        lower = item["name"].lower()
        if mode == "onedir" and lower.endswith("-portable.zip"):
            return item
    return candidates[0] if candidates else None


def digest_hex(asset) -> str:
    """``sha256:...`` из GitHub API → строка hex. Пусто — проверять нечем."""
    raw = str((asset or {}).get("digest") or "").strip().lower()
    if raw.startswith("sha256:"):
        raw = raw[7:].strip()
    if len(raw) == 64 and all(ch in "0123456789abcdef" for ch in raw):
        return raw
    return ""


# --- скачивание и проверка -------------------------------------------------

def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_host(url: str) -> None:
    """Не ходить за обновлением на произвольный хост из ответа API."""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host not in ("github.com", "objects.githubusercontent.com",
                    "release-assets.githubusercontent.com",
                    "github-releases.githubusercontent.com"):
        raise UpdateError(i18n.t("Неожиданный адрес обновления: {v0}", v0=host or url))


def download_asset(asset: dict, destination: Path, timeout: float = 30.0) -> Path:
    """Скачать ассет GitHub в файл. Бросает UpdateError с понятным текстом."""
    url = str(asset.get("url") or "")
    if not url.lower().startswith("https://"):
        raise UpdateError(i18n.t("В релизе нет ссылки на файл обновления."))
    _check_host(url)

    request = urllib.request.Request(url, headers={
        "User-Agent": "Gammadrilon-auto-update",
        "Accept": "application/octet-stream",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            _check_host(response.geturl())
            expected = int(response.headers.get("Content-Length") or 0)
            received = 0
            with open(destination, "wb") as fh:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    received += len(chunk)
    except urllib.error.HTTPError as exc:
        raise UpdateError(i18n.t("GitHub ответил {v0}", v0=exc.code)) from exc
    except urllib.error.URLError as exc:
        raise UpdateError(i18n.t("нет связи: {v0}", v0=exc.reason)) from exc
    except OSError as exc:
        raise UpdateError(i18n.t("не удалось скачать файл: {v0}", v0=exc)) from exc

    if expected and received != expected:
        raise UpdateError(i18n.t("Файл скачался не целиком: {v0} из {v1} байт.",
                                 v0=received, v1=expected))
    size = int(asset.get("size") or 0)
    if size and destination.stat().st_size != size:
        raise UpdateError(i18n.t("Размер файла не совпал с релизом."))
    return destination


def verify_digest(path, expected: str) -> None:
    if not expected:
        raise UpdateError(i18n.t(
            "В релизе нет контрольной суммы sha256 — автообновление отключено."))
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        raise UpdateError(i18n.t(
            "Контрольная сумма не совпала: файл повреждён или подменён."))


# --- распаковка ------------------------------------------------------------

def _members(zf: zipfile.ZipFile):
    """Проверить имена файлов zip: без абсолютных путей и ``..``."""
    checked = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        raw = info.filename.replace("\\", "/").strip()
        if not raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
            raise UpdateError(i18n.t("В архиве недопустимый путь: {v0}", v0=info.filename))
        parts = PurePosixPath(raw).parts
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise UpdateError(i18n.t("В архиве недопустимый путь: {v0}", v0=info.filename))
        checked.append((info, parts))
    if not checked:
        raise UpdateError(i18n.t("Архив обновления пуст."))
    return checked


def extract_onedir(zip_path, destination: Path) -> Path:
    """Распаковать портативный zip, проверив структуру и защиту от zip-slip."""
    destination = Path(destination)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = _members(zf)
            roots = {parts[0].lower() for _info, parts in members}
            if roots != {"gammadrilon"}:
                raise UpdateError(i18n.t(
                    "В архиве ожидалась папка Gammadrilon, а найдено: {v0}.",
                    v0=", ".join(sorted(roots))[:80]))
            files = {"/".join(parts).lower() for _info, parts in members}
            if "gammadrilon/gammadrilon.exe" not in files:
                raise UpdateError(i18n.t("В архиве нет Gammadrilon.exe."))
            if not any(name.startswith("gammadrilon/_internal/") for name in files):
                raise UpdateError(i18n.t("В архиве нет папки _internal."))
            destination.mkdir(parents=True, exist_ok=True)
            zf.extractall(destination, [info for info, _parts in members])
    except zipfile.BadZipFile as exc:
        raise UpdateError(i18n.t("Архив обновления повреждён.")) from exc

    root = destination / "Gammadrilon"
    if not (root / "Gammadrilon.exe").is_file():
        raise UpdateError(i18n.t("После распаковки не найден Gammadrilon.exe."))
    return root


def _check_pe(path: Path) -> None:
    try:
        with open(path, "rb") as fh:
            if fh.read(2) != b"MZ":
                raise UpdateError(i18n.t("Скачанный файл не похож на exe."))
    except OSError as exc:
        raise UpdateError(i18n.t("не удалось прочитать файл: {v0}", v0=exc)) from exc


# --- подготовка ------------------------------------------------------------

def _free_space(path: Path) -> int:
    try:
        return int(shutil.disk_usage(path).free)
    except OSError:
        return 0


def _copy_settings_backup(root: Path) -> Path | None:
    """Копия presets.json до обновления — на случай совсем странного отказа."""
    source = root / CONFIG_NAME
    if not source.is_file():
        return None
    backup = root / CONFIG_BACKUP
    try:
        shutil.copy2(source, backup)
    except OSError as exc:
        raise UpdateError(i18n.t("Не удалось сохранить копию настроек: {v0}", v0=exc)) from exc
    return backup


def prepare_update(info: dict, mode: str | None = None, *,
                   root: Path | None = None, work_root: Path | None = None,
                   download=download_asset) -> dict:
    """Скачать и подготовить обновление. Возврат — dict для GUI.

    Всё, что может упасть (сеть, диск, хэш, структура архива), превращается в
    ``{"ok": False, "error": "текст"}``: вызывающий не обязан ловить исключения.
    """
    try:
        if not isinstance(info, dict) or not info.get("ok"):
            raise UpdateError(i18n.t("Нет данных о новом релизе."))
        mode = mode or install_mode()
        if mode not in ("onedir", "onefile"):
            raise UpdateError(i18n.t("Автообновление работает только в собранной версии."))

        base = Path(root) if root is not None else app_root()
        asset = asset_for(mode, info.get("assets"))
        if asset is None:
            raise UpdateError(i18n.t(
                "В релизе нет файла для автообновления — скачайте вручную."))
        expected = digest_hex(asset)
        if not expected:
            raise UpdateError(i18n.t(
                "В релизе нет контрольной суммы sha256 — автообновление отключено."))

        work = Path(work_root) if work_root is not None else Path(
            tempfile.mkdtemp(prefix="Gammadrilon-update-"))
        work.mkdir(parents=True, exist_ok=True)

        asset_size = int(asset.get("size") or 0)
        free = _free_space(work)
        if free and asset_size and free < asset_size * 3 + 50 * 1024 * 1024:
            raise UpdateError(i18n.t(
                "Мало места на диске: нужно примерно {v0} МБ, свободно {v1} МБ.",
                v0=(asset_size * 3) // (1024 * 1024) + 50,
                v1=free // (1024 * 1024)))

        filename = str(asset.get("name") or "update.bin")
        package = work / filename
        download(asset, package)
        verify_digest(package, expected)

        staging = work / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        if mode == "onedir":
            source_dir = extract_onedir(package, staging)
            source_exe = ""
        else:
            _check_pe(package)
            source_dir = ""
            source_exe = staging / APP_EXE
            shutil.copy2(package, source_exe)

        backup = _copy_settings_backup(base)

        plan = {
            "ok": True,
            "version": str(info.get("tag") or ""),
            "mode": mode,
            "target_dir": str(base),
            "source_dir": str(source_dir) if source_dir else "",
            "source_exe": str(source_exe) if source_exe else "",
            "exe_name": exe_name(),
            "app_pid": os.getpid(),
            "log_path": str(Path(tempfile.gettempdir()) / LOG_NAME),
            "work_dir": str(work),
            "restart": True,
            "wait_seconds": WAIT_SECONDS,
            "settings_backup": str(backup) if backup else "",
        }
        plan["script_path"] = str(_write_helper(work))
        plan["config_path"] = str(_write_config(work, plan))
        return {"ok": True, "plan": plan, "version": plan["version"]}
    except UpdateError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — сеть/диск умеют удивлять
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _write_config(work: Path, plan: dict) -> Path:
    config = work / "update.json"
    config.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def _write_helper(work: Path) -> Path:
    script = work / "apply_update.ps1"
    script.write_text(UPDATE_SCRIPT, encoding="utf-8")
    return script


# --- запуск helper ---------------------------------------------------------

def helper_command(script_path, config_path) -> list:
    """Команда PowerShell. Отдельная функция — её проще проверить тестом."""
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden",
        "-File", str(script_path),
        "-Config", str(config_path),
    ]


def launch_helper(plan: dict) -> None:
    """Запустить helper и убедиться, что он не упал сразу."""
    script = Path(plan["script_path"])
    config = Path(plan["config_path"])
    if not script.is_file() or not config.is_file():
        raise UpdateError(i18n.t("Файлы обновления не найдены."))

    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            helper_command(script, config),
            cwd=str(Path(plan["work_dir"])),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
    except OSError as exc:
        raise UpdateError(i18n.t("Не удалось запустить обновление: {v0}", v0=exc)) from exc

    # PowerShell с запрещённой политикой падает сразу. Даём ему полсекунды и
    # проверяем: если процесс жив, он уже ждёт выхода приложения.
    import time
    time.sleep(0.6)
    if proc.poll() is not None:
        raise UpdateError(i18n.t(
            "Обновление не запустилось (код {v0}). Скачайте архив вручную.",
            v0=proc.returncode))


# --- PowerShell helper -----------------------------------------------------
# Скрипт намеренно на ASCII: его читает Windows PowerShell 5.1, а пути с
# кириллицей приходят из update.json в UTF-8.

UPDATE_SCRIPT = r'''param([string]$Config)
$ErrorActionPreference = "Stop"
$cfg = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json

function Write-Log([string]$Message) {
    try {
        $line = "{0} {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $Message
        Add-Content -LiteralPath $cfg.log_path -Value $line -Encoding UTF8
    } catch { }
}

function Remove-Any([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    } else {
        Remove-Item -LiteralPath $Path -Force
    }
}

function Move-WithRetry([string]$Source, [string]$Destination) {
    $last = $null
    for ($i = 0; $i -lt 24; $i++) {
        try {
            Move-Item -LiteralPath $Source -Destination $Destination -Force
            return
        } catch {
            $last = $_
            Start-Sleep -Milliseconds 500
        }
    }
    throw $last
}

function Copy-WithRetry([string]$Source, [string]$Destination) {
    $last = $null
    for ($i = 0; $i -lt 24; $i++) {
        try {
            Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
            return
        } catch {
            $last = $_
            Start-Sleep -Milliseconds 500
        }
    }
    throw $last
}

$target = [string]$cfg.target_dir
$exePath = Join-Path $target ([string]$cfg.exe_name)
$internal = Join-Path $target "_internal"
$backupInternal = Join-Path $target "_internal.old"
$backupExe = $exePath + ".old"
$presets = Join-Path $target "presets.json"
$presetsBackup = Join-Path $target "presets.before-update.json"
$sourceDir = [string]$cfg.source_dir
$sourceExe = [string]$cfg.source_exe
$workDir = [string]$cfg.work_dir

Write-Log ("start mode={0} target={1}" -f $cfg.mode, $target)

$proc = $null
if ([int]$cfg.app_pid -gt 0) {
    try {
        $proc = Get-Process -Id ([int]$cfg.app_pid) -ErrorAction SilentlyContinue
    } catch { }
    if ($proc) {
        $null = $proc.WaitForExit([int]$cfg.wait_seconds * 1000)
        if (-not $proc.HasExited) {
            Write-Log "app did not exit; abort"
            exit 3
        }
    }
}
Start-Sleep -Milliseconds 900

# Компаньоны питания/GPU запускаются тем же exe. Обычно они уже вышли; если
# нет — не даём им держать старые файлы.
try {
    $baseName = [IO.Path]::GetFileNameWithoutExtension([string]$cfg.exe_name)
    $left = Get-CimInstance Win32_Process -Filter ("Name='" + $baseName + ".exe'") -ErrorAction SilentlyContinue
    foreach ($p in $left) {
        if ($p.ExecutablePath -and ($p.ExecutablePath -ieq $exePath) -and ([int]$p.ProcessId -ne [int]$cfg.app_pid)) {
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
                Write-Log ("stopped leftover {0}" -f $p.ProcessId)
            } catch { }
        }
    }
} catch { }

# Копия настроек: основной файл остаётся на месте, это страховка.
try {
    if (Test-Path -LiteralPath $presets) {
        Copy-Item -LiteralPath $presets -Destination $presetsBackup -Force
    }
} catch { }

$swapped = $false
try {
    if ($cfg.mode -eq "onedir") {
        $srcExe = Join-Path $sourceDir "Gammadrilon.exe"
        $srcInternal = Join-Path $sourceDir "_internal"
        if (-not (Test-Path -LiteralPath $srcExe)) { throw "new exe missing in staging" }
        if (-not (Test-Path -LiteralPath $srcInternal)) { throw "new _internal missing in staging" }

        Remove-Any $backupInternal
        Remove-Any $backupExe
        if (Test-Path -LiteralPath $internal) { Move-WithRetry $internal $backupInternal }
        if (Test-Path -LiteralPath $exePath) { Move-WithRetry $exePath $backupExe }
        Copy-WithRetry $srcInternal $internal
        Copy-WithRetry $srcExe $exePath
    } elseif ($cfg.mode -eq "onefile") {
        if (-not (Test-Path -LiteralPath $sourceExe)) { throw "new exe missing in staging" }
        Remove-Any $backupExe
        if (Test-Path -LiteralPath $exePath) { Move-WithRetry $exePath $backupExe }
        Copy-WithRetry $sourceExe $exePath
    } else {
        throw "unknown mode"
    }
    $swapped = $true
    Write-Log "files swapped"
} catch {
    Write-Log ("swap failed: {0}" -f $_.Exception.Message)
}

if (-not $swapped) {
    Write-Log "rollback before swap"
    try {
        if (Test-Path -LiteralPath $backupInternal) {
            Remove-Any $internal
            Move-WithRetry $backupInternal $internal
        }
        if (Test-Path -LiteralPath $backupExe) {
            Remove-Any $exePath
            Move-WithRetry $backupExe $exePath
        }
        Write-Log "rollback done"
    } catch {
        Write-Log ("rollback failed: {0}" -f $_.Exception.Message)
    }
    exit 2
}

if (-not (Test-Path -LiteralPath $presets) -and (Test-Path -LiteralPath $presetsBackup)) {
    try { Copy-Item -LiteralPath $presetsBackup -Destination $presets -Force } catch { }
}

$ok = $false
if ($cfg.restart) {
    try {
        $newProc = Start-Process -FilePath $exePath -WorkingDirectory $target -PassThru
        Start-Sleep -Seconds 6
        if ($newProc.HasExited) {
            throw ("new app exited with code {0}" -f $newProc.ExitCode)
        }
        Write-Log "restart ok"
        $ok = $true
    } catch {
        Write-Log ("restart failed: {0}" -f $_.Exception.Message)
    }
} else {
    Write-Log "restart disabled"
    $ok = $true
}

if ($ok) {
    if ($cfg.restart) {
        try { Remove-Any $backupInternal } catch { }
        try { Remove-Any $backupExe } catch { }
        try { Remove-Any $workDir } catch { }
    }
    exit 0
}

# Новая версия не поднялась — возвращаем старые файлы и запускаем их.
Write-Log "rollback after failed restart"
try {
    Remove-Any $internal
    if (Test-Path -LiteralPath $backupInternal) { Move-WithRetry $backupInternal $internal }
    Remove-Any $exePath
    if (Test-Path -LiteralPath $backupExe) { Move-WithRetry $backupExe $exePath }
    if (Test-Path -LiteralPath $exePath) {
        Start-Process -FilePath $exePath -WorkingDirectory $target | Out-Null
    }
    Write-Log "rollback done"
} catch {
    Write-Log ("rollback failed: {0}" -f $_.Exception.Message)
}
exit 2
'''
