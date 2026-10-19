# -*- coding: utf-8 -*-
"""Разбор `sc query`: тип службы и её состояние — разные строки."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_zapret  # noqa: E402


def test_service_state_skips_type_line(monkeypatch):
    """«Тип : 10 WIN32_OWN_PROCESS» не должен приниматься за состояние.

    Раньше код 10 не находился в таблице и функция возвращала None, хотя
    служба работала — страница «Обход DPI» писала «служба не найдена».
    """
    output = (
        "\nИмя_службы: zapret \n"
        "        Тип                : 10  WIN32_OWN_PROCESS  \n"
        "        Состояние          : 4  RUNNING \n"
        "                                (STOPPABLE, NOT_PAUSABLE)\n"
    )
    monkeypatch.setattr(gd_zapret.gd_proc, "run",
                        lambda *a, **kw: SimpleNamespace(stdout=output, stderr=""))
    assert gd_zapret.service_state("zapret") == "RUNNING"


def test_service_state_returns_none_for_missing_service(monkeypatch):
    output = "[SC] EnumQueryServicesStatus: OpenService FAILED 1060:\n"
    monkeypatch.setattr(gd_zapret.gd_proc, "run",
                        lambda *a, **kw: SimpleNamespace(stdout="", stderr=output))
    assert gd_zapret.service_state("nope") is None


def test_strategy_bat_name_accepts_ui_id_and_file_name():
    """UI передаёт id без `.bat`, а recon ждал только имя файла."""
    assert gd_zapret.strategy_bat_name("general") == "general.bat"
    assert gd_zapret.strategy_bat_name("general (ALT2)") == "general (ALT2).bat"
    assert gd_zapret.strategy_bat_name("general.bat") == "general.bat"
    assert gd_zapret.strategy_bat_name("..\\general") is None
    assert gd_zapret.strategy_bat_name("other") is None


def test_valid_argv_rejects_quotes_and_newlines():
    """sc create хранит кавычки как экранированный текст — winws их не поймёт."""
    assert not gd_zapret._valid_argv(['--hostlist="C:\\x y\\list.txt"'])
    assert not gd_zapret._valid_argv(["--hostlist=x\n--new"])
    assert not gd_zapret._valid_argv([])
    assert gd_zapret._valid_argv(["--wf-tcp=80,443", "--hostlist=C:\\x\\list.txt"])
