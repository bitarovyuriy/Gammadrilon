# -*- coding: utf-8 -*-
"""Автообновление: выбор ассета, хэш, защита zip и реальный PowerShell-helper.

Сеть не трогаем: архив готовим сами, скачивание подменяем копированием файла.
Helper запускается по-настоящему — иначе ошибка в скрипте всплыла бы только у
человека после «Обновить».
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_autoupdate as au  # noqa: E402 — путь проекта добавлен выше


def _asset(name: str, digest: str = "", size: int = 0,
           host: str = "github.com", scheme: str = "https") -> dict:
    return {
        "name": name,
        "url": f"{scheme}://{host}/release/{name}",
        "size": size,
        "digest": f"sha256:{digest}" if digest else "",
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portable_zip(path: Path, exe: bytes = b"MZnew",
                  internal: str = "new.dll") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Gammadrilon/Gammadrilon.exe", exe)
        zf.writestr(f"Gammadrilon/_internal/{internal}", b"new")
        zf.writestr("Gammadrilon/README.md", "readme")
    return path


def _run_helper(work: Path, plan: dict) -> subprocess.CompletedProcess:
    test_root = work.resolve().parent
    for key in ("target_dir", "source_dir", "source_exe", "log_path", "work_dir", "settings_path", "settings_backup"):
        if plan.get(key):
            assert Path(plan[key]).resolve().is_relative_to(test_root)
    plan["script_path"] = str(au._write_helper(work))
    plan["config_path"] = str(au._write_config(work, plan))
    return subprocess.run(
        au.helper_command(plan["script_path"], plan["config_path"]),
        cwd=str(work), timeout=90, capture_output=True, text=True)


def test_asset_for_prefers_stable_names():
    assets = [
        _asset("Gammadrilon-v3.4-portable.zip"),
        _asset("Gammadrilon-portable.zip"),
        _asset("Gammadrilon.exe"),
    ]
    assert au.asset_for("onedir", assets)["name"] == "Gammadrilon-portable.zip"
    assert au.asset_for("onefile", assets)["name"] == "Gammadrilon.exe"


def test_asset_for_never_picks_the_installer_as_program():
    """Установщик — не новая версия программы.

    В релизе рядом лежат `Gammadrilon-Setup-3.6.exe` и `Gammadrilon-v3.6.exe`.
    Если выбрать первый, обновление заменит приложение установщиком.
    """
    assets = [
        _asset("Gammadrilon-Setup-3.6.exe"),
        _asset("Gammadrilon-v3.6.exe"),
        _asset("Gammadrilon-v3.6-portable.zip"),
    ]
    assert au.asset_for("onefile", assets)["name"] == "Gammadrilon-v3.6.exe"
    assert au.asset_for("onedir", assets)["name"] == "Gammadrilon-v3.6-portable.zip"


def test_asset_for_rejects_plain_http_and_wrong_kind():
    assert au.asset_for("onedir", [_asset("Gammadrilon-portable.zip", scheme="http")]) is None
    assert au.asset_for("onefile", [_asset("Gammadrilon-portable.zip")]) is None
    assert au.asset_for("dev", [_asset("Gammadrilon.exe")]) is None


def test_digest_hex_accepts_only_sha256():
    good = "a" * 64
    assert au.digest_hex({"digest": f"sha256:{good}"}) == good
    assert au.digest_hex({"digest": good.upper()}) == good
    assert au.digest_hex({"digest": "md5:abc"}) == ""
    assert au.digest_hex({}) == ""


def test_verify_digest_reports_mismatch(tmp_path):
    file = tmp_path / "update.zip"
    file.write_bytes(b"payload")
    au.verify_digest(file, _sha(file))

    with pytest.raises(au.UpdateError, match="Контрольная сумма"):
        au.verify_digest(file, "0" * 64)


def test_extract_onedir_rejects_zip_slip(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("Gammadrilon/Gammadrilon.exe", b"MZ")
        zf.writestr("Gammadrilon/_internal/x.dll", b"x")
        zf.writestr("Gammadrilon/../../evil.txt", b"evil")

    with pytest.raises(au.UpdateError, match="недопустимый путь"):
        au.extract_onedir(bad, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_extract_onedir_checks_layout(tmp_path):
    no_exe = tmp_path / "no_exe.zip"
    with zipfile.ZipFile(no_exe, "w") as zf:
        zf.writestr("Gammadrilon/_internal/x.dll", b"x")

    with pytest.raises(au.UpdateError, match="Gammadrilon.exe"):
        au.extract_onedir(no_exe, tmp_path / "out")

    wrong_root = tmp_path / "wrong_root.zip"
    with zipfile.ZipFile(wrong_root, "w") as zf:
        zf.writestr("Other/Gammadrilon.exe", b"MZ")
        zf.writestr("Other/_internal/x.dll", b"x")

    with pytest.raises(au.UpdateError, match="ожидалась папка"):
        au.extract_onedir(wrong_root, tmp_path / "out2")


@pytest.mark.parametrize("config_location", ["portable", "localappdata", "programdata", "legacy"])
def test_prepare_update_keeps_settings_backup(tmp_path, monkeypatch, config_location):
    package = _portable_zip(tmp_path / "update.zip")
    digest = _sha(package)
    info = {
        "ok": True,
        "tag": "v9.9",
        "assets": [_asset("Gammadrilon-portable.zip", digest, package.stat().st_size)],
    }

    target = tmp_path / "Тест Программы"
    (target / "_internal").mkdir(parents=True)
    (target / "Gammadrilon.exe").write_bytes(b"MZold")
    (target / "_internal" / "old.dll").write_bytes(b"old")
    appdata = tmp_path / "LocalAppData"
    programdata = tmp_path / "ProgramData"
    monkeypatch.setattr(au.gd_paths, "app_dir", lambda: target)
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    monkeypatch.setenv("ProgramData", str(programdata))
    monkeypatch.delenv("GD_PORTABLE", raising=False)
    if config_location == "portable":
        (target / "portable.txt").touch()
    active_dir = {
        "portable": target,
        "legacy": target,
        "localappdata": appdata / "Gammadrilon",
        "programdata": programdata / "Gammadrilon",
    }[config_location]
    (target / "presets.json").write_bytes(b'{"settings": {"theme": "light"}}')
    active_dir.mkdir(parents=True, exist_ok=True)
    active = active_dir / "presets.json"
    active.write_bytes(b'{"settings": {"theme": "dark"}}')

    work = tmp_path / "work"
    res = au.prepare_update(
        info, "onedir", root=target, work_root=work,
        download=lambda _asset, dest: shutil.copyfile(package, dest))

    assert res["ok"], res
    plan = res["plan"]
    assert plan["mode"] == "onedir"
    assert Path(plan["source_dir"], "Gammadrilon.exe").is_file()
    backup = active_dir / "presets.before-update.json"
    assert Path(plan["settings_backup"]) == backup
    assert backup.read_bytes() == active.read_bytes()
    assert (work / "apply_update.ps1").is_file()
    assert (work / "update.json").is_file()

    if shutil.which("powershell") is None:
        return
    # Обычное завершение перед заменой exe сохраняет последние настройки.
    active.write_bytes(b'{"settings": {"theme": "dark", "window_size": [820, 720]}}')
    preserved = active_dir / "rules.json"
    preserved.write_bytes(b'{"rules": [{"name": "keep"}]}')
    plan.update(app_pid=0, restart=False, log_path=str(tmp_path / "update.log"))
    result = _run_helper(work, plan)
    assert result.returncode == 0, result.stderr + result.stdout
    assert backup.read_bytes() == active.read_bytes()
    assert preserved.read_bytes() == b'{"rules": [{"name": "keep"}]}'
    assert (target / "Gammadrilon.exe").read_bytes() == b"MZnew"
    assert (target / "_internal" / "new.dll").read_bytes() == b"new"
    assert (target / "portable.txt").is_file() == (config_location == "portable")


def test_failed_settings_backup_preserves_previous_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("GD_PORTABLE", "1")
    monkeypatch.setattr(au.gd_paths, "app_dir", lambda: tmp_path)
    settings = tmp_path / "presets.json"
    backup = tmp_path / au.CONFIG_BACKUP
    settings.write_bytes(b'{"theme": "dark"}')
    backup.write_bytes(b'{"theme": "light"}')

    def fail_copy(source, destination):
        destination.write(source.read(1))
        raise OSError("disk unavailable")

    with monkeypatch.context() as interrupted:
        interrupted.setattr(au.shutil, "copyfileobj", fail_copy)
        with pytest.raises(au.UpdateError, match="disk unavailable"):
            au._copy_settings_backup(tmp_path)

    assert backup.read_bytes() == b'{"theme": "light"}'
    assert settings.read_bytes() == b'{"theme": "dark"}'
    assert set(tmp_path.iterdir()) == {settings, backup}
    assert au._copy_settings_backup(tmp_path) == backup
    assert backup.read_bytes() == settings.read_bytes()


@pytest.mark.skipif(shutil.which("powershell") is None, reason="нужен Windows PowerShell")
def test_helper_swaps_onedir_and_keeps_presets(tmp_path):
    target = tmp_path / "app"
    (target / "_internal").mkdir(parents=True)
    (target / "Gammadrilon.exe").write_bytes(b"MZold")
    (target / "_internal" / "old.dll").write_bytes(b"old")
    (target / "presets.json").write_text('{"presets": [1]}', encoding="utf-8")

    staging = tmp_path / "staging" / "Gammadrilon"
    (staging / "_internal").mkdir(parents=True)
    (staging / "Gammadrilon.exe").write_bytes(b"MZnew")
    (staging / "_internal" / "new.dll").write_bytes(b"new")

    work = tmp_path / "work"
    work.mkdir()
    plan = {
        "mode": "onedir",
        "target_dir": str(target),
        "source_dir": str(staging),
        "source_exe": "",
        "exe_name": "Gammadrilon.exe",
        "app_pid": 0,
        "wait_seconds": 1,
        "log_path": str(tmp_path / "update.log"),
        "work_dir": str(work),
        "restart": False,
    }
    result = _run_helper(work, plan)

    assert result.returncode == 0, result.stderr + result.stdout
    assert (target / "Gammadrilon.exe").read_bytes() == b"MZnew"
    assert (target / "_internal" / "new.dll").is_file()
    assert not (target / "_internal" / "old.dll").exists()
    assert (target / "presets.json").read_text(encoding="utf-8") == '{"presets": [1]}'
    assert (target / "_internal.old" / "old.dll").is_file()
    assert (target / "Gammadrilon.exe.old").is_file()


@pytest.mark.skipif(shutil.which("powershell") is None, reason="нужен Windows PowerShell")
@pytest.mark.parametrize("old_backup", [False, True])
def test_helper_rolls_back_broken_staging(tmp_path, old_backup):
    target = tmp_path / "app"
    (target / "_internal").mkdir(parents=True)
    (target / "Gammadrilon.exe").write_bytes(b"MZold")
    (target / "_internal" / "old.dll").write_bytes(b"old")
    (target / "presets.json").write_text("keep", encoding="utf-8")
    if old_backup:
        (target / "Gammadrilon.exe.old").write_bytes(b"MZprevious")
        (target / "_internal.old").mkdir()
        (target / "_internal.old" / "previous.dll").write_bytes(b"previous")

    staging = tmp_path / "staging" / "Gammadrilon"
    staging.mkdir(parents=True)      # exe и _internal забыли положить

    work = tmp_path / "work"
    work.mkdir()
    plan = {
        "mode": "onedir",
        "target_dir": str(target),
        "source_dir": str(staging),
        "source_exe": "",
        "exe_name": "Gammadrilon.exe",
        "app_pid": 0,
        "wait_seconds": 1,
        "log_path": str(tmp_path / "update.log"),
        "work_dir": str(work),
        "restart": False,
    }
    result = _run_helper(work, plan)

    assert result.returncode == 2, result.stderr + result.stdout
    assert (target / "Gammadrilon.exe").read_bytes() == b"MZold"
    assert (target / "_internal" / "old.dll").is_file()
    assert (target / "presets.json").read_text(encoding="utf-8") == "keep"
    if old_backup:
        assert (target / "Gammadrilon.exe.old").read_bytes() == b"MZprevious"
        assert (target / "_internal.old" / "previous.dll").is_file()


@pytest.mark.skipif(shutil.which("powershell") is None, reason="нужен Windows PowerShell")
@pytest.mark.parametrize("fail_rollback", [False, True])
def test_onedir_retry_and_independent_rollback(tmp_path, monkeypatch, fail_rollback):
    target = tmp_path / "Папка приложения [тест]"
    internal = target / "_internal"
    internal.mkdir(parents=True)
    (target / "Gammadrilon.exe").write_bytes(b"MZold")
    (internal / "old.dll").write_bytes(b"old")

    work = tmp_path / "Материалы обновления"
    staging = work / "staging"
    (staging / "_internal").mkdir(parents=True)
    (staging / "Gammadrilon.exe").write_bytes(b"MZnew")
    (staging / "_internal" / "new.dll").write_bytes(b"new")
    log = tmp_path / "update.log"
    plan = {
        "mode": "onedir",
        "target_dir": str(target),
        "source_dir": str(staging),
        "source_exe": "",
        "exe_name": "Gammadrilon.exe",
        "app_pid": 0,
        "wait_seconds": 1,
        "log_path": str(log),
        "work_dir": str(work),
        "restart": fail_rollback,
    }
    if fail_rollback:
        injection = '''
function Start-Process { return @{ ExitCode = 1 } }
function Start-Sleep { param($Milliseconds, $Seconds) }
function Move-Item {
    param([string]$LiteralPath, [string]$Destination, [switch]$Force)
    if ($LiteralPath.EndsWith("_internal.old")) { throw "the old directory is locked" }
    Microsoft.PowerShell.Management\\Move-Item @PSBoundParameters
}
'''
    else:
        injection = '''
$script:failedOnce = $false
function Copy-Item {
    param([string]$LiteralPath, [string]$Destination, [switch]$Recurse, [switch]$Force)
    if ($LiteralPath.EndsWith("_internal") -and -not $script:failedOnce) {
        $script:failedOnce = $true
        $null = [IO.Directory]::CreateDirectory($Destination)
        [IO.File]::WriteAllText((Join-Path $Destination "partial.dll"), "partial")
        throw "simulated copy failure after creating destination"
    }
    Microsoft.PowerShell.Management\\Copy-Item @PSBoundParameters
}
'''
    monkeypatch.setattr(au, "UPDATE_SCRIPT", au.UPDATE_SCRIPT.replace(
        "function Remove-Any", injection + "\nfunction Remove-Any", 1))

    result = _run_helper(work, plan)

    if fail_rollback:
        assert result.returncode == 2, result.stderr + result.stdout
        assert (target / "Gammadrilon.exe").read_bytes() == b"MZold"
        assert (target / "_internal.old" / "old.dll").read_bytes() == b"old"
        assert "old directory is locked" in log.read_text(encoding="utf-8-sig")
    else:
        assert result.returncode == 0, result.stderr + result.stdout
        assert (target / "Gammadrilon.exe").read_bytes() == b"MZnew"
        assert list(internal.iterdir()) == [internal / "new.dll"]


@pytest.mark.skipif(shutil.which("powershell") is None, reason="нужен Windows PowerShell")
def test_helper_replaces_onefile(tmp_path):
    target = tmp_path / "app"
    target.mkdir()
    (target / "Gammadrilon.exe").write_bytes(b"MZold")

    new_exe = tmp_path / "new" / "Gammadrilon.exe"
    new_exe.parent.mkdir()
    new_exe.write_bytes(b"MZnew")

    work = tmp_path / "work"
    work.mkdir()
    plan = {
        "mode": "onefile",
        "target_dir": str(target),
        "source_dir": "",
        "source_exe": str(new_exe),
        "exe_name": "Gammadrilon.exe",
        "app_pid": 0,
        "wait_seconds": 1,
        "log_path": str(tmp_path / "update.log"),
        "work_dir": str(work),
        "restart": False,
    }
    result = _run_helper(work, plan)

    assert result.returncode == 0, result.stderr + result.stdout
    assert (target / "Gammadrilon.exe").read_bytes() == b"MZnew"
    assert (target / "Gammadrilon.exe.old").read_bytes() == b"MZold"


@pytest.mark.skipif(shutil.which("powershell") is None, reason="нужен Windows PowerShell")
def test_failed_onefile_selftest_keeps_other_files(tmp_path, monkeypatch):
    target = tmp_path / "app"
    internal = target / "_internal"
    internal.mkdir(parents=True)
    (target / "Gammadrilon.exe").write_bytes(b"MZold")
    (internal / "other-app.dll").write_bytes(b"untouched")

    work = tmp_path / "work"
    work.mkdir()
    new_exe = work / "new.exe"
    new_exe.write_bytes(b"MZnew")
    log = tmp_path / "update.log"
    plan = {
        "mode": "onefile",
        "target_dir": str(target),
        "source_dir": "",
        "source_exe": str(new_exe),
        "exe_name": "Gammadrilon.exe",
        "app_pid": 0,
        "wait_seconds": 1,
        "log_path": str(log),
        "work_dir": str(work),
        "restart": True,
    }
    stub_start = '''function Start-Process {
    param([string]$FilePath, [string]$ArgumentList, [switch]$Wait,
        [switch]$PassThru, [string]$WindowStyle, [string]$WorkingDirectory)
    if ($ArgumentList -eq "--selftest") { return @{ ExitCode = 1 } }
    Write-Log "restarted previous app"
}

function Remove-Any'''
    monkeypatch.setattr(au, "UPDATE_SCRIPT", au.UPDATE_SCRIPT.replace("function Remove-Any", stub_start, 1))
    result = _run_helper(work, plan)

    assert result.returncode == 2, result.stderr + result.stdout
    assert (target / "Gammadrilon.exe").read_bytes() == b"MZold"
    assert (internal / "other-app.dll").read_bytes() == b"untouched"
    assert "restarted previous app" in log.read_text(encoding="utf-8-sig")
