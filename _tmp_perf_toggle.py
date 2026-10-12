# -*- coding: utf-8 -*-
"""ВРЕМЕННАЯ проверка переключателя производительности (горячая клавиша).

Проверяем без UAC и без записи в драйвер (страницы CPU/GPU подменены заглушками):
  1. нажатие клавиши применяет пару профилей «ограничение» и переключает
     состояние; повторное нажатие — пару «производительность»;
  2. пустой профиль в блоке = «этот блок не трогаем»; пустая пара — ничего не
     делаем и состояние не переключаем;
  3. неизвестный профиль — честная ошибка в отчёте, вторая половина применяется;
  4. звук переключателя: низкие тона вниз при включении ограничений, высокие
     вверх — при снятии (winsound подменён заглушкой);
  5. диалог «Глобальные клавиши»: клавиша perf_toggle в списке, пара профилей
     и звук читаются/пишутся;
  6. HotkeyManager регистрирует perf_toggle и эмитит сигнал perfToggle;
  7. трей: пункт переключателя с галкой, клик эмитит сигнал, галка переживает
     смену языка;
  8. палитра команд: команда переключателя в списке и зовёт окно.

Запуск:  .venv\\Scripts\\python.exe _tmp_perf_toggle.py
"""
from __future__ import annotations

import os
import sys
import time
import types

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

app = QApplication([])

