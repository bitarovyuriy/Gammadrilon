# -*- coding: utf-8 -*-
"""Автообновление zapret: версии, архив, сохранение пользовательского, откат."""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_zapret  # noqa: E402
import gd_zapret_update as upd  # noqa: E402


def _make_zip(zip_path: Path, prefix: str = "zapret-new") -> Path:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"{prefix}/bin/winws.exe", b"MZ")
        zf.writestr(f"{prefix}/service.bat", "@echo off\n")
        zf.writestr(f"{prefix}/general.bat", "@echo off\n")
        zf.writestr(f"{prefix}/installed-version.txt", "2.0\n")
        zf.writestr(f"{prefix}/lists/list-general.txt", "example.com\n")
    return zip_path


def test_version_compare_is_numeric_not_string():
    assert upd.parse_version("1.10.10") > upd.parse_version("1.10.3")
    assert upd.parse_version("1.10.3") > upd.parse_version("1.9.9")
    assert upd.version_is_newer("1.10.4", "1.10.3")
    assert not upd.version_is_newer("1.10.3", "1.10.3")
    assert upd.version_is_newer("1.10.3", None)


def test_installed_version_reads_installed_version_file(tmp_path):
    (tmp_path / "installed-version.txt").write_text("1.10.3\n", encoding="ascii")
    assert upd.installed_version(str(tmp_path)) == "1.10.3"
    (tmp_path / "installed-version.txt").unlink()
    (tmp_path / "version.txt").write_text("1.10.2", encoding="ascii")
    assert upd.installed_version(str(tmp_path)) == "1.10.2"


def test_release_zip_url_prefers_zip_asset():
    release = {"tag": "1.10.4", "assets": [
        {"name": "x.rar", "url": "https://example/x.rar"},
        {"name": "x.zip", "url": "https://example/x.zip"},
    ]}
    assert upd.release_zip_url(release) == "https://example/x.zip"
    fallback = upd.release_zip_url({"tag": "1.10.4", "assets": []})
    assert fallback.endswith("/download/1.10.4/zapret-discord-youtube-1.10.4.zip")


def test_check_updates_caches_for_a_day(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(upd, "CACHE_FILE", cache)
    calls = {"n": 0}

    def fake_json(url, timeout=0):
        calls["n"] += 1
        return {"tag_name": "1.10.4", "name": "1.10.4", "published_at": "",
                "body": "notes", "html_url": "https://example/release",
                "assets": [{"name": "x.zip", "size": 1, "browser_download_url": "https://x",
                            "download_count": 1}]}

    monkeypatch.setattr(upd, "_http_json", fake_json)
    (tmp_path / "installed-version.txt").write_text("1.10.3\n", encoding="ascii")
    first = upd.check_updates(str(tmp_path), force=True)
    second = upd.check_updates(str(tmp_path), force=False)
    assert first["ok"] and first["newer"] and not first["cached"]
    assert second["ok"] and second["cached"] and second["latest"] == "1.10.4"
    assert calls["n"] == 1


def test_validate_zip_rejects_path_traversal(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.exe", b"x")
    ok, msg = upd.validate_zip(bad)
    assert not ok and "опасные пути" in msg

    good = _make_zip(tmp_path / "good.zip")
    ok, msg = upd.validate_zip(good)
    assert ok, msg


def test_apply_update_keeps_user_files_and_rolls_back(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(upd, "MANIFEST_FILE", manifest)

    old = tmp_path / "zapret"
    (old / "bin").mkdir(parents=True)
    (old / "bin" / "winws.exe").write_bytes(b"MZ-old")
    (old / "service.bat").write_text("@echo off\n", encoding="ascii")
    (old / "installed-version.txt").write_text("1.10.3\n", encoding="ascii")
    (old / "general.bat").write_text("old\n", encoding="ascii")
    (old / "general (USER).user.bat").write_text("user\n", encoding="ascii")
    (old / "lists").mkdir()
    (old / "lists" / "list-general-user.txt").write_text("mine.example\n", encoding="utf-8")
    (old / "lists" / "ipset-all.txt").write_text("1.2.3.4/32\n", encoding="utf-8")
    (old / "utils").mkdir()
    (old / "utils" / "game_filter.enabled").write_text("all\n", encoding="ascii")

    archive = _make_zip(tmp_path / "new.zip")
    res = upd.apply_update(old, archive, "2.0")
    assert res["ok"], res

    assert (old / "installed-version.txt").read_text(encoding="ascii").strip() == "2.0"
    assert (old / "general.bat").read_text(encoding="ascii").strip() == "@echo off"
    assert (old / "general (USER).user.bat").read_text(encoding="ascii").strip() == "user"
    assert (old / "lists" / "list-general-user.txt").read_text(encoding="utf-8").strip() == "mine.example"
    assert (old / "lists" / "ipset-all.txt").read_text(encoding="utf-8").strip() == "1.2.3.4/32"
    assert (old / "utils" / "game_filter.enabled").read_text(encoding="ascii").strip() == "all"
    assert Path(res["backup"]).is_file()

    # откат возвращает старую версию и её файлы
    back = upd.rollback_update(upd.load_manifest())
    assert back["ok"], back
    assert (old / "installed-version.txt").read_text(encoding="ascii").strip() == "1.10.3"
    assert (old / "general.bat").read_text(encoding="ascii").strip() == "old"
    assert (old / "general (USER).user.bat").is_file()


def test_game_filter_advanced_format_roundtrip(tmp_path):
    ok, mode = gd_zapret.set_game_filter_mode(
        str(tmp_path), "all", "1024-1934,1936-65535", "1024-65535")
    assert ok and mode == "all"
    text = (tmp_path / "utils" / "game_filter.enabled").read_text(encoding="ascii")
    assert "tcp=1024-1934,1936-65535" in text and "udp=1024-65535" in text
    state = gd_zapret.game_filter_state(str(tmp_path))
    assert state["mode"] == "all"
    assert state["tcp"] == "1024-1934,1936-65535"
    assert state["udp"] == "1024-65535"


def test_game_filter_old_format_is_understood(tmp_path):
    (tmp_path / "utils").mkdir()
    (tmp_path / "utils" / "game_filter.enabled").write_text("tcp\n", encoding="ascii")
    state = gd_zapret.game_filter_state(str(tmp_path))
    assert state["mode"] == "tcp"
    assert state["tcp"] == gd_zapret.GAME_FILTER_DEFAULT_TCP


def test_game_filter_rejects_bad_range(tmp_path):
    ok, msg = gd_zapret.set_game_filter_mode(str(tmp_path), "tcp", "99999")
    assert not ok and "диапазон" in msg
    assert gd_zapret.valid_port_range("1024-1934,1936-65535") == "1024-1934,1936-65535"
    assert gd_zapret.valid_port_range("ABCD") is None
    assert gd_zapret.valid_port_range("2000-1000") is None


def test_service_binpath_quotes_winws(tmp_path):
    argv = ["--wf-tcp=80,443", "--filter-udp=443"]
    line = gd_zapret.service_binpath(str(tmp_path), argv)
    assert "winws.exe" in line
    assert "--wf-tcp=80,443" in line
