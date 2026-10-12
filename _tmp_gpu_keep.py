# -*- coding: utf-8 -*-
"""Живой тест: лимит GPU переживает выход приложения, если так решено.

Проверяем на настоящей карте четыре сценария (ТЗ 4.3 + выбранная семантика):
  1. keep=True  → shutdown(restore=True) НЕ откатывает: лимит остался, исходное
     значение легло в gpu_state.json;
  2. новая сессия читает исходное из файла и кнопка «Вернуть как было» возвращает
     именно 300 Вт (а не «текущее» 212), файл при этом исчезает;
  3. force=True → неподтверждённое изменение откатывается вопреки настройке;
  4. keep=False → откат при выходе, как раньше.

Запуск: python _tmp_gpu_keep.py  (нужен рабочий nvidia-smi и тихий UAC)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gd_gpu  # noqa: E402

STATE = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Gammadrilon" / "gpu_state.json"
TEST_PL = 212


def pl(wait: float = 1.6):
    time.sleep(wait)
    st = gd_gpu.read_state()
    return None if st is None else st["pl"]


def state_file():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None


def show(tag: str) -> None:
    print(f"  {tag}: лимит {pl():.0f} Вт · gpu_state.json {state_file()}")


def main() -> int:
    fails = []
    if STATE.exists():
        STATE.unlink()
    print(f"старт: лимит {pl(0.2):.0f} Вт, gpu_state.json {state_file()}")

    # 1. применение с сохранением после выхода
    print("\n[1] apply {0} Вт, keep_on_exit=True".format(TEST_PL))
    client = gd_gpu.GpuClient()
    resp = client.call({"v": 1, "cmd": "apply", "pl": TEST_PL, "keep_on_exit": True})
    print(f"  ответ хелпера: ok={resp.get('ok')} · {resp.get('error') or 'принято'}")
    if pl() != TEST_PL:
        fails.append("шаг 1: лимит не применён")
    show("после apply")
    if (state_file() or {}).get("original_pl") != 300:
        fails.append("шаг 1: исходный лимит не сохранён на диск")

    print("  выход приложения (shutdown restore=True, force=False)")
    client.shutdown_async(restore=True)
    show("после выхода")
    if pl(0.2) != TEST_PL:
        fails.append("шаг 1: лимит не пережил выход приложения")
    if state_file() is None:
        fails.append("шаг 1: gpu_state.json исчез")

    # 2. новая сессия: возврат исходного из файла
    print("\n[2] новая сессия хелпера: вернуть исходный лимит")
    client2 = gd_gpu.GpuClient()
    st = client2.call({"v": 1, "cmd": "status"})
    orig = ((st.get("data") or {}).get("original") or {}).get("pl")
    print(f"  исходный из прошлой сессии: {orig}")
    if orig != 300:
        fails.append(f"шаг 2: исходный прочитан как {orig}, а не 300")
    resp = client2.call({"v": 1, "cmd": "restore"})
    print(f"  ответ хелпера: ok={resp.get('ok')} · {resp.get('error') or 'возвращено'}")
    show("после restore")
    if pl(0.2) != 300:
        fails.append("шаг 2: исходный лимит не возвращён")
    if state_file() is not None:
        fails.append("шаг 2: gpu_state.json не удалён после отката")

    # 3. неподтверждённое изменение: force перебивает настройку
    print("\n[3] apply {0} Вт, keep_on_exit=True → shutdown(force=True)".format(TEST_PL))
    client3 = gd_gpu.GpuClient()
    client3.call({"v": 1, "cmd": "apply", "pl": TEST_PL, "keep_on_exit": True})
    if pl() != TEST_PL:
        fails.append("шаг 3: лимит не применён")
    client3.shutdown_async(restore=True, force=True)
    show("после выхода с force")
    if pl(0.2) != 300:
        fails.append("шаг 3: неподтверждённое изменение не откатилось")
    if state_file() is not None:
        fails.append("шаг 3: gpu_state.json остался после force-отката")

    # 4. keep=False: откат при выходе (прежнее поведение)
    print("\n[4] apply {0} Вт, keep_on_exit=False → shutdown".format(TEST_PL))
    client4 = gd_gpu.GpuClient()
    client4.call({"v": 1, "cmd": "apply", "pl": TEST_PL, "keep_on_exit": False})
    if pl() != TEST_PL:
        fails.append("шаг 4: лимит не применён")
    client4.shutdown_async(restore=True)
    show("после выхода")
    if pl(0.2) != 300:
        fails.append("шаг 4: откат при выходе не сработал")

    print("\nитог: " + ("ВСЁ ОК" if not fails else "ОШИБКИ:\n  - " + "\n  - ".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
