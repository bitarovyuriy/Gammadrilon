# -*- coding: utf-8 -*-
"""ВРЕМЕННАЯ проверка этапа 7 (обход DPI, zapret): ядро, контур winws, вкладка.

Запуск:  .venv\\Scripts\\python.exe _tmp_zapret.py

Без админа и без UAC: привилегированные команды проверяются на фейковой папке
(winws.exe = копия ping.exe), «боевой» winws пользователя не трогается.
Файлы состояния/кэша/лога подменяются на временные — реальные не пишутся.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import sysconfig as _sysconfig

    _site = _sysconfig.get_paths().get("purelib", "")
    _qt_plugins = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                              os.path.join(_qt_plugins, "platforms"))
except Exception:  # noqa: BLE001
    pass

if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from PyQt5.QtWidgets import QApplication  # noqa: E402

import gd_i18n as i18n  # noqa: E402
import gd_zapret  # noqa: E402

app = QApplication([])

FAILS: list[str] = []
LOG: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    line = ("ок      " if ok else "ПРОВАЛ  ") + name + (f" — {extra}" if extra else "")
    print(line, flush=True)
    LOG.append(line)
    if not ok:
        FAILS.append(name)


def pump(ms: int) -> None:
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.02)


REAL = r"D:\za\zapret-discord-youtube-1.10.2"
TMP = Path(tempfile.mkdtemp(prefix="gd_zapret_"))

# фейковая папка zapret: копия ping.exe в роли winws.exe, батник как у zapret
FAKE = TMP / "zapret-test"
(FAKE / "bin").mkdir(parents=True)
(FAKE / "utils").mkdir()
(FAKE / "lists").mkdir()
shutil.copyfile(os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                             "System32", "ping.exe"), FAKE / "bin" / "winws.exe")
(FAKE / "service.bat").write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
(FAKE / "general.bat").write_text(
    "@echo off\r\n"
    "cd /d \"%~dp0\"\r\n"
    "set \"BIN=%~dp0bin\\\"\r\n"
    "cd /d %BIN%\r\n"
    "start \"zapret: test\" /min \"%BIN%winws.exe\" -t 127.0.0.1\r\n",
    encoding="ascii")
(FAKE / "general (TEST).bat").write_text(
    "@echo off\r\n"
    "cd /d \"%~dp0\"\r\n"
    "set \"BIN=%~dp0bin\\\"\r\n"
    "cd /d %BIN%\r\n"
    "start \"zapret: test\" /min \"%BIN%winws.exe\" -t 127.0.0.1\r\n",
    encoding="ascii")

# подмена путей состояния/кэша/лога — реальные файлы пользователя не трогаем
gd_zapret.STATE_FILE = TMP / "zapret_state.json"
gd_zapret.CACHE_FILE = TMP / "zapret_argv.json"
gd_zapret.LOG_FILE = TMP / "zapret_winws.log"

# чужой winws пользователя (если запущен) — не трогаем и не считаем помехой
FOREIGN = [p["pid"] for p in gd_zapret.processes()]

print("=== ядро: путь и стратегии ===", flush=True)
(TMP / "тест-кириллица").mkdir(exist_ok=True)
v = gd_zapret.validate(REAL)
check("ядро: реальная папка валидна без ошибок", not v["errors"], str(v))
check("ядро: кириллица в пути — предупреждение, не запрет",
      gd_zapret.validate(str(TMP / "тест-кириллица"))["warnings"] != [])
check("ядро: пустой путь — ошибка",
      bool(gd_zapret.validate("")["errors"]))
check("ядро: не-папка — ошибка", bool(gd_zapret.validate(str(TMP / "нет-такой"))["errors"]))
check("ядро: папка без winws.exe — ошибка",
      bool(gd_zapret.validate(str(TMP))["errors"]))
gp = gd_zapret.guess_path()
check("ядро: подсказка папки — старшая версия слота ТЗ",
      bool(gp) and "1.10.2" in gp and not gd_zapret.validate(gp)["errors"], str(gp))

sts = gd_zapret.scan_strategies(REAL)
check("ядро: 22 стратегии в папке ТЗ", len(sts) == 22, str(len(sts)))
check("ядро: BASE — первая, EXP — последняя",
      sts[0]["name"] == "BASE" and sts[-1]["name"] == "EXP",
      f"{sts[0]['name']}..{sts[-1]['name']}")
alts = [s["name"] for s in sts if s["group"] == "ALT"]
check("ядро: ALT сортируются численно (ALT, ALT2, … ALT13)",
      alts[:3] == ["ALT", "ALT2", "ALT3"] and alts[-1] == "ALT13", str(alts[:4]))
check("ядро: группы из ТЗ", {s["group"] for s in sts} ==
      {"BASE", "ALT", "FAKE TLS AUTO", "SIMPLE FAKE", "EXP"},
      str({s["group"] for s in sts}))
check("ядро: id стратегии — имя файла без расширения",
      sts[1]["id"] == "general (ALT)" and sts[1]["file"] == "general (ALT).bat",
      f"{sts[1]['id']} / {sts[1]['file']}")

print("=== тумблеры: GameFilter и IPSet (в temp-папке) ===", flush=True)
check("GameFilter: нет файла — off", gd_zapret.game_filter(str(FAKE)) == "off")
ok, mode = gd_zapret.set_game_filter(str(FAKE), True, True)
check("GameFilter: оба тумблера — all", ok and mode == "all"
      and gd_zapret.game_filter(str(FAKE)) == "all", str(mode))
ok, mode = gd_zapret.set_game_filter(str(FAKE), True, False)
check("GameFilter: только TCP — tcp", ok and mode == "tcp"
      and gd_zapret.game_filter(str(FAKE)) == "tcp", str(mode))
ok, mode = gd_zapret.set_game_filter(str(FAKE), False, True)
check("GameFilter: только UDP — udp", ok and mode == "udp"
      and gd_zapret.game_filter(str(FAKE)) == "udp", str(mode))
ok, mode = gd_zapret.set_game_filter(str(FAKE), False, False)
check("GameFilter: оба выключены — файл удалён",
      ok and mode == "off" and gd_zapret.game_filter(str(FAKE)) == "off"
      and not (FAKE / "utils" / "game_filter.enabled").exists(), str(mode))

(TMP / "тест-кириллица").mkdir(exist_ok=True)

(TMP / "тест-кириллица").mkdir(exist_ok=True)

ipset_file = FAKE / "lists" / "ipset-all.txt"
ipset_file.write_text("1.1.1.1/32\n2.2.2.2/32\n8.8.8.8/32\n", encoding="ascii")
info = gd_zapret.ipset_info(str(FAKE))
check("IPSet: список с записями — loaded", info["mode"] == "loaded" and info["lines"] == 3,
      str(info))
ok, msg = gd_zapret.set_ipset(str(FAKE), False)
check("IPSet: выключение — заглушка + backup",
      ok and gd_zapret.ipset_info(str(FAKE))["mode"] == "none"
      and (FAKE / "lists" / "ipset-all.txt.backup").is_file(), msg)
ok, msg = gd_zapret.set_ipset(str(FAKE), True)
check("IPSet: включение — список возвращён из backup",
      ok and gd_zapret.ipset_info(str(FAKE))["mode"] == "loaded"
      and "1.1.1.1/32" in ipset_file.read_text(encoding="ascii"), msg)
# после restore backup «съеден» (ren обратно) — any без копии: честный отказ
ipset_file.write_text("", encoding="ascii")
check("IPSet: пустой файл — any", gd_zapret.ipset_info(str(FAKE))["mode"] == "any")
ok, msg = gd_zapret.set_ipset(str(FAKE), True)
check("IPSet: any без backup — честный отказ, без выдумок", not ok, msg)
# а с копией — восстановление
(FAKE / "lists" / "ipset-all.txt.backup").write_text("1.1.1.1/32\n", encoding="ascii")
ok, msg = gd_zapret.set_ipset(str(FAKE), True)
check("IPSet: any + backup — восстановление",
      ok and gd_zapret.ipset_info(str(FAKE))["mode"] == "loaded"
      and "1.1.1.1/32" in ipset_file.read_text(encoding="ascii"), msg)

print("=== кэш argv и состояние ===", flush=True)
check("кэш: пусто у нового пути", gd_zapret.cache_get(str(FAKE), "general (TEST)") is None)
gd_zapret.cache_put(str(FAKE), "general (TEST)", ["-t", "127.0.0.1"])
check("кэш: круг put→get", gd_zapret.cache_get(str(FAKE), "general (TEST)") == ["-t", "127.0.0.1"])
st = (FAKE / "bin" / "winws.exe").stat()
os.utime(FAKE / "bin" / "winws.exe", (st.st_atime, st.st_mtime + 10))  # смена версии
check("кэш: смена winws.exe инвалидирует argv",
      gd_zapret.cache_get(str(FAKE), "general (TEST)") is None)
gd_zapret.cache_put(str(FAKE), "general (TEST)", ["-t", "127.0.0.1"])
gd_zapret.cache_drop(str(FAKE), "general (TEST)")
check("кэш: drop убирает запись", gd_zapret.cache_get(str(FAKE), "general (TEST)") is None)

gd_zapret.save_our_state(str(FAKE), "general (TEST)", 4242, ["-t", "127.0.0.1"])
st = gd_zapret.our_state()
check("состояние: круг save→our_state",
      st and st["pid"] == 4242 and st["strategy"] == "general (TEST)", str(st))
gd_zapret.clear_our_state()
check("состояние: clear убирает файл", gd_zapret.our_state() is None)

gd_zapret.LOG_FILE.write_text("\n".join(f"строка {i}" for i in range(50)), encoding="utf-8")
tail = gd_zapret.log_tail(5)
check("лог: хвост — последние строки", tail == [f"строка {i}" for i in range(45, 50)],
      str(tail))

print("=== процессы и службы ===", flush=True)
check("служба: несуществующая — None", gd_zapret.service_state("nosuchsvc-xyz") is None)
sc_wd = gd_zapret.service_state("WinDivert")
check("служба: WinDivert читается (STOPPED/RUNNING/None)",
      sc_wd in (None, "STOPPED", "RUNNING", "START_PENDING", "STOP_PENDING"), str(sc_wd))
check("процессы: чужой winws пользователя виден, наш pid не считается winws",
      all(p["pid"] in FOREIGN for p in gd_zapret.processes()) and
      not gd_zapret.pid_alive(os.getpid()), str(FOREIGN))

print("=== контур winws: освоение и запуск (фейковый winws, без админа) ===", flush=True)
_real_processes = gd_zapret.processes


def _processes_without_foreign():
    """Для старта: чужой winws пользователя не считаем конфликтом в этом тесте."""
    return [p for p in _real_processes() if p["pid"] not in FOREIGN]


# 1) освоение фейковой стратегии
gd_zapret.processes = _processes_without_foreign
ok, res = gd_zapret.recon(str(FAKE), "general (TEST).bat", timeout=12)
check("освоение: argv пойман у winws.exe", ok and res.get("argv") == ["-t", "127.0.0.1"],
      str(res))
check("освоение: разведочный winws убит по PID",
      ok and not gd_zapret.pid_alive(res.get("pid")), str(res.get("pid")))
check("освоение: чужой winws пользователя не тронут",
      all(gd_zapret.pid_alive(pid) for pid in FOREIGN), str(FOREIGN))

# 2) запуск прямым CreateProcess с освоенным argv
ok, res = gd_zapret.start_winws(str(FAKE), ["-t", "127.0.0.1"],
                                log_file=str(gd_zapret.LOG_FILE), note="TEST")
check("запуск: winws.exe поднят напрямую (PID)", ok and res.get("pid"), str(res))
pid = res.get("pid") if ok else 0
check("запуск: процесс жив через 1.2 с", ok and gd_zapret.pid_alive(pid), str(pid))
check("запуск: в лог записан заголовок запуска",
      "запуск: TEST" in gd_zapret.LOG_FILE.read_text(encoding="utf-8", errors="replace"))
ok2, msg = gd_zapret.start_winws(str(FAKE), ["-t", "127.0.0.1"])
check("запуск: поверх работающего — честный отказ", not ok2, msg)

# 3) остановка строго по PID и только winws.exe
ok3, msg = gd_zapret.stop_winws(os.getpid())
check("остановка: не-winws PID не убивается", not ok3, msg)
ok3, msg = gd_zapret.stop_winws(pid)
check("остановка: наш winws остановлен по PID",
      ok3 and not gd_zapret.pid_alive(pid), msg)
ok3, msg = gd_zapret.stop_winws(pid)
check("остановка: повторная — «уже завершён», без ошибки", ok3, msg)
gd_zapret.processes = _real_processes

# 4) защита от подмены имени стратегии (путь/чужой файл)
ok, msg = gd_zapret.recon(str(FAKE), "..\\evil.bat")
check("освоение: обход пути запрещён", not ok, str(msg))
ok, msg = gd_zapret.recon(str(FAKE), "service.bat")
check("освоение: не-general*.bat запрещён", not ok, str(msg))
ok, msg = gd_zapret.recon(str(FAKE), "general (TEST).bat", timeout=8)
# батник стартует winws мгновенно; проверяем, что повторное освоение не ломается
check("освоение: повторное освоение работает (кэш не мешает)", ok, str(msg))

print("=== хелпер: команды zapret через handle() (без elevation) ===", flush=True)
import gd_gpu_service  # noqa: E402

resp = gd_gpu_service.handle({"cmd": "zapret_stop", "pid": os.getpid()})
check("хелпер: zapret_stop не-winws — честный отказ", not resp.get("ok"), str(resp))
resp = gd_gpu_service.handle({"cmd": "zapret_recon", "path": str(FAKE),
                              "strategy": "nope.bat"})
check("хелпер: zapret_recon с плохим именем — отказ", not resp.get("ok"), str(resp))
resp = gd_gpu_service.handle({"cmd": "zapret_start", "path": str(TMP / "нет"),
                              "argv": ["-t", "127.0.0.1"]})
check("хелпер: zapret_start с плохим путём — отказ", not resp.get("ok"), str(resp))
resp = gd_gpu_service.handle({"cmd": "zapret_adopt", "pid": os.getpid()})
check("хелпер: zapret_adopt не-winws — отказ", not resp.get("ok"), str(resp))



print("=== вкладка: снимок, конфликт, тумблеры (offscreen) ===", flush=True)
from PyQt5.QtCore import Qt  # noqa: E402

from gd_zapret_ui import ZapretWidget  # noqa: E402

w = ZapretWidget(standalone=True, host=None)
w._path_manual = str(REAL)
w._on_snap(w._snap_job())
check("вкладка: путь показан", w.lbl_path.text() == str(REAL), w.lbl_path.text())
check("вкладка: список стратегий (22 + 5 заголовков групп)",
      w.list.count() == 27, str(w.list.count()))
if FOREIGN:
    check("вкладка: внешний winws показан честно",
          "ВНЕШНИЙ winws.exe" in w.lbl_state.text(), w.lbl_state.text())
    check("вкладка: блок конфликта виден", w.lbl_conflict.isVisibleTo(w))
    check("вкладка: «Включить» заблокирована при конфликте", not w.btn_on.isEnabled())
    w._ignore_foreign()
    check("вкладка: «Оставить как есть» снимает блок",
          not w.lbl_conflict.isVisibleTo(w))
    check("вкладка: включение недоступно, пока чужой winws работает",
          not w.btn_on.isEnabled() and not w.btn_recon.isEnabled())
else:
    check("вкладка: внешних нет — «ВЫКЛЮЧЕНО»",
          "ВЫКЛЮЧЕНО" in w.lbl_state.text(), w.lbl_state.text())

for i in range(w.list.count()):
    it = w.list.item(i)
    if it.data(Qt.UserRole):
        w.list.setCurrentItem(it)
        break
check("вкладка: выбор стратегии — подпись с именем файла",
      "файл:" in w.lbl_sel.text(), w.lbl_sel.text())
check("вкладка: «Переосвоить» доступна ровно когда нет чужого winws",
      w.btn_recon.isEnabled() == (not FOREIGN), str(w.btn_recon.isEnabled()))

# тумблеры пишут в temp-папку, а не в боевую
w._path_manual = str(FAKE)
w._on_snap(w._snap_job())
w.chk_tcp.setChecked(True)
w._on_toggle_gf()
check("вкладка: тумблер TCP записал game_filter=tcp",
      gd_zapret.game_filter(str(FAKE)) == "tcp", gd_zapret.game_filter(str(FAKE)))
w.chk_udp.setChecked(True)
w._on_toggle_gf()
check("вкладка: TCP+UDP → all", gd_zapret.game_filter(str(FAKE)) == "all")
pump(300)
check("вкладка: галочки отражают файл (tcp+udp)",
      w.chk_tcp.isChecked() and w.chk_udp.isChecked())
w.chk_tcp.setChecked(False)
w.chk_udp.setChecked(False)
w._on_toggle_gf()
check("вкладка: снятие обоих → off (файл удалён)",
      gd_zapret.game_filter(str(FAKE)) == "off")
pump(200)
check("вкладка: журнал winws показан", "запуск: TEST" in w.txt_log.toPlainText(),
      w.txt_log.toPlainText()[-80:])
w.deactivate()
w.close()

print("=== окно: вкладка в сайдбаре и ленивая активация ===", flush=True)
import main as gd_main  # noqa: E402

app.setStyleSheet(gd_main.build_app_stylesheet())
win = gd_main.MainWindow()
win.tray.hide()
check("окно: tab.zapret есть в стеке и сайдбаре",
      "tab.zapret" in win._key_to_index and "tab.zapret" in win._nav_items)
check("окно: индекс вкладки — 6 (жёсткий индекс палитры)",
      win._key_to_index.get("tab.zapret") == 6, str(win._key_to_index.get("tab.zapret")))
check("окно: имя вкладки — «Обход DPI»", i18n.t("tab.zapret") == "Обход DPI")
zpage = win._zapret_page
check("окно: до открытия вкладки опрос выключен", not zpage._poll._timer.isActive())
win.tabs.setCurrentIndex(win._key_to_index["tab.zapret"])
pump(800)
check("окно: открытие вкладки включает опрос", zpage._poll._timer.isActive())
check("окно: снимок отработал", zpage.lbl_state.text() not in ("", "—"),
      zpage.lbl_state.text())
win.tabs.setCurrentIndex(win._key_to_index["tab.gamma"])
pump(300)
check("окно: уход с вкладки выключает опрос", not zpage._poll._timer.isActive())

try:
    win.tray.hide()
    import gd_metrics

    gd_metrics.stop()
    import gd_bg

    gd_bg.wait_all(1500)
except Exception:  # noqa: BLE001
    pass

shutil.rmtree(TMP, ignore_errors=True)
print((f"ВСЕ ОК" if not FAILS else "ПРОВАЛЫ: " + "; ".join(FAILS)), flush=True)
with open(os.path.join(HERE, "build", "shots", "zapret.log"), "w",
          encoding="utf-8") as fh:
    fh.write("\n".join(LOG) + "\n")
print("RESULT: " + ("OK" if not FAILS else f"FAIL ({len(FAILS)})"), flush=True)
sys.exit(1 if FAILS else 0)
