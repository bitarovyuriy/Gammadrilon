# -*- coding: utf-8 -*-
"""Автообновление и списки zapret-discord-youtube (Flowseal).

Модуль закрывает ту часть работы с zapret, ради которой раньше приходилось
ходить в консольный ``service.bat``:

  * определение установленной версии и запрос последнего релиза на GitHub
    (релизы — единственный официальный канал обновления Flowseal);
  * скачивание zip-архива релиза, безопасная распаковка в staging;
  * бэкап текущей папки, сохранение пользовательских файлов (свои стратегии,
    ``*-user.txt``, ipset, флаги) и подмена каталога с откатом;
  * обновление ``lists/ipset-all.txt`` из служебного списка репозитория;
  * загрузка ``.service/hosts`` и аккуратное применение управляемого блока.

Все сетевые операции — через ``urllib`` (без сторонних зависимостей),
с таймаутами и понятной ошибкой вместо исключения. Тяжёлые действия
(подмена каталога, запись hosts) выполняет elevated-хелпер: см.
``gd_gpu_service.py``, команды ``zapret_update`` и ``zapret_hosts``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import gd_i18n as i18n
import gd_paths
import gd_state

REPO = "Flowseal/zapret-discord-youtube"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
REPO_URL = f"https://github.com/{REPO}"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/refs/heads/main"
IPSET_URL = f"{RAW_BASE}/.service/ipset-service.txt"
VERSION_URL = f"{RAW_BASE}/.service/version.txt"
HOSTS_URL = f"{RAW_BASE}/.service/hosts"

#: проверять обновления не чаще раза в сутки (щадим лимит GitHub API)
CHECK_TTL = 24 * 3600
#: сколько ждать сеть в фоне
NET_TIMEOUT = 20.0
#: zip-архив релиза для пользователя — самый простой формат под Windows
ZIP_PREFIX = "zapret-discord-youtube-"

#: блок в hosts, которым управляет Gammadrilon: чужое не трогаем
HOSTS_BEGIN = "# >>> Gammadrilon zapret hosts >>>"
HOSTS_END = "# <<< Gammadrilon zapret hosts <<<"

_DATA_DIR = gd_paths.log_dir()
CACHE_FILE = _DATA_DIR / "zapret_update.json"
MANIFEST_FILE = _DATA_DIR / "zapret_update_manifest.json"
HOSTS_BAK_DIR = _DATA_DIR / "zapret_hosts_backup"


def _now() -> float:
    return time.time()


def _data_dir() -> Path:
    d = Path(_DATA_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# версии
# --------------------------------------------------------------------------


def installed_version(root: str | None) -> str | None:
    """Версия установленного zapret: ``installed-version.txt``, затем ``version.txt``."""
    if not root:
        return None
    base = Path(root)
    for name in ("installed-version.txt", "version.txt"):
        try:
            text = (base / name).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text and len(text) < 40:
            return text.splitlines()[0].strip()
    return None


def parse_version(text: str) -> tuple:
    """Сравнимая версия: ``1.10.10`` важнее, чем ``1.10.3`` (числа, не строки).

    Суффиксы (``1.10.3b``) отбрасываются до цифр: официальный канал — релизы
    с числовым тегом, а сравнение не должно падать на «неожиданном» теге.
    """
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums) if nums else (0,)


def version_is_newer(latest: str | None, current: str | None) -> bool:
    if not latest:
        return False
    if not current:
        return True
    return parse_version(latest) > parse_version(current)


# --------------------------------------------------------------------------
# GitHub: последний релиз и кэш проверки
# --------------------------------------------------------------------------


def _http_json(url: str, timeout: float = NET_TIMEOUT) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Gammadrilon",
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError(i18n.t("GitHub вернул не объект"))
    return data


def _release_from_api(data: dict) -> dict:
    assets = []
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        assets.append({
            "name": str(asset.get("name") or ""),
            "size": int(asset.get("size") or 0),
            "url": str(asset.get("browser_download_url") or ""),
            "downloads": int(asset.get("download_count") or 0),
        })
    return {
        "tag": str(data.get("tag_name") or "").strip(),
        "name": str(data.get("name") or data.get("tag_name") or "").strip(),
        "published_at": str(data.get("published_at") or ""),
        "body": str(data.get("body") or ""),
        "url": str(data.get("html_url") or ""),
        "assets": assets,
    }


def latest_from_raw(timeout: float = NET_TIMEOUT) -> str | None:
    """Запасной канал: ``.service/version.txt`` (то же, что читает service.bat)."""
    try:
        req = urllib.request.Request(VERSION_URL, headers={"User-Agent": "Gammadrilon",
                                                           "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace").strip()
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return text.splitlines()[0].strip() if text else None


def check_updates(root: str | None = None, force: bool = False) -> dict:
    """Сверить установленную версию с последним релизом Flowseal.

    Возвращает словарь (никогда не бросает):

      ``{ok, current, latest, newer, release, checked_at, cached, error}``.

    Результат кэшируется на сутки: окно не должно дёргать GitHub API при
    каждом открытии вкладки, а лимит анонимных запросов — 60 в час.
    """
    current = installed_version(root)
    cache = gd_state.read_json(CACHE_FILE, {})
    if not isinstance(cache, dict):
        cache = {}
    checked_at = cache.get("checked_at") if isinstance(cache, dict) else None
    fresh = isinstance(checked_at, (int, float)) and (_now() - checked_at) < CHECK_TTL
    if fresh and not force and cache.get("latest"):
        return {
            "ok": True, "current": current, "latest": cache.get("latest"),
            "newer": version_is_newer(cache.get("latest"), current),
            "release": cache.get("release") or {}, "checked_at": checked_at,
            "cached": True, "error": None,
        }

    release: dict = {}
    error = None
    latest = None
    try:
        release = _release_from_api(_http_json(API_LATEST))
        latest = release.get("tag") or None
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        latest = latest_from_raw()
        if latest:
            release = {"tag": latest, "name": latest, "published_at": "",
                       "body": "", "url": f"{REPO_URL}/releases/tag/{latest}", "assets": []}

    if not latest:
        return {"ok": False, "current": current, "latest": None, "newer": False,
                "release": {}, "checked_at": None, "cached": False,
                "error": error or i18n.t("не удалось получить последнюю версию")}

    payload = {
        "checked_at": _now(), "latest": latest, "release": release,
        "current_at_check": current,
    }
    gd_state.write_json_atomic(CACHE_FILE, payload)
    return {"ok": True, "current": current, "latest": latest,
            "newer": version_is_newer(latest, current), "release": release,
            "checked_at": payload["checked_at"], "cached": False, "error": error}


def release_zip_url(release: dict) -> str | None:
    """Прямая ссылка на zip-архив релиза (архив предпочтительнее rar: есть zipfile)."""
    for asset in (release or {}).get("assets") or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("url") or "")
        if name.lower().endswith(".zip") and url:
            return url
    tag = (release or {}).get("tag") or ""
    if tag:
        return f"{REPO_URL}/releases/download/{tag}/{ZIP_PREFIX}{tag}.zip"
    return None


# --------------------------------------------------------------------------
# скачивание
# --------------------------------------------------------------------------


def download(url: str, dest: str | Path, on_progress=None,
             timeout: float = 120.0) -> tuple[bool, str]:
    """Скачать файл на диск кусками. ``on_progress(done, total)`` — по желанию."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Gammadrilon"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, part.open("wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if on_progress:
                    try:
                        on_progress(done, total)
                    except Exception:  # noqa: BLE001 — прогресс не должен ломать загрузку
                        pass
        if dest.exists():
            dest.unlink()
        os.replace(part, dest)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        return False, i18n.t("не удалось скачать: {v0}", v0=exc)
    if dest.stat().st_size <= 0:
        return False, i18n.t("скачался пустой файл")
    return True, str(dest)


# --------------------------------------------------------------------------
# распаковка и проверка архива
# --------------------------------------------------------------------------


def _safe_zip_members(zf: zipfile.ZipFile) -> list[str]:
    """Защита от «zip-slip»: запрещаем абсолютные пути и ``..``."""
    bad = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            bad.append(name)
            continue
        if any(part == ".." for part in name.split("/")):
            bad.append(name)
    return bad


def validate_zip(zip_path: str | Path) -> tuple[bool, str]:
    """Проверить, что архив — релиз zapret (есть ``bin/winws.exe``)."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if _safe_zip_members(zf):
                return False, i18n.t("архив содержит опасные пути")
            names = [n.replace("\\", "/") for n in zf.namelist()]
            if not any(n.endswith("bin/winws.exe") for n in names):
                return False, i18n.t("в архиве нет bin\\winws.exe")
    except (zipfile.BadZipFile, OSError) as exc:
        return False, i18n.t("битый архив: {v0}", v0=exc)
    return True, ""


def extract_archive(zip_path: str | Path, staging: str | Path) -> tuple[bool, str | Path]:
    """Распаковать архив в staging и вернуть корень с ``bin/winws.exe``."""
    staging = Path(staging)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging)
    except (zipfile.BadZipFile, OSError) as exc:
        return False, i18n.t("не удалось распаковать: {v0}", v0=exc)

    if (staging / "bin" / "winws.exe").is_file():
        return True, staging
    children = [p for p in staging.iterdir() if p.is_dir()]
    for child in children:
        if (child / "bin" / "winws.exe").is_file():
            return True, child
    return False, i18n.t("в архиве нет папки bin\\winws.exe")


# --------------------------------------------------------------------------
# сохранение пользовательского и подмена каталога
# --------------------------------------------------------------------------


def _preserved_relpaths(root: Path, new_root: Path) -> list[str]:
    """Что переносим из старой установки в новую.

    Пользовательское и настройки — да; файлы официального релиза — нет
    (иначе новая версия не заработает). Свои стратегии вне релиза сохраняем
    целиком, поэтому ``recon`` для них остаётся рабочим.
    """
    keep: list[str] = []
    patterns = (
        "*.user.bat",
        "utils/*.enabled",
        "lists/*-user.txt",
        "lists/ipset-all.txt",
        "lists/ipset-all.txt.backup",
    )
    for pat in patterns:
        for old in root.glob(pat):
            if old.is_file():
                keep.append(old.relative_to(root).as_posix())

    new_bats = {p.name for p in new_root.glob("general*.bat")}
    for old in root.glob("general*.bat"):
        if old.name not in new_bats:
            keep.append(old.name)
    return sorted(set(keep))


def backup_root(root: str | Path, version: str | None = None) -> Path | None:
    """Заархивировать текущую папку рядом: ``имя.backup-версия-дата.zip``."""
    src = Path(root)
    if not src.is_dir():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_version = re.sub(r"[^0-9A-Za-z._-]", "_", version or "unknown")
    base = src.parent / f"{src.name}.backup-{safe_version}-{stamp}"
    try:
        return Path(shutil.make_archive(str(base), "zip", root_dir=str(src.parent),
                                        base_dir=src.name))
    except OSError:
        return None


def apply_update(root: str | Path, zip_path: str | Path, new_version: str,
                 on_progress=None) -> dict:
    """Подменить папку zapret содержимым архива, сохранив пользовательское.

    Перед вызовом winws и служба должны быть остановлены — вызывающий знает,
    как это сделать. При любой ошибке старая папка возвращается на место.
    """
    src = Path(root)
    if not src.is_dir():
        return {"ok": False, "error": i18n.t("папка zapret не найдена")}
    ok, msg = validate_zip(zip_path)
    if not ok:
        return {"ok": False, "error": msg}

    old_version = installed_version(str(src))
    old_dir = src.parent / f"{src.name}.old-{int(_now())}"
    staging = src.parent / f"{src.name}.stage-{int(_now())}"
    backup = None
    moved_old = False
    try:
        if on_progress:
            on_progress(0, 1, i18n.t("резервная копия"))
        backup = backup_root(src, old_version)
        if backup is None:
            return {"ok": False, "error": i18n.t("не удалось создать резервную копию")}

        ok, res = extract_archive(zip_path, staging)
        if not ok:
            return {"ok": False, "error": res}
        new_root = Path(res)

        # переносим пользовательское до подмены: новые файлы релиза важнее
        copied = 0
        for rel in _preserved_relpaths(src, new_root):
            old = src / rel
            dst = new_root / rel
            if not old.is_file():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, dst)
            copied += 1

        if on_progress:
            on_progress(1, 2, i18n.t("замена папки"))
        os.replace(src, old_dir)
        moved_old = True
        os.replace(new_root, src)

        # новая версия могла не привезти installed-version.txt — фиксируем явно
        (src / "installed-version.txt").write_text(new_version + "\n", encoding="ascii")
        shutil.rmtree(old_dir, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
    except OSError as exc:
        # откат: старая папка на месте, staging убираем
        try:
            if moved_old and old_dir.exists() and not src.exists():
                os.replace(old_dir, src)
        except OSError:
            pass
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": i18n.t("не удалось заменить папку: {v0}", v0=exc)}

    manifest = {
        "root": str(src), "backup": str(backup or ""),
        "old_version": old_version, "new_version": new_version,
        "at": _now(), "kept_files": copied,
    }
    gd_state.write_json_atomic(MANIFEST_FILE, manifest)
    return {"ok": True, "backup": str(backup or ""), "old_version": old_version,
            "new_version": new_version, "copied": copied, "root": str(src)}


def load_manifest() -> dict | None:
    data = gd_state.read_json(MANIFEST_FILE, None)
    return data if isinstance(data, dict) else None


def rollback_update(manifest: dict | None = None) -> dict:
    """Вернуть папку из резервной копии последнего обновления."""
    manifest = manifest or load_manifest()
    if not manifest:
        return {"ok": False, "error": i18n.t("нет резервной копии обновления")}
    root = Path(str(manifest.get("root") or ""))
    backup = Path(str(manifest.get("backup") or ""))
    if not backup.is_file():
        return {"ok": False, "error": i18n.t("резервная копия не найдена")}
    if not root.parent.is_dir():
        return {"ok": False, "error": i18n.t("папка установки недоступна")}
    try:
        if root.exists():
            shutil.rmtree(root)
        with zipfile.ZipFile(backup) as zf:
            zf.extractall(root.parent)
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "error": i18n.t("не удалось откатить: {v0}", v0=exc)}
    if not (root / "bin" / "winws.exe").is_file():
        return {"ok": False, "error": i18n.t("откат без рабочего winws.exe")}
    try:
        MANIFEST_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True, "root": str(root), "version": manifest.get("old_version")}


# --------------------------------------------------------------------------
# списки и hosts
# --------------------------------------------------------------------------


def _looks_like_data(text: str) -> bool:
    """Не HTML-ошибка и не пустышка: хотя бы 20 строк с IP/доменом."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if len(lines) < 20:
        return False
    head = "\n".join(lines[:5]).lower()
    return "<html" not in head and "<!doctype" not in head


def update_ipset(root: str | Path, on_progress=None) -> dict:
    """Обновить ``lists/ipset-all.txt`` из служебного списка Flowseal."""
    root = Path(root)
    lists = root / "lists"
    if not lists.is_dir():
        return {"ok": False, "error": i18n.t("папки lists нет")}
    target = lists / "ipset-all.txt"
    tmp = target.with_name("ipset-all.txt.download")
    ok, msg = download(IPSET_URL, tmp, on_progress=on_progress)
    if not ok:
        return {"ok": False, "error": msg}
    try:
        text = tmp.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": str(exc)}
    if not _looks_like_data(text):
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": i18n.t("список похож на ошибку загрузки")}

    # заботимся о режиме «none»: placeholder не должен затирать рабочий backup
    from gd_zapret import ipset_info  # локальный импорт: без цикла на старте

    mode = (ipset_info(str(root)) or {}).get("mode")
    backup = Path(str(target) + ".backup")
    if mode == "loaded" and target.is_file():
        try:
            shutil.copy2(target, backup)
        except OSError:
            pass
    os.replace(tmp, target)
    lines = len([ln for ln in text.splitlines() if ln.strip()])
    return {"ok": True, "lines": lines, "size": target.stat().st_size}


def fetch_hosts() -> tuple[bool, str]:
    """Скачать официальный hosts-файл Flowseal в временный файл."""
    tmp = _data_dir() / "zapret_hosts.download"
    ok, msg = download(HOSTS_URL, tmp)
    if not ok:
        return False, msg
    try:
        text = tmp.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return False, str(exc)
    if "<html" in text.lower():
        tmp.unlink(missing_ok=True)
        return False, i18n.t("hosts похож на ошибку загрузки")
    return True, str(tmp)


def hosts_status(downloaded: str | Path) -> dict:
    """Нужно ли обновлять hosts: сравниваем первую и последнюю строки файла."""
    hosts = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
    try:
        current = hosts.read_text(encoding="utf-8", errors="replace")
    except OSError:
        current = ""
    try:
        lines = [ln.rstrip("\r\n") for ln in Path(downloaded).read_text(
            encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        lines = []
    if not lines:
        return {"needs_update": False, "path": str(hosts), "reason": "empty"}
    first, last = lines[0], lines[-1]
    return {
        "needs_update": (first not in current) or (last not in current),
        "path": str(hosts), "first": first, "last": last,
        "managed": HOSTS_BEGIN in current and HOSTS_END in current,
    }


def _replace_managed_block(current: str, block: str) -> str:
    """Заменить блок Gammadrilon, не задевая остальные строки hosts."""
    if HOSTS_BEGIN in current and HOSTS_END in current:
        start = current.index(HOSTS_BEGIN)
        end = current.index(HOSTS_END) + len(HOSTS_END)
        return current[:start] + block + current[end:]
    if current and not current.endswith("\n"):
        current += "\n"
    return current + ("\n" if current else "") + block + "\n"


def apply_hosts(downloaded: str | Path) -> dict:
    """Применить скачанный hosts безопасно: бэкап + управляемый блок.

    Требует прав администратора (пишет системный hosts), поэтому вызывается
    из elevated-хелпера.
    """
    hosts = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
    try:
        content = Path(downloaded).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    block = HOSTS_BEGIN + "\n" + content.strip() + "\n" + HOSTS_END
    try:
        current = hosts.read_text(encoding="utf-8", errors="replace")
    except OSError:
        current = ""
    HOSTS_BAK_DIR.mkdir(parents=True, exist_ok=True)
    bak = HOSTS_BAK_DIR / f"hosts-{time.strftime('%Y%m%d-%H%M%S')}.bak"
    try:
        shutil.copy2(hosts, bak)
        hosts.write_text(_replace_managed_block(current, block), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": i18n.t("не удалось обновить hosts: {v0}", v0=exc)}
    return {"ok": True, "backup": str(bak), "path": str(hosts)}
