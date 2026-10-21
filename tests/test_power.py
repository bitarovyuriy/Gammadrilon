# -*- coding: utf-8 -*-
"""Профили питания: повторное применение не должно копить копии схем.

Все вызовы powrprof подменены: тест не трогает настоящие схемы Windows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_power  # noqa: E402 — путь проекта добавлен выше


def _fake_system(monkeypatch):
    """Маленькая модель схем питания: словарь GUID -> имя и активный GUID."""
    schemes = {"ORIG": "Сбалансированная"}
    holder = {"state": None}
    calls = {"duplicate": [], "deleted": [], "active": "ORIG"}

    monkeypatch.setattr(gd_power, "load_state", lambda: holder["state"])
    monkeypatch.setattr(gd_power, "save_state", lambda st: holder.__setitem__("state", st))
    monkeypatch.setattr(gd_power, "clear_state", lambda: True)
    monkeypatch.setattr(gd_power, "get_active_scheme", lambda: calls["active"])
    monkeypatch.setattr(gd_power, "scheme_name", lambda guid: schemes.get(guid, ""))
    monkeypatch.setattr(gd_power, "list_schemes", lambda: list(schemes))
    monkeypatch.setattr(gd_power, "write_values",
                        lambda guid, values: {"ok": list(values), "failed": {}})

    def duplicate(src):
        guid = f"CLONE-{len(calls['duplicate']) + 1}"
        calls["duplicate"].append(src)
        schemes[guid] = schemes.get(src, "")
        return guid

    def rename(guid, name):
        schemes[guid] = name
        return True

    def activate(guid):
        calls["active"] = guid
        return True

    def delete(guid):
        calls["deleted"].append(guid)
        schemes.pop(guid, None)
        return True

    monkeypatch.setattr(gd_power, "duplicate_scheme", duplicate)
    monkeypatch.setattr(gd_power, "rename_scheme", rename)
    monkeypatch.setattr(gd_power, "set_active_scheme", activate)
    monkeypatch.setattr(gd_power, "delete_scheme", delete)
    return schemes, holder, calls


def test_reapply_updates_same_clone(monkeypatch):
    """Второй профиль пишется в ту же копию: original и GUID не подменяются."""
    schemes, holder, calls = _fake_system(monkeypatch)

    first = gd_power.apply_profile("eco")
    second = gd_power.apply_profile("normal")

    assert calls["duplicate"] == ["ORIG"], "клон активного клона создавать нельзя"
    assert second["original_scheme"] == "ORIG"
    assert second["clone_scheme"] == first["clone_scheme"]
    assert calls["deleted"] == [], "рабочую копию при повторном применении не удаляем"
    name = schemes[second["clone_scheme"]]
    assert name.count(gd_power.CLONE_MARK) == 1, name
    assert holder["state"]["profile"] == "normal"


def test_apply_after_manual_switch_uses_new_scheme(monkeypatch):
    """Пользователь выбрал другую схему — она и становится новой исходной."""
    schemes, holder, calls = _fake_system(monkeypatch)
    gd_power.apply_profile("eco")

    schemes["OTHER"] = "Игровая"
    calls["active"] = "OTHER"
    state = gd_power.apply_profile("boost")

    assert state["original_scheme"] == "OTHER"
    assert calls["deleted"] == ["CLONE-1"], "прошлая копия должна быть убрана"
    assert calls["duplicate"][-1] == "OTHER"


def test_failed_reapply_keeps_working_clone(monkeypatch):
    """Неудачная запись не удаляет копию, в которой уже был рабочий профиль."""
    schemes, holder, calls = _fake_system(monkeypatch)
    first = gd_power.apply_profile("eco")

    monkeypatch.setattr(
        gd_power, "write_values",
        lambda guid, values: {"ok": [], "failed": {"max_state": [("AC", 1)]}},
    )
    with pytest.raises(OSError):
        gd_power.apply_profile("normal")

    assert calls["deleted"] == []
    assert holder["state"]["profile"] == "eco"
    assert holder["state"]["clone_scheme"] == first["clone_scheme"]


def test_clone_name_strips_repeated_mark(monkeypatch):
    """Имя копии не должно расти: «Gammadrilon — Gammadrilon — X» -> одна метка."""
    monkeypatch.setattr(
        gd_power, "scheme_name",
        lambda guid: "Gammadrilon — Gammadrilon — Сбалансированная",
    )
    assert gd_power.clone_name("X") == "Gammadrilon — Сбалансированная"


def test_snapshot_reports_profile_only_for_active_clone(monkeypatch):
    """State от прошлой схемы не выдаёт профиль за применённый."""
    monkeypatch.setattr(gd_power, "read_values", lambda guid: {})
    monkeypatch.setattr(gd_power, "get_active_scheme", lambda: "CLONE-1")
    monkeypatch.setattr(gd_power, "scheme_name", lambda guid: "Gammadrilon — ЭКО")
    state = {"profile": "eco", "clone_scheme": "CLONE-1", "original_scheme": "ORIG"}
    monkeypatch.setattr(gd_power, "read_state_report", lambda: (state, ""))

    snap = gd_power.state_snapshot()
    assert snap["profile"] == "eco"
    assert snap["active_is_clone"] is True

    monkeypatch.setattr(gd_power, "get_active_scheme", lambda: "ORIG")
    monkeypatch.setattr(gd_power, "scheme_name", lambda guid: "Сбалансированная")
    snap = gd_power.state_snapshot()
    assert snap["profile"] is None, "после ручного выбора профиль уже не активен"
    assert snap["active_is_clone"] is False


def test_snapshot_marks_clone_without_state(monkeypatch):
    """Активная наша копия без state — это не «исходная схема»."""
    monkeypatch.setattr(gd_power, "read_values", lambda guid: {})
    monkeypatch.setattr(gd_power, "get_active_scheme", lambda: "CLONE-OLD")
    monkeypatch.setattr(gd_power, "scheme_name", lambda guid: "Gammadrilon — Gammadrilo")
    monkeypatch.setattr(gd_power, "read_state_report", lambda: (None, ""))

    snap = gd_power.state_snapshot()
    assert snap["profile"] is None
    assert snap["active_is_clone"] is True


def test_display_name_drops_cut_marker_tail():
    """Огрызок метки от старого обрезания на 80 символах не показывается."""
    name = "Gammadrilon — Gammadrilon — Gammadrilo"
    assert gd_power.display_scheme_name(name) == ""


def test_clone_name_fits_windows_limit(monkeypatch):
    monkeypatch.setattr(gd_power, "scheme_name", lambda guid: "Д" * 200)
    assert len(gd_power.clone_name("X")) <= 80


def test_apply_on_orphan_clone_reuses_it(monkeypatch):
    """State потерян, но активна наша копия: пишем в неё, а не клонируем клон."""
    schemes, holder, calls = _fake_system(monkeypatch)
    schemes["CLONE-OLD"] = "Gammadrilon — Сбалансированная"
    calls["active"] = "CLONE-OLD"
    holder["state"] = None

    state = gd_power.apply_profile("eco")

    assert calls["duplicate"] == [], "новую копию из старой копии делать нельзя"
    assert state["clone_scheme"] == "CLONE-OLD"
    assert state["original_scheme"] == "ORIG", "исходную нашли по имени без метки"
    assert schemes["CLONE-OLD"].count(gd_power.CLONE_MARK) == 1
    assert schemes["CLONE-OLD"] == "Gammadrilon — Сбалансированная"


def test_apply_sweeps_orphan_clones(monkeypatch):
    """Применение профиля убирает все чужие наши копии, а не только прошлую."""
    schemes, holder, calls = _fake_system(monkeypatch)
    schemes["CLONE-OLD-1"] = "Gammadrilon — Сбалансированная"
    schemes["CLONE-OLD-2"] = "Gammadrilon — Сбалансированная"
    holder["state"] = None

    state = gd_power.apply_profile("normal")

    assert sorted(calls["deleted"]) == ["CLONE-OLD-1", "CLONE-OLD-2"]
    assert state["clone_scheme"] in schemes
    assert [g for g in schemes if g.startswith("CLONE-")] == [state["clone_scheme"]]


def test_cleanup_switches_away_from_active_clone(monkeypatch):
    """Активную копию Windows не удаляет: сначала возвращаем обычную схему."""
    schemes, holder, calls = _fake_system(monkeypatch)
    schemes["CLONE-1"] = "Gammadrilon — Сбалансированная"
    schemes["CLONE-2"] = "Gammadrilon — Сбалансированная"
    calls["active"] = "CLONE-2"
    holder["state"] = None

    rep = gd_power.cleanup_clones()

    assert rep["activated"] == "ORIG"
    assert calls["active"] == "ORIG"
    assert sorted(rep["removed"]) == ["CLONE-1", "CLONE-2"]
    assert not rep["failed"]
    assert "CLONE-1" not in schemes and "CLONE-2" not in schemes
    assert "ORIG" in schemes


def test_cleanup_keeps_working_clone(monkeypatch):
    """Копию применённого профиля уборка не трогает: по ней делается откат."""
    schemes, holder, calls = _fake_system(monkeypatch)
    state = gd_power.apply_profile("eco")
    schemes["CLONE-OLD"] = "Gammadrilon — Сбалансированная"

    rep = gd_power.cleanup_clones()

    assert rep["removed"] == ["CLONE-OLD"]
    assert state["clone_scheme"] in schemes
    assert holder["state"]["clone_scheme"] == state["clone_scheme"]


def test_list_clones_hides_tracked_clone(monkeypatch):
    """Рабочая копия профиля — не «остаток», её не показываем и не считаем мусором."""
    schemes, holder, calls = _fake_system(monkeypatch)
    state = gd_power.apply_profile("eco")
    schemes["CLONE-OLD"] = "Gammadrilon — Сбалансированная"

    rows = gd_power.list_clones()
    assert [r["guid"] for r in rows] == ["CLONE-OLD"]
    assert [r["guid"] for r in gd_power.list_clones(include_tracked=True)] == [
        state["clone_scheme"], "CLONE-OLD"]


def test_cleanup_repairs_state_pointing_to_clone(monkeypatch):
    """Старый state записал «исходной» нашу же копию: уборка чинит её и удаляет."""
    schemes, holder, calls = _fake_system(monkeypatch)
    gd_power.apply_profile("eco")
    schemes["CLONE-BAD"] = "Gammadrilon — Gammadrilon — Gammadrilo"
    bad_state = dict(holder["state"])
    bad_state["original_scheme"] = "CLONE-BAD"
    holder["state"] = bad_state

    rep = gd_power.cleanup_clones()

    assert rep["removed"] == ["CLONE-BAD"]
    assert holder["state"]["original_scheme"] == "ORIG"
    assert "CLONE-BAD" not in schemes


def test_apply_repairs_original_that_is_clone(monkeypatch):
    """Повторное применение не тащит дальше испорченную «исходную» из state."""
    schemes, holder, calls = _fake_system(monkeypatch)
    gd_power.apply_profile("eco")
    schemes["CLONE-BAD"] = "Gammadrilon — Сбалансированная"
    bad_state = dict(holder["state"])
    bad_state["original_scheme"] = "CLONE-BAD"
    holder["state"] = bad_state

    state = gd_power.apply_profile("normal")

    assert state["original_scheme"] == "ORIG"
    assert "CLONE-BAD" in calls["deleted"]


def test_restore_without_original_strips_mark(monkeypatch):
    """Исходную схему не вернуть, а копию нельзя удалить — снимаем метку."""
    schemes, holder, calls = _fake_system(monkeypatch)
    schemes["CLONE-1"] = "Gammadrilon — Сбалансированная"
    calls["active"] = "CLONE-1"
    monkeypatch.setattr(gd_power, "delete_scheme", lambda guid: False)

    rep = gd_power.restore_report({"clone_scheme": "CLONE-1", "original_scheme": ""})

    assert rep["ok"] is True, rep
    assert schemes["CLONE-1"] == "Сбалансированная"