import gd_perf  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(("ок      " if ok else "ПРОВАЛ  ") + name + (f" — {extra}" if extra else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


def pump(ms: int) -> None:
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.02)


class _FakePage:
    """Заглушка страницы CPU/GPU: пишет вызовы apply_profile."""

    def __init__(self):
        self.calls: list[tuple] = []

    def apply_profile(self, pid, source=None, on_done=None):
        self.calls.append((pid, source))


def make_config(limited: dict, boost: dict, state: bool = False) -> dict:
    return {"settings": {"perf_profiles": {"limited": limited, "boost": boost},
                         "perf_limited": state},
            "presets": []}


# --- 1. переключение пары профилей -------------------------------------------
power, gpu = _FakePage(), _FakePage()
config = make_config({"cpu": "eco", "gpu": "quiet"},
                     {"cpu": "normal", "gpu": "default"})

report = gd_perf.toggle(config, power, gpu)
check("нажатие 1: CPU → eco, GPU → quiet",
      power.calls == [("eco", "горячая клавиша")] and gpu.calls == [("quiet", "горячая клавиша")],
      f"{power.calls} / {gpu.calls}")
check("нажатие 1: состояние — ограничения включены",
      report["ok"] is True and report["limited"] is True
      and config["settings"]["perf_limited"] is True, str(report))

power.calls.clear()
gpu.calls.clear()
report2 = gd_perf.toggle(config, power, gpu)
check("нажатие 2: CPU → normal, GPU → default",
      power.calls == [("normal", "горячая клавиша")] and gpu.calls == [("default", "горячая клавиша")],
      f"{power.calls} / {gpu.calls}")
check("нажатие 2: состояние — ограничения сняты",
      report2["ok"] is True and report2["limited"] is False
      and config["settings"]["perf_limited"] is False, str(report2))
check("цель следующего нажатия — снова ограничение",
      gd_perf.target_state(True) == "boost" and gd_perf.target_state(False) == "limited")

# --- 2. пустые профили: «не трогать блок» ------------------------------------
power.calls.clear()
gpu.calls.clear()
config_empty = make_config({"cpu": "", "gpu": ""}, {"cpu": "", "gpu": ""})
r_empty = gd_perf.toggle(config_empty, power, gpu)
check("пустая пара: ничего не делаем и не переключаем",
      r_empty["ok"] is False and not power.calls and not gpu.calls
      and config_empty["settings"]["perf_limited"] is False, str(r_empty))

config_gpu_only = make_config({"cpu": "", "gpu": "quiet"}, {"cpu": "", "gpu": "default"})
r_gpu = gd_perf.toggle(config_gpu_only, power, gpu)
check("пустой CPU: трогаем только GPU, состояние переключено",
      r_gpu["ok"] is True and not power.calls
      and gpu.calls == [("quiet", "горячая клавиша")]
      and config_gpu_only["settings"]["perf_limited"] is True,
      f"{power.calls} / {gpu.calls} / {r_gpu}")

# --- 3. неизвестный профиль — честная ошибка ---------------------------------
power.calls.clear()
gpu.calls.clear()
config_bad = make_config({"cpu": "turbo", "gpu": "quiet"}, {"cpu": "normal", "gpu": "default"})
r_bad = gd_perf.toggle(config_bad, power, gpu)
check("неизвестный CPU-профиль: ошибка названа, GPU применён",
      r_bad["ok"] is True and not power.calls and bool(r_bad["errors"])
      and gpu.calls == [("quiet", "горячая клавиша")], str(r_bad))
check("неизвестный CPU-профиль: состояние переключено (GPU применился)",
      config_bad["settings"]["perf_limited"] is True)

power.calls.clear()
gpu.calls.clear()
config_bad2 = make_config({"cpu": "turbo", "gpu": "bogus"}, {"cpu": "normal", "gpu": "default"})
r_bad2 = gd_perf.toggle(config_bad2, power, gpu)
check("оба профиля неизвестны: ничего не применяем и состояние не трогаем",
      r_bad2["ok"] is False and len(r_bad2["errors"]) == 2
      and not power.calls and not gpu.calls
      and config_bad2["settings"]["perf_limited"] is False, str(r_bad2))

# --- 4. звук переключателя ---------------------------------------------------
_old_winsound = sys.modules.get("winsound")
fake_ws = types.ModuleType("winsound")
beeps: list[tuple] = []
fake_ws.Beep = lambda freq, dur: beeps.append((freq, dur))
sys.modules["winsound"] = fake_ws

gd_perf.play_sound(True)
deadline = time.monotonic() + 2.0
while len(beeps) < 2 and time.monotonic() < deadline:
    time.sleep(0.02)
check("звук «ограничение включено»: два низких тона вниз",
      [f for f, _ in beeps] == [392, 262], str(beeps))

beeps.clear()
gd_perf.play_sound(False)
deadline = time.monotonic() + 2.0
while len(beeps) < 2 and time.monotonic() < deadline:
    time.sleep(0.02)
check("звук «ограничения сняты»: два высоких тона вверх",
      [f for f, _ in beeps] == [523, 784], str(beeps))

if _old_winsound is None:
    sys.modules.pop("winsound", None)
else:
    sys.modules["winsound"] = _old_winsound

# --- 5. диалог «Глобальные клавиши» ------------------------------------------
import main  # noqa: E402

dialog = main.HotkeySettingsDialog(
    {"next_preset": "page up", "perf_toggle": "ctrl+alt+p"},
    {"limited": {"cpu": "eco", "gpu": "quiet"},
     "boost": {"cpu": "normal", "gpu": "max"}},
    False)

check("диалог: клавиша perf_toggle в списке", "perf_toggle" in dialog.inputs
      and dialog.inputs["perf_toggle"].text() == "ctrl+alt+p")
check("диалог: пара профилей прочитана",
      dialog.result_perf() == {"limited": {"cpu": "eco", "gpu": "quiet"},
                               "boost": {"cpu": "normal", "gpu": "max"}},
      str(dialog.result_perf()))
check("диалог: звук выключен (как передали)", dialog.result_sound() is False)

cpu_combo, gpu_combo = dialog.perf_inputs["limited"]
cpu_combo.setCurrentIndex(cpu_combo.findData("boost"))
gpu_combo.setCurrentIndex(gpu_combo.findData(""))
dialog.chk_sound.setChecked(True)
check("диалог: правки читаются обратно",
      dialog.result_perf()["limited"] == {"cpu": "boost", "gpu": ""}
      and dialog.result_sound() is True, str(dialog.result_perf()))

# --- 6. HotkeyManager: регистрация и сигнал ----------------------------------
_added: list[str] = []
_old_add = main.keyboard.add_hotkey
_old_remove = main.keyboard.remove_hotkey
main.keyboard.add_hotkey = lambda hk, cb, suppress=False: _added.append(hk) or len(_added)
main.keyboard.remove_hotkey = lambda handle: None

manager = main.HotkeyManager()
manager.register({"settings": {"hotkeys": {"perf_toggle": "ctrl+alt+p"}}, "presets": []})
check("менеджер регистрирует perf_toggle",
      "ctrl+alt+p" in _added, str(_added))

got: list[bool] = []
manager.perfToggle.connect(lambda: got.append(True))
manager.perfToggle.emit()
pump(100)
check("сигнал perfToggle доходит до слота", got == [True], str(got))

manager.clear()
main.keyboard.add_hotkey = _old_add
main.keyboard.remove_hotkey = _old_remove

# --- 7. трей: пункт переключателя и галка состояния --------------------------
from gd_tray import TrayAssistant  # noqa: E402

tray = TrayAssistant(None)
events: list[bool] = []
tray.perfToggleRequested.connect(lambda: events.append(True))

check("трей: пункт переключателя в меню и он с галкой",
      hasattr(tray, "_perf_act") and tray._perf_act.isCheckable())
check("трей: галка выключена, пока ограничений нет",
      not tray._perf_act.isChecked())

tray.set_perf_limited(True)
check("трей: set_perf_limited(True) включает галку", tray._perf_act.isChecked())

tray._perf_act.trigger()
pump(100)
check("трей: клик по пункту эмитит perfToggleRequested", events == [True], str(events))

tray.retranslate()
check("трей: после смены языка галка сохранилась",
      tray._perf_act.isChecked(), str(tray._perf_act.isChecked()))

# --- 8. палитра команд: команда переключателя --------------------------------
from PyQt5.QtWidgets import QWidget  # noqa: E402

import gd_i18n as i18n  # noqa: E402
from gd_palette import CommandPalette  # noqa: E402


class _FakeMain(QWidget):
    """Заглушка окна: палитре от него нужен только метод переключателя."""

    def __init__(self):
        super().__init__()
        self.calls: list[str] = []

    def _on_perf_toggle(self):
        self.calls.append("toggle")


fake = _FakeMain()
pal = CommandPalette(fake)
perf_title = i18n.t("pal.perf_toggle")
titles = [t for t, _kw, _a in pal._commands]
check("палитра: команда переключателя в списке",
      perf_title in titles, str(titles[:4]) + " …")

action = next(a for t, _kw, a in pal._commands if t == perf_title)
action()
check("палитра: команда зовёт переключатель окна",
      fake.calls == ["toggle"], str(fake.calls))

# --- итог --------------------------------------------------------------------
print()
print("ИТОГ:", "все проверки пройдены" if not FAILS else f"провалов {len(FAILS)}: {FAILS}")
sys.exit(1 if FAILS else 0)

