# -*- coding: utf-8 -*-
"""Портативный режим и единые пути (gd_paths).

Правило простое: без маркера рядом с приложением всё как раньше
(ProgramData — данные, LocalAppData — логи), с маркером — рядом с exe.
Переменная GD_PORTABLE сильнее маркера, чтобы приборы не зависели от того,
что лежит в папке проекта.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_paths  # noqa: E402 — путь проекта добавлен выше

APP = gd_paths.APP_NAME


def _env_for(tmp_path: Path, monkeypatch, flag: str | None, marker: bool):
    """Приложение «лежит» в tmp_path: ProgramData/LocalAppData тоже там."""
    monkeypatch.setattr(gd_paths, "app_dir", lambda: tmp_path / "app")
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    if flag is None:
        monkeypatch.delenv(gd_paths.ENV_FLAG, raising=False)
    else:
        monkeypatch.setenv(gd_paths.ENV_FLAG, flag)
    if marker:
        (tmp_path / "app" / "portable.txt").write_text("portable\n", encoding="utf-8")
    return tmp_path / "app"


def test_without_marker_everything_is_where_it_was(tmp_path, monkeypatch):
    _env_for(tmp_path, monkeypatch, flag=None, marker=False)
    assert not gd_paths.portable_enabled()
    assert gd_paths.data_dir() == tmp_path / "pd" / APP
    assert gd_paths.log_dir() == tmp_path / "lad" / APP
    assert gd_paths.find_marker() is None


def test_marker_next_to_app_turns_portable_on(tmp_path, monkeypatch):
    app = _env_for(tmp_path, monkeypatch, flag=None, marker=True)
    assert gd_paths.portable_enabled()
    assert gd_paths.data_dir() == app
    assert gd_paths.log_dir() == app


def test_marker_name_variants_are_accepted(tmp_path, monkeypatch):
    for name in gd_paths.MARKER_NAMES[1:]:
        app = _env_for(tmp_path / name.replace(".", "_"), monkeypatch, flag=None, marker=False)
        (app / name).write_text("", encoding="utf-8")
        assert gd_paths.portable_enabled(), name


def test_env_flag_beats_marker_in_both_directions(tmp_path, monkeypatch):
    app = _env_for(tmp_path, monkeypatch, flag="0", marker=True)
    assert not gd_paths.portable_enabled(), "GD_PORTABLE=0 должен выключать режим и с маркером"
    assert gd_paths.data_dir() == tmp_path / "pd" / APP

    app = _env_for(tmp_path / "b", monkeypatch, flag="1", marker=False)
    assert gd_paths.portable_enabled(), "GD_PORTABLE=1 должен включать режим и без маркера"
    assert gd_paths.data_dir() == app


def test_garbage_env_flag_falls_back_to_marker(tmp_path, monkeypatch):
    _env_for(tmp_path, monkeypatch, flag="может быть", marker=True)
    assert gd_paths.portable_enabled()


def test_modules_ask_gd_paths(tmp_path, monkeypatch):
    """Журнал, правила и DNS ходят за путями в gd_paths, а не считают сами."""
    app = _env_for(tmp_path, monkeypatch, flag="1", marker=False)

    import gd_dns
    import gd_journal
    import gd_rules

    assert gd_journal.journal_path() == app / "journal.jsonl"
    assert gd_rules.rules_path().parent == app
    assert gd_dns.state_path().parent == app


def test_app_dir_is_project_dir_when_running_from_sources():
    assert not getattr(sys, "frozen", False)
    assert gd_paths.app_dir() == ROOT


def test_import_time_paths_follow_environment():
    """Компаньоны читают пути на импорте — там должен действовать тот же режим."""
    script = (
        "import gd_power, gd_zapret, gd_helper, gd_paths;"
        "print(gd_power.STATE_DIR); print(gd_zapret._DATA_DIR);"
        "print(gd_helper._SESSION_DIR); print(gd_paths.app_dir())"
    )
    base = dict(os.environ)
    base.pop(gd_paths.ENV_FLAG, None)

    env = dict(base, GD_PORTABLE="1")
    out = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, check=True)
    lines = out.stdout.strip().splitlines()
    app = str(ROOT)
    assert lines == [app, app, app, app], out.stdout

    env = dict(base, GD_PORTABLE="0",
               ProgramData=str(ROOT / "_pd_tmp"), LOCALAPPDATA=str(ROOT / "_lad_tmp"))
    out = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, check=True)
    lines = out.stdout.strip().splitlines()
    assert lines[0] == str(ROOT / "_pd_tmp" / APP)
    assert lines[1] == str(ROOT / "_lad_tmp" / APP)
    assert lines[2] == str(ROOT / "_lad_tmp" / APP)


def test_mode_note_tells_where_data_lives(tmp_path, monkeypatch):
    app = _env_for(tmp_path, monkeypatch, flag=None, marker=False)
    note = gd_paths.mode_note()
    assert str(gd_paths.data_dir()) in note and str(gd_paths.log_dir()) in note
    assert gd_paths.MARKER_NAMES[0] in note, "человеку нужен точный признак режима"

    # в портативном режиме путь один и тот же — показываем его, а не два разных
    _env_for(tmp_path, monkeypatch, flag="1", marker=False)
    note = gd_paths.mode_note()
    assert str(app) in note
    assert gd_paths.MARKER_NAMES[0] not in note
