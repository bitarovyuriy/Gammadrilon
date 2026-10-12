# -*- coding: utf-8 -*-
"""Живая проверка копий схем питания: метка, поиск и уборка.

Создаёт копию активной схемы (без активации), называет её, ищет по метке,
убирает уборщиком. Активная схема не меняется. Это единственная проверка, которая
работает с настоящей схемой питания: остальные (``_tmp_state_guard.py``) подменяют
API и трогают только временный файл состояния.

Запуск: .venv\\Scripts\\python.exe _tmp_power_mark.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import gd_power as p  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(("ок      " if ok else "ПРОВАЛ  ") + name + (f" — {extra}" if extra else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


active = p.get_active_scheme()
check("живьём: активная схема читается", bool(active), repr(active))

# 1. переименование копии: раньше здесь был access violation (чужой argtypes —
#    в размер буфера уезжал мусор), поэтому повторяем несколько раз
renamed = 0
for _ in range(6):
    clone = p.duplicate_scheme(active)
    if p.rename_scheme(clone, p.clone_name(active)) \
            and p.scheme_name(clone).startswith(p.CLONE_MARK):
        renamed += 1
    p.delete_scheme(clone)
check("живьём: копия переименовывается в 6 попытках из 6", renamed == 6,
      f"удачных {renamed}")

# 2. копия находится по метке сразу после создания и убирается уборщиком
clone = p.duplicate_scheme(active)
p.rename_scheme(clone, p.clone_name(active))
found = [c["guid"] for c in p.list_clones()]
check("живьём: свежая копия видна поиску по метке", found == [clone], f"нашли {found}")
check("живьём: метка и имя копии совпадают с ожидаемым",
      p.clone_name(active) == f"{p.CLONE_MARK} — {p.scheme_name(active)}"[:80],
      repr(p.clone_name(active)))

rep = p.cleanup_clones()
check("живьём: уборщик убрал копию и назвал её guid",
      clone in rep["removed"] and not rep["failed"], repr(rep))
check("живьём: после уборки поиск по метке пуст", p.list_clones() == [])
check("живьём: активная схема не менялась", p.get_active_scheme() == active)

# 3. отказы без исключений: чужой guid и активная схема
check("живьём: удаление чужого guid — честный False, без исключений",
      p.delete_scheme("00000000-0000-0000-0000-000000000000") is False)
ok_active, note_active = p.remove_clone(p.get_active_scheme())
check("живьём: активную схему remove_clone не трогает и объясняет почему",
      ok_active is False and note_active == p.i18n.t(
          "копия сейчас активна: Windows не даст её удалить — сначала выберите "
          "другую схему питания"),
      repr(note_active))

print()
print("ИТОГ:", "все проверки пройдены" if not FAILS else f"провалов {len(FAILS)}: {FAILS}")
sys.exit(1 if FAILS else 0)




