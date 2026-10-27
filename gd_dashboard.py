# -*- coding: utf-8 -*-
"""Модель раскладки главного экрана Gammadrilon — без Qt.

Каталог виджетов главной страницы, раскладка по умолчанию, загрузка и
сохранение файла ``dashboard.json``, нормализация любой раскладки и операции
над плитками (переместить, ширина, добавить, убрать, высота строки).

Сетка: страница — строки, в строке ровно ``COLUMNS`` (12) колонок суммарной
ширины плиток. У строки высота S/M/L (104/140/208 px), у плитки своя ширина
от ``min_w`` до ``max_w``. Строка всегда полная: когда плиток в ней становится
больше или меньше, ширины соседей подстраиваются. Любой мусор в файле чинится
``normalize()``, поэтому внутри приложения всегда лежит корректная раскладка.

Никаких импортов PyQt5 — только стандартная библиотека, ``gd_state`` и
``gd_paths``. Модель используется тестами и будущим ``gd_dashboard_ui.py``.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import gd_paths
import gd_state

Layout = dict[str, Any]

COLUMNS = 12
ROW_HEIGHTS = {"S": 104, "M": 140, "L": 208}

#: Каталог виджетов главного экрана. min_w/max_w — в колонках, kind — метрика,
#: действие или список. max_w у всех плиток 12 (весь ряд), min_w — 3..6.
CATALOG: dict[str, dict] = {
    "w.cpu": {"min_w": 4, "max_w": 12, "kind": "metric"},
    "w.gpu": {"min_w": 4, "max_w": 12, "kind": "metric"},
    "w.ram": {"min_w": 3, "max_w": 12, "kind": "metric"},
    "w.power": {"min_w": 3, "max_w": 12, "kind": "action"},
    "w.gpu_limit": {"min_w": 3, "max_w": 12, "kind": "action"},
    "w.gamma": {"min_w": 3, "max_w": 12, "kind": "action"},
    "w.dns": {"min_w": 4, "max_w": 12, "kind": "metric"},
    "w.zapret": {"min_w": 4, "max_w": 12, "kind": "metric"},
    "w.rules": {"min_w": 4, "max_w": 12, "kind": "list"},
    "w.journal": {"min_w": 4, "max_w": 12, "kind": "list"},
}


def default_layout() -> Layout:
    """Раскладка по умолчанию: три строки, сумма ширин в каждой строке — 12."""
    return {
        "version": 1,
        "rows": [
            {"h": "M", "tiles": [
                {"id": "w.cpu", "w": 4},
                {"id": "w.gpu", "w": 4},
                {"id": "w.ram", "w": 4},
            ]},
            {"h": "S", "tiles": [
                {"id": "w.power", "w": 6},
                {"id": "w.dns", "w": 6},
            ]},
            {"h": "M", "tiles": [
                {"id": "w.gamma", "w": 12},
            ]},
        ],
    }


def layout_path() -> Path:
    """Путь к файлу раскладки: ``gd_paths.data_dir()/dashboard.json``."""
    return gd_paths.data_dir() / "dashboard.json"


def load_layout(path: str | Path | None = None) -> Layout:
    """Загрузить раскладку из файла.

    Нет файла — раскладка по умолчанию. Битый JSON — раскладка по умолчанию, и
    битый файл уезжает в ``.bak`` через :func:`gd_state.quarantine`. Чужая
    структура (валидный JSON, но не раскладка) — просто раскладка по
    умолчанию, без карантина.
    """
    path = Path(path or layout_path())
    if not path.exists():
        return default_layout()
    data = gd_state.read_json(path)
    if data is None:
        gd_state.quarantine(path)
        return default_layout()
    if not isinstance(data, dict):
        return default_layout()
    return normalize(data)


def save_layout(layout: Layout, path: str | Path | None = None) -> bool:
    """Сохранить раскладку атомарно через :func:`gd_state.write_json_atomic`.

    Возвращает True, если запись удалась. Перед записью раскладка нормируется,
    так что в файле всегда корректная модель.
    """
    return gd_state.write_json_atomic(path or layout_path(), normalize(layout))


def _clamp_width(tile_id: str, width: Any) -> int:
    """Ширина плитки, зажатая в ``[min_w, max_w]`` из каталога."""
    spec = CATALOG[tile_id]
    try:
        w = int(width)
    except (TypeError, ValueError):
        return spec["min_w"]
    return min(max(w, spec["min_w"]), spec["max_w"])


def _fit_row(tiles: list[dict[str, Any]], spill: list[dict[str, Any]]) -> None:
    """Довести строку ровно до COLUMNS колонок.

    Лишние плитки (с конца строки) уходят в ``spill`` — новую строку внизу.
    Остаток ширины отдаётся оставшимся плиткам по порядку, в пределах max_w.
    """
    while tiles and sum(t["w"] for t in tiles) > COLUMNS:
        spill.append(tiles.pop())
    if tiles:
        gap = COLUMNS - sum(t["w"] for t in tiles)
        for tile in tiles:
            if gap <= 0:
                break
            room = CATALOG[tile["id"]]["max_w"] - tile["w"]
            give = min(gap, room)
            tile["w"] += give
            gap -= give


def normalize(layout: Any) -> Layout:
    """Привести раскладку к канону (вход не мутирует).

    Правила: неизвестные id и дубли выбрасываются, ``w`` зажимается в
    ``[min_w, max_w]``, сумма ширин строки доводится до COLUMNS (остаток —
    плиткам по порядку, лишнее — новая строка внизу), пустые строки удаляются,
    пустая/битая раскладка заменяется раскладкой по умолчанию.
    """
    if not isinstance(layout, dict):
        return default_layout()
    rows_in = layout.get("rows")
    if not isinstance(rows_in, list):
        return default_layout()

    new_rows: list[dict[str, Any]] = []
    spill: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows_in:
        if not isinstance(row, dict):
            continue
        tiles_in = row.get("tiles")
        if not isinstance(tiles_in, list):
            tiles_in = []

        tiles: list[dict[str, Any]] = []
        for tile in tiles_in:
            if not isinstance(tile, dict):
                continue
            tile_id = tile.get("id")
            if not isinstance(tile_id, str) or tile_id not in CATALOG:
                continue  # неизвестный виджет
            if tile_id in seen:
                continue  # дубль
            seen.add(tile_id)
            tiles.append({"id": tile_id, "w": _clamp_width(tile_id, tile.get("w"))})

        _fit_row(tiles, spill)
        if tiles:
            h = row.get("h")
            if h not in ROW_HEIGHTS:
                h = "M"
            new_rows.append({"h": h, "tiles": tiles})

    # Плитки, вышедшие за пределов строк, — свои строки внизу, каждая нормируется.
    while spill:
        row_tiles = list(spill)
        spill = []
        _fit_row(row_tiles, spill)
        if row_tiles:
            new_rows.append({"h": "M", "tiles": row_tiles})

    if not new_rows:
        return default_layout()
    return {"version": 1, "rows": new_rows}


def _find_tile(layout: Layout, tile_id: str) -> tuple[int, int, dict[str, Any], dict[str, Any]] | None:
    """Найти плитку: (индекс строки, индекс плитки, строка, плитка)."""
    rows = layout.get("rows") if isinstance(layout, dict) else None
    if not isinstance(rows, list):
        return None
    for ri, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        tiles = row.get("tiles")
        if not isinstance(tiles, list):
            continue
        for ti, tile in enumerate(tiles):
            if isinstance(tile, dict) and tile.get("id") == tile_id:
                return ri, ti, row, tile
    return None


def move_tile(layout: Layout, tile_id: str, row_index: int, position: int) -> Layout:
    """Переместить плитку в строку ``row_index`` на позицию ``position``.

    Возвращает новую нормализованную раскладку; вход не мутирует.
    Неизвестный id — без изменений.
    """
    layout = copy.deepcopy(layout)
    found = _find_tile(layout, tile_id)
    if not found:
        return normalize(layout)
    _ri, _ti, row, tile = found
    del row["tiles"][_ti]

    rows = layout.get("rows")
    if not isinstance(rows, list) or not rows:
        rows = [{"h": "M", "tiles": []}]
        layout["rows"] = rows
    try:
        r = int(row_index)
    except (TypeError, ValueError):
        r = len(rows) - 1
    r = max(0, min(r, len(rows) - 1))

    target = rows[r]
    if not isinstance(target, dict):
        target = {"h": "M", "tiles": []}
        rows[r] = target
    tiles = target.get("tiles")
    if not isinstance(tiles, list):
        tiles = []
        target["tiles"] = tiles
    try:
        p = int(position)
    except (TypeError, ValueError):
        p = len(tiles)
    p = max(0, min(p, len(tiles)))
    tiles.insert(p, {"id": tile["id"], "w": tile["w"]})
    return normalize(layout)


def set_width(layout: Layout, tile_id: str, width: int) -> Layout:
    """Задать ширину плитки, обменяв колонки с соседями по строке.

    Расширение забирает колонки у плиток справа, затем слева — но не ниже их
    ``min_w``; если соседи отдать не могут, ширина останавливается на том, что
    собралось. Сужение отдаёт освободившиеся колонки им же, чтобы строка
    осталась полной. Вход не мутирует, неизвестный id — без изменений.
    """
    layout = copy.deepcopy(layout)
    found = _find_tile(layout, tile_id)
    if not found:
        return normalize(layout)
    _ri, ti, row, tile = found
    tiles = row["tiles"]
    target = _clamp_width(tile_id, width)
    delta = target - tile["w"]
    if delta == 0:
        return normalize(layout)

    # ближайший сосед справа важнее дальних: ручку тянут именно к нему
    order = [i for i in range(ti + 1, len(tiles))] + [i for i in range(ti - 1, -1, -1)]

    if delta > 0:
        need = delta
        for i in order:
            if need <= 0:
                break
            spec = CATALOG[tiles[i]["id"]]
            take = min(need, tiles[i]["w"] - spec["min_w"])
            if take > 0:
                tiles[i]["w"] -= take
                need -= take
        tile["w"] = target - need
    else:
        need = -delta
        for i in order:
            if need <= 0:
                break
            spec = CATALOG[tiles[i]["id"]]
            give = min(need, spec["max_w"] - tiles[i]["w"])
            if give > 0:
                tiles[i]["w"] += give
                need -= give
        # остаток отдавать некому (плитка одна в строке) — сужение отменяется,
        # иначе normalize растянет её обратно и выйдет скачок на экране
        tile["w"] = target + need
    return normalize(layout)


def add_tile(layout: Layout, tile_id: str, row_index: int | None = None) -> Layout:
    """Добавить плитку каталога шириной ``min_w`` в строку ``row_index``.

    ``None`` — последняя строка. Чужой id или уже стоящая плитка — без
    изменений. Вход не мутирует.
    """
    if tile_id not in CATALOG:
        return normalize(layout) if isinstance(layout, dict) else default_layout()
    layout = copy.deepcopy(layout)
    if _find_tile(layout, tile_id):
        return normalize(layout)

    rows = layout.get("rows") if isinstance(layout, dict) else None
    if not isinstance(rows, list) or not rows:
        rows = [{"h": "M", "tiles": []}]
        layout["rows"] = rows
    if row_index is None:
        r = len(rows) - 1
    else:
        try:
            r = int(row_index)
        except (TypeError, ValueError):
            r = len(rows) - 1
        r = max(0, min(r, len(rows) - 1))

    target = rows[r]
    if not isinstance(target, dict):
        target = {"h": "M", "tiles": []}
        rows[r] = target
    tiles = target.get("tiles")
    if not isinstance(tiles, list):
        tiles = []
        target["tiles"] = tiles
    tiles.append({"id": tile_id, "w": CATALOG[tile_id]["min_w"]})
    return normalize(layout)


def remove_tile(layout: Layout, tile_id: str) -> Layout:
    """Убрать плитку из раскладки.

    Строка нормируется, пустая строка удаляется, пустая раскладка — по
    умолчанию. Вход не мутирует.
    """
    layout = copy.deepcopy(layout)
    rows = layout.get("rows") if isinstance(layout, dict) else None
    if not isinstance(rows, list):
        return default_layout()
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("tiles"), list):
            row["tiles"] = [
                t for t in row["tiles"]
                if not (isinstance(t, dict) and t.get("id") == tile_id)
            ]
    return normalize(layout)


def set_row_height(layout: Layout, row_index: int, height: str) -> Layout:
    """Задать высоту строки (одна из ``ROW_HEIGHTS``).

    Неверная высота или индекс строки — без изменений. Вход не мутирует.
    """
    layout = copy.deepcopy(layout)
    rows = layout.get("rows") if isinstance(layout, dict) else None
    if not isinstance(rows, list):
        return default_layout()
    if height not in ROW_HEIGHTS:
        return normalize(layout)
    try:
        r = int(row_index)
    except (TypeError, ValueError):
        return normalize(layout)
    if r < 0 or r >= len(rows):
        return normalize(layout)
    row = rows[r]
    if not isinstance(row, dict):
        return normalize(layout)
    row["h"] = height
    return normalize(layout)


def tile_ids(layout: Layout) -> list[str]:
    """Идентификаторы плиток в порядке строк: слева направо, сверху вниз."""
    ids: list[str] = []
    rows = layout.get("rows") if isinstance(layout, dict) else None
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("tiles"), list):
                for tile in row["tiles"]:
                    if isinstance(tile, dict) and isinstance(tile.get("id"), str):
                        ids.append(tile["id"])
    return ids
