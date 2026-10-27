# -*- coding: utf-8 -*-
"""Модель раскладки главного экрана (gd_dashboard) — тесты без Qt и сети."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gd_dashboard  # noqa: E402 — путь проекта добавлен выше
import gd_paths  # noqa: E402 — путь проекта добавлен выше


def _row_sum(row) -> int:
    return sum(tile["w"] for tile in row["tiles"])


def _all_rows_sum(layout) -> bool:
    return all(_row_sum(row) == gd_dashboard.COLUMNS for row in layout["rows"])


def test_default_layout_is_valid():
    layout = gd_dashboard.default_layout()
    assert layout["version"] == 1
    assert len(layout["rows"]) == 3
    for row in layout["rows"]:
        assert row["h"] in gd_dashboard.ROW_HEIGHTS
        assert _row_sum(row) == gd_dashboard.COLUMNS
        for tile in row["tiles"]:
            assert tile["id"] in gd_dashboard.CATALOG
            spec = gd_dashboard.CATALOG[tile["id"]]
            assert spec["min_w"] <= tile["w"] <= spec["max_w"]


def test_default_layout_ids_match_catalog():
    assert gd_dashboard.tile_ids(gd_dashboard.default_layout()) == [
        "w.cpu", "w.gpu", "w.ram", "w.power", "w.dns", "w.zapret",
    ]


def test_layout_path_points_to_data_dir():
    assert isinstance(gd_dashboard.layout_path(), Path)
    assert gd_dashboard.layout_path() == gd_paths.data_dir() / "dashboard.json"


def test_load_layout_missing_file(tmp_path):
    assert gd_dashboard.load_layout(tmp_path / "nope.json") == gd_dashboard.default_layout()
    assert not (tmp_path / "nope.json.bak").exists()


def test_load_layout_broken_json_quarantined(tmp_path):
    path = tmp_path / "dashboard.json"
    path.write_text("{not valid json", encoding="utf-8")
    layout = gd_dashboard.load_layout(path)
    assert layout == gd_dashboard.default_layout()
    assert not path.exists()
    assert (tmp_path / "dashboard.json.bak").exists()


def test_load_layout_foreign_shape_is_default_without_quarantine(tmp_path):
    path = tmp_path / "dashboard.json"
    path.write_text('{"rows": "not a list"}', encoding="utf-8")
    assert gd_dashboard.load_layout(path) == gd_dashboard.default_layout()
    assert not (tmp_path / "dashboard.json.bak").exists()


def test_load_layout_wrong_json_type(tmp_path):
    path = tmp_path / "dashboard.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert gd_dashboard.load_layout(path) == gd_dashboard.default_layout()
    assert not (tmp_path / "dashboard.json.bak").exists()


def test_save_load_round_trip(tmp_path):
    layout = gd_dashboard.default_layout()
    path = tmp_path / "dashboard.json"
    assert gd_dashboard.save_layout(layout, path) is True
    loaded = gd_dashboard.load_layout(path)
    assert loaded == layout
    assert _all_rows_sum(loaded)


def test_save_layout_creates_parent_directory(tmp_path):
    path = tmp_path / "sub" / "dir" / "dashboard.json"
    assert gd_dashboard.save_layout(gd_dashboard.default_layout(), path) is True
    assert gd_dashboard.load_layout(path) == gd_dashboard.default_layout()


def test_normalize_drops_unknown_ids_and_duplicates():
    layout = {
        "version": 1,
        "rows": [
            {"h": "M", "tiles": [
                {"id": "w.cpu", "w": 4},
                {"id": "w.cpu", "w": 4},
                {"id": "w.nope", "w": 4},
                {"id": "w.gpu", "w": 4},
            ]},
        ],
    }
    before = copy.deepcopy(layout)
    norm = gd_dashboard.normalize(layout)
    assert gd_dashboard.tile_ids(norm) == ["w.cpu", "w.gpu"]
    assert _all_rows_sum(norm)
    # вход не мутирован
    assert layout == before


def test_normalize_clamps_width_to_max():
    layout = {"version": 1, "rows": [{"h": "M", "tiles": [{"id": "w.cpu", "w": 99}]}]}
    norm = gd_dashboard.normalize(layout)
    assert norm["rows"][0]["tiles"][0]["w"] == gd_dashboard.CATALOG["w.cpu"]["max_w"]
    assert _all_rows_sum(norm)


def test_normalize_clamps_width_to_min():
    layout = {"version": 1, "rows": [{"h": "M", "tiles": [
        {"id": "w.cpu", "w": 1},
        {"id": "w.gpu", "w": 4},
    ]}]}
    norm = gd_dashboard.normalize(layout)
    tiles = norm["rows"][0]["tiles"]
    assert tiles[0]["w"] == 8  # 1 зажат в min_w=4, потом забрал остаток ширины
    assert tiles[1]["w"] == 4
    assert _all_rows_sum(norm)
    # гарантия зажима: никакая плитка не вылезла за [min_w, max_w]
    for tile in tiles:
        spec = gd_dashboard.CATALOG[tile["id"]]
        assert spec["min_w"] <= tile["w"] <= spec["max_w"]


def test_normalize_minimal_tiles_row_grows_to_columns():
    layout = {"version": 1, "rows": [{"h": "S", "tiles": [
        {"id": "w.power", "w": 3},
        {"id": "w.gpu_limit", "w": 3},
    ]}]}
    row = gd_dashboard.normalize(layout)["rows"][0]
    assert _row_sum(row) == gd_dashboard.COLUMNS
    # остаток отдаётся плиткам по порядку — первой до max_w
    assert row["tiles"][0]["w"] == 9
    assert row["tiles"][1]["w"] == 3


def test_normalize_excess_moves_to_new_row():
    layout = {"version": 1, "rows": [{"h": "M", "tiles": [
        {"id": "w.cpu", "w": 12},
        {"id": "w.gpu", "w": 12},
    ]}]}
    norm = gd_dashboard.normalize(layout)
    assert [t["id"] for t in norm["rows"][0]["tiles"]] == ["w.cpu"]
    assert norm["rows"][0]["tiles"][0]["w"] == 12
    assert norm["rows"][1]["tiles"][0]["id"] == "w.gpu"
    assert norm["rows"][1]["tiles"][0]["w"] == 12
    assert _all_rows_sum(norm)


def test_normalize_empty_layout_is_default():
    assert gd_dashboard.normalize({}) == gd_dashboard.default_layout()
    assert gd_dashboard.normalize({"rows": []}) == gd_dashboard.default_layout()
    assert gd_dashboard.normalize(None) == gd_dashboard.default_layout()
    assert gd_dashboard.normalize("не раскладка") == gd_dashboard.default_layout()


def test_normalize_drops_empty_rows():
    layout = {
        "version": 1,
        "rows": [
            {"h": "M", "tiles": [{"id": "w.cpu", "w": 12}]},
            {"h": "S", "tiles": []},
            {"h": "M", "tiles": [{"id": "w.gamma", "w": 12}]},
        ],
    }
    norm = gd_dashboard.normalize(layout)
    assert [row["h"] for row in norm["rows"]] == ["M", "M"]
    assert _all_rows_sum(norm)


def test_normalize_repairs_bad_row_height():
    layout = {"version": 1, "rows": [{"h": "XL", "tiles": [{"id": "w.cpu", "w": 12}]}]}
    norm = gd_dashboard.normalize(layout)
    assert norm["rows"][0]["h"] == "M"


def test_move_tile_within_row():
    layout = gd_dashboard.default_layout()
    moved = gd_dashboard.move_tile(layout, "w.cpu", 0, 2)
    assert [t["id"] for t in moved["rows"][0]["tiles"]] == ["w.gpu", "w.ram", "w.cpu"]
    assert _all_rows_sum(moved)
    # вход не мутирован
    assert gd_dashboard.tile_ids(layout) == gd_dashboard.tile_ids(gd_dashboard.default_layout())


def test_move_tile_between_rows():
    layout = gd_dashboard.default_layout()
    moved = gd_dashboard.move_tile(layout, "w.power", 0, 0)
    assert "w.power" in [t["id"] for t in moved["rows"][0]["tiles"]]
    assert "w.power" not in [t["id"] for row in moved["rows"][1:] for t in row["tiles"]]
    assert sorted(gd_dashboard.tile_ids(moved)) == sorted(gd_dashboard.tile_ids(layout))
    assert _all_rows_sum(moved)


def test_move_tile_unknown_id_is_noop():
    layout = gd_dashboard.default_layout()
    moved = gd_dashboard.move_tile(layout, "w.nope", 0, 0)
    assert moved == gd_dashboard.normalize(layout)
    assert gd_dashboard.tile_ids(moved) == gd_dashboard.tile_ids(layout)


def test_set_width_takes_columns_from_right_neighbour():
    layout = {
        "version": 1,
        "rows": [{"h": "M", "tiles": [{"id": "w.cpu", "w": 6}, {"id": "w.ram", "w": 6}]}],
    }
    widened = gd_dashboard.set_width(layout, "w.cpu", 8)
    assert [t["w"] for t in widened["rows"][0]["tiles"]] == [8, 4]
    assert _row_sum(widened["rows"][0]) == gd_dashboard.COLUMNS
    # вход не мутирован
    assert [t["w"] for t in layout["rows"][0]["tiles"]] == [6, 6]


def test_set_width_stops_at_neighbour_minimum():
    layout = gd_dashboard.default_layout()
    # gpu стоит на своём минимуме (4) и не отдаёт, ram отдаёт одну колонку
    wide = gd_dashboard.set_width(layout, "w.cpu", 12)
    row0 = wide["rows"][0]
    assert [t["w"] for t in row0["tiles"]] == [5, 4, 3]
    assert _row_sum(row0) == gd_dashboard.COLUMNS
    assert _all_rows_sum(wide)


def test_set_width_shrinks_and_gives_columns_to_neighbour():
    layout = {
        "version": 1,
        "rows": [{"h": "M", "tiles": [{"id": "w.cpu", "w": 9}, {"id": "w.ram", "w": 3}]}],
    }
    narrow = gd_dashboard.set_width(layout, "w.cpu", 6)
    assert [t["w"] for t in narrow["rows"][0]["tiles"]] == [6, 6]
    assert _row_sum(narrow["rows"][0]) == gd_dashboard.COLUMNS


def test_set_width_clamps_to_minimum():
    layout = {
        "version": 1,
        "rows": [{"h": "M", "tiles": [{"id": "w.cpu", "w": 9}, {"id": "w.ram", "w": 3}]}],
    }
    narrow = gd_dashboard.set_width(layout, "w.cpu", 1)
    assert narrow["rows"][0]["tiles"][0]["w"] == gd_dashboard.CATALOG["w.cpu"]["min_w"]
    assert _all_rows_sum(narrow)


def test_set_width_solo_tile_cannot_shrink():
    layout = {
        "version": 1,
        "rows": [{"h": "M", "tiles": [{"id": "w.gamma", "w": 12}]}],
    }
    # соседей нет — отдавать колонки некому, ширина остаётся полной
    same = gd_dashboard.set_width(layout, "w.gamma", 6)
    assert same["rows"][0]["tiles"][0]["w"] == 12
    assert _all_rows_sum(same)


def test_set_width_unknown_id_is_noop():
    layout = gd_dashboard.default_layout()
    changed = gd_dashboard.set_width(layout, "w.nope", 5)
    assert changed == gd_dashboard.normalize(layout)


def test_add_tile_unknown_id_is_noop():
    layout = gd_dashboard.default_layout()
    changed = gd_dashboard.add_tile(layout, "w.nope", 0)
    assert changed == gd_dashboard.normalize(layout)


def test_add_tile_duplicate_is_noop():
    layout = gd_dashboard.default_layout()
    changed = gd_dashboard.add_tile(layout, "w.cpu", 0)
    assert changed == gd_dashboard.normalize(layout)


def test_add_tile_appears_and_rebalances():
    layout = gd_dashboard.default_layout()
    added = gd_dashboard.add_tile(layout, "w.rules", 0)
    ids = gd_dashboard.tile_ids(added)
    assert ids.count("w.rules") == 1
    assert _all_rows_sum(added)
    # строка 0 переполнилась, лишнее ушло в новую строку внизу
    assert len(added["rows"]) == len(layout["rows"]) + 1


def test_remove_tile():
    layout = gd_dashboard.default_layout()
    removed = gd_dashboard.remove_tile(layout, "w.dns")
    assert gd_dashboard.tile_ids(removed) == [
        "w.cpu", "w.gpu", "w.ram", "w.power", "w.zapret",
    ]
    row1 = removed["rows"][1]
    assert row1["tiles"] == [{"id": "w.power", "w": 12}]
    assert _all_rows_sum(removed)
    # вход не мутирован
    assert gd_dashboard.tile_ids(layout) == gd_dashboard.tile_ids(gd_dashboard.default_layout())


def test_remove_last_tile_gives_default():
    layout = {"version": 1, "rows": [{"h": "M", "tiles": [{"id": "w.cpu", "w": 12}]}]}
    assert gd_dashboard.remove_tile(layout, "w.cpu") == gd_dashboard.default_layout()


def test_set_row_height_valid():
    layout = gd_dashboard.default_layout()
    changed = gd_dashboard.set_row_height(layout, 0, "S")
    assert changed["rows"][0]["h"] == "S"
    assert changed["rows"][1]["h"] == "S"  # вторая строка и так S — не тронута
    assert _all_rows_sum(changed)


def test_set_row_height_invalid_height_is_noop():
    layout = gd_dashboard.default_layout()
    unchanged = gd_dashboard.set_row_height(layout, 0, "XL")
    assert unchanged == gd_dashboard.normalize(layout)
    unchanged = gd_dashboard.set_row_height(layout, 99, "S")
    assert unchanged == gd_dashboard.normalize(layout)


def test_mutation_functions_do_not_mutate_input():
    layout = gd_dashboard.default_layout()
    before = copy.deepcopy(layout)
    gd_dashboard.move_tile(layout, "w.cpu", 0, 1)
    gd_dashboard.set_width(layout, "w.gpu", 5)
    gd_dashboard.add_tile(layout, "w.power", 0)
    gd_dashboard.remove_tile(layout, "w.ram")
    gd_dashboard.set_row_height(layout, 0, "S")
    gd_dashboard.normalize(layout)
    assert layout == before
