# -*- coding: utf-8 -*-
"""Живой тест: «лимит остаётся после выхода» в СОБРАННОМ exe (портатив v3.1).

Проверяется та ветка, которой пользуется пользователь: хелпер запускается не как
`gd_gpu_service.py`, а как `Gammadrilon.exe --gpu-service`, и настройку
`gpu_keep_on_exit` читает сам — из `presets.json` рядом с exe (frozen-ветка
`_read_keep_setting`). Что прочитан именно конфиг портатива, видно по шагу 5:
в конфиге портатива написано «откатывать», а в dev-конфиге ключа нет (значит
«не откатывать») — перепутанный файл сразу провалил бы шаг.

Сценарий:
  1. apply 212 Вт (keep_on_exit=true из конфига) → драйвер 212, исходный 300 на диске;
  2. выход приложения: shutdown(restore=True) → лимит ОСТАЛСЯ 212, gpu_state.json жив;
  3. новая сессия: исходный читается из файла (300) → restore → 300, файл убран;
  4. force: неподтверждённое изменение откатывается вопреки настройке;
  5. keep_on_exit=false в конфиге → откат при выходе, как раньше.

Запуск:  .venv\\Scripts\\python.exe _tmp_gpu_exe_keep.py
Нужны: собранный портатив (`dist\\Gammadrilon\\Gammadrilon.exe`), nvidia-smi, тихий UAC.
Конфиг портатива восстанавливается байт-в-байт; хелперы привязаны к PID этого
процесса (`--ui-pid`), поэтому уходят сами, если тест упадёт.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gd_gpu  # noqa: E402
import gd_proc  # noqa: E402

EXE = os.path.join(HERE, "dist", "Gammadrilon", "Gammadrilon.exe")
CFG = Path(HERE) / "dist" / "Gammadrilon" / "presets.json"
STATE = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Gammadrilon" / "gpu_state.json"
LOG = os.path.join(os.environ.get("TEMP", HERE), "gd_gpu_exe_keep.log")
TEST_PL = 212
ORIG_PL = 300

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK ' if ok else 'ФЕЙЛ'} {name}" + (f" · {detail}" if detail else ""), flush=True)
    if not ok:
        fails.append(name)


def pl(wait: float = 1.6):
    """Текущий лимит с карты (через nvidia-smi, без админа)."""
    time.sleep(wait)
    st = gd_gpu.read_state()
    return None if st is None else round(st["pl"])


def state_file():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_keep(on: bool) -> None:
    """Положить в конфиг портатива ровно то, что читает хелпер."""
    CFG.write_text(json.dumps({"settings": {"gpu_keep_on_exit": on}},
                              ensure_ascii=False, indent=4), encoding="utf-8")


def launch(port: int, token: str) -> gd_gpu.GpuClient:
    """Поднять хелпер из exe так же, как это делает окно (Start-Process -Verb RunAs)."""
    session = os.path.join(HERE, "build", f"gpu_exe_session_{port}.json")
    os.makedirs(os.path.dirname(session), exist_ok=True)
    with open(session, "w", encoding="utf-8") as fh:
        json.dump({"port": port, "token": token}, fh)
    tail = f"'--port','{port}','--token','{token}','--ui-pid','{os.getpid()}','--log','{LOG}'"
    cmd = (f"Start-Process -FilePath '{EXE}' -ArgumentList '--gpu-service',{tail} "
           f"-Verb RunAs -WindowStyle Hidden")
    gd_proc.popen(["powershell", "-NoProfile", "-Command", cmd])
    client = gd_gpu.GpuClient(port=port, session_file=session)
    for _ in range(60):                       # UAC + старт exe
        time.sleep(0.5)
        if client.has_session():
            break
    return client


def send(client: gd_gpu.GpuClient, obj: dict, timeout: float = 30.0) -> dict:
    """Команда только в живой хелпер: call() умеет поднимать dev-процесс, а он тут не нужен."""
    return client.call_existing(obj, timeout=timeout)


def main() -> int:
    if not os.path.isfile(EXE):
        print(f"нет собранного exe: {EXE}")
        return 2
    print(f"exe: {EXE} ({os.path.getsize(EXE) / 1024 / 1024:.2f} МБ)")
    backup = CFG.read_bytes() if CFG.exists() else None
    if STATE.exists():
        STATE.unlink()
    print(f"старт: лимит {pl(0.2)} Вт · gpu_state.json {state_file()} · "
          f"конфиг портатива {'есть' if backup else 'нет'}")
    try:
        # 1. применение: keep_on_exit берётся из конфига (в команде apply его нет)
        print("\n[1] apply {0} Вт, keep_on_exit=true в конфиге портатива".format(TEST_PL))
        write_keep(True)
        c1 = launch(47321, "00112233445566778899aabbccddee01")
        check("хелпер exe поднялся (UAC/loopback)", c1.has_session())
        resp = send(c1, {"v": 1, "cmd": "apply", "pl": TEST_PL})
        print(f"  ответ: ok={resp.get('ok')} · {resp.get('error') or 'принято'}")
        check("лимит применён", pl() == TEST_PL, f"{pl(0.2)} Вт")
        check("исходный 300 записан на диск",
              (state_file() or {}).get("original_pl") == ORIG_PL, str(state_file()))

        # 2. выход приложения: лимит обязан остаться
        print("  выход приложения: shutdown(restore=True)")
        resp = send(c1, {"cmd": "shutdown", "restore": True})
        print(f"  ответ: ok={resp.get('ok')}")
        c1._conn = None
        check("лимит пережил выход", pl(1.0) == TEST_PL, f"{pl(0.2)} Вт")
        check("gpu_state.json остался", state_file() is not None, str(state_file()))
        check("хелпер вышел", not c1.has_session())

        # 3. новая сессия: исходный из файла, откат по кнопке
        print("\n[2] новая сессия хелпера (exe): вернуть исходный лимит")
        c2 = launch(47322, "00112233445566778899aabbccddee02")
        check("второй хелпер поднялся", c2.has_session())
        st = send(c2, {"v": 1, "cmd": "status"})
        orig = ((st.get("data") or {}).get("original") or {}).get("pl")
        print(f"  исходный из прошлой сессии: {orig}")
        check("исходный прочитан как 300", orig == ORIG_PL, str(orig))
        resp = send(c2, {"v": 1, "cmd": "restore"})
        print(f"  ответ: ok={resp.get('ok')} · {resp.get('error') or 'возвращено'}")
        check("лимит вернулся к 300", pl() == ORIG_PL, f"{pl(0.2)} Вт")
        check("gpu_state.json убран", state_file() is None, str(state_file()))
        send(c2, {"cmd": "shutdown", "restore": True})
        c2._conn = None

        # 4. неподтверждённое изменение: force сильнее настройки
        print("\n[3] apply {0} Вт → shutdown(restore=True, force=True)".format(TEST_PL))
        write_keep(True)
        c3 = launch(47323, "00112233445566778899aabbccddee03")
        send(c3, {"v": 1, "cmd": "apply", "pl": TEST_PL})
        check("лимит применён", pl() == TEST_PL, f"{pl(0.2)} Вт")
        resp = send(c3, {"cmd": "shutdown", "restore": True, "force": True})
        print(f"  ответ: ok={resp.get('ok')}")
        c3._conn = None
        check("неподтверждённое изменение откатилось", pl(1.0) == ORIG_PL, f"{pl(0.2)} Вт")
        check("gpu_state.json не остался", state_file() is None, str(state_file()))

        # 5. keep_on_exit=false в конфиге портатива: прежнее поведение
        print("\n[4] keep_on_exit=false в конфиге → откат при выходе")
        write_keep(False)
        c4 = launch(47324, "00112233445566778899aabbccddee04")
        send(c4, {"v": 1, "cmd": "apply", "pl": TEST_PL})
        check("лимит применён", pl() == TEST_PL, f"{pl(0.2)} Вт")
        resp = send(c4, {"cmd": "shutdown", "restore": True})
        print(f"  ответ: ok={resp.get('ok')}")
        c4._conn = None
        check("откат при выходе сработал", pl(1.0) == ORIG_PL, f"{pl(0.2)} Вт")
        check("gpu_state.json не остался", state_file() is None, str(state_file()))
    finally:
        # конфиг портатива — байт-в-байт как был (тест не меняет настройки пользователя)
        if backup is None:
            CFG.unlink(missing_ok=True)
        else:
            CFG.write_bytes(backup)
        for p in Path(HERE, "build").glob("gpu_exe_session_*.json"):
            p.unlink(missing_ok=True)

    if os.path.isfile(LOG):
        print("\n-- лог хелперов (последние строки) --")
        with open(LOG, encoding="utf-8", errors="replace") as fh:
            for line in fh.read().splitlines()[-8:]:
                print("  " + line)

    print(f"\nитог: лимит {pl(0.2)} Вт · " +
          ("ВСЁ ОК" if not fails else "ОШИБКИ:\n  - " + "\n  - ".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
