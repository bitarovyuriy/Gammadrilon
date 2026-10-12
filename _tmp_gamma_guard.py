# -*- coding: utf-8 -*-
"""ВРЕМЕННАЯ проверка «честной гаммы»: сторож восстановления, диагностика, fallback.

Живой гамма-API в проверочной среде недоступен (SetDeviceGammaRamp отвечает FALSE,
GetDeviceGammaRamp не читается) — поэтому логика сторожа проверяется на подменённых
чтении/применении, а отказоустойчивость настоящих путей — на живых вызовах:
ничего не должно падать, а честный отказ (False/None) — доходить до статуса.

  1. контроллер: живые apply/read_current не падают; ramp_max_diff и ramp_array верны;
  2. gd_display.hdr_active(): bool или None, без исключений;
  3. сторож: сброс рампы → возврат; совпадение → тишина; дефолт/флаг/анимация/None → тишина;
  4. сторож: неудачный возврат — молчание (без ложного «восстановлена»);
  5. apply_values: честный статус — HDR, общий отказ, и флаг факта;
  6. i18n: все новые строки переведены (DIRECT);
  7. «кошачье зрение» живьём: сторож защищает его цель, выключение возвращает прежние;
  8. ручная правка слайдеров и пресет снимают «кошачье зрение» без отката значений;
  9. конфиг: атомарная запись, честный отказ, восстановление битого и мусорной
     формы файла, отложенная запись окна (схлопывание + немедленный сброс).

Запуск:  .venv\\Scripts\\python.exe _tmp_gamma_guard.py
"""
from __future__ import annotations

import os
import sys
import time

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

from PyQt5.QtGui import QFontDatabase  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
_fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
for _f in ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf"):
    _p = os.path.join(_fonts_dir, _f)
    if os.path.isfile(_p):
        QFontDatabase.addApplicationFont(_p)

import gd_display  # noqa: E402
import gd_i18n as i18n  # noqa: E402
import main as gd_main  # noqa: E402

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


# --- 1. контроллер: живые вызовы и помощники ---------------------------------
gc = gd_main.GammaController()

apply_result = gc.apply(1.0, 1.0, 1.0, 6500)
check("контроллер: живой apply отвечает честным bool, без исключений",
      isinstance(apply_result, bool), f"apply → {apply_result}")

read_result = gc.read_current()
check("контроллер: живой read_current — рампа или None, без исключений",
      read_result is None or (isinstance(read_result, tuple) and len(read_result) == 3),
      "None" if read_result is None else "рампа 3×256")

ramp_a = gc.build_ramp(1.0, 1.0, 1.0, 6500)
ramp_b = gc.build_ramp(0.9, 0.95, 1.15, 5500)
check("контроллер: ramp_max_diff(равные) == 0",
      gd_main.GammaController.ramp_max_diff(ramp_a, ramp_a) == 0)
check("контроллер: ramp_max_diff(сброс) заметно больше порога сторожа",
      gd_main.GammaController.ramp_max_diff(ramp_a, ramp_b) > gd_main.GAMMA_WATCH_TOLERANCE,
      str(gd_main.GammaController.ramp_max_diff(ramp_a, ramp_b)))

arr = gc.ramp_array(*ramp_a)
check("контроллер: ramp_array собирает 768 значений",
      len(arr) == 768 and arr[0] == ramp_a[0][0] and arr[767] == ramp_a[2][255])
check("контроллер: путей DC — DISPLAY + 16 дисплеев",
      gc._display_dc_names()[0] == "DISPLAY" and len(gc._display_dc_names()) == 17)

# --- 2. HDR-детект: не падает и не врёт типом ---------------------------------
hdr = gd_display.hdr_active()
check("gd_display.hdr_active(): True/False/None, без исключений",
      hdr is None or isinstance(hdr, bool), repr(hdr))

# --- 3. сторож: логика на подменённых чтении/применении -----------------------
# файл настроек не трогаем: запоминаем каждую попытку записи
SAVES: list = []


def _no_save(config):
    SAVES.append(dict(config.get("settings", {}).get("current", {})))


_real_save_config = gd_main.save_config   # настоящая запись — нужна секции 9
gd_main.save_config = _no_save

win = gd_main.MainWindow()
pump(300)

check("окно: сторож запущен с заданным интервалом",
      win.gamma_watch_timer.isActive()
      and win.gamma_watch_timer.interval() == gd_main.GAMMA_WATCH_INTERVAL_MS,
      f"active={win.gamma_watch_timer.isActive()}, {win.gamma_watch_timer.interval()} мс")

win.gamma_watch_timer.stop()   # дальше тикаем вручную — по шагам

state = {"read": None, "apply_result": True, "calls": []}


def fake_apply(b, c, g, t):
    state["calls"].append((b, c, g, t))
    return state["apply_result"]


real_read = win.gamma_controller.read_current
real_apply = win.gamma_controller.apply
win.gamma_controller.read_current = lambda: state["read"]
win.gamma_controller.apply = fake_apply

target = win.gamma_controller.build_ramp(0.9, 0.95, 1.15, 5500)
win._gamma_applied_values = (0.9, 0.95, 1.15, 5500)
win._gamma_applied_ok = True

try:
    # 3.1 сброс системой → возвращаем
    state["calls"].clear()
    state["read"] = win.gamma_controller.build_ramp(1.0, 1.0, 1.0, 6500)
    win.gamma_watch_tick()
    check("сторож: сброшенную рампу возвращает с текущими значениями",
          state["calls"] == [(0.9, 0.95, 1.15, 5500)], str(state["calls"]))
    check("сторож: о возврате честно сказано в статусе",
          win.status_label.text() == i18n.t("Гамма восстановлена после сброса системой."),
          repr(win.status_label.text()))

    # 3.2 рампа совпала → тишина
    state["calls"].clear()
    state["read"] = target
    win.gamma_watch_tick()
    check("сторож: совпавшую рампу не трогает", state["calls"] == [], str(state["calls"]))

    # 3.3 дефолтные значения → нечего защищать
    state["calls"].clear()
    win._gamma_applied_values = (1.0, 1.0, 1.0, 6500)
    state["read"] = win.gamma_controller.build_ramp(0.9, 0.95, 1.15, 5500)
    win.gamma_watch_tick()
    check("сторож: при дефолтных значениях спит", state["calls"] == [], str(state["calls"]))
    win._gamma_applied_values = (0.9, 0.95, 1.15, 5500)

    # 3.4 последнее применение не удалось → не судим
    state["calls"].clear()
    win._gamma_applied_ok = False
    state["read"] = win.gamma_controller.build_ramp(1.0, 1.0, 1.0, 6500)
    win.gamma_watch_tick()
    check("сторож: без удачного применения не работает",
          state["calls"] == [], str(state["calls"]))
    win._gamma_applied_ok = True

    # 3.5 идёт анимация → не мешаем
    state["calls"].clear()
    win.gamma_animation_timer.start(16)
    win.gamma_watch_tick()
    win.gamma_animation_timer.stop()
    win.animation_data = None
    check("сторож: во время анимации не мешает", state["calls"] == [], str(state["calls"]))

    # 3.6 рампу не прочитать → не гадаем
    state["calls"].clear()
    state["read"] = None
    win.gamma_watch_tick()
    check("сторож: нечитаемую рампу не трогает", state["calls"] == [], str(state["calls"]))

    # 3.7 «кошачье зрение»: гамма 2.2 не сохраняется в конфиг — сторож обязан её защищать
    state["calls"].clear()
    win._gamma_applied_values = (1.0, 1.0, 2.2, 6500)
    state["read"] = win.gamma_controller.build_ramp(1.0, 1.0, 2.2, 6500)
    win.gamma_watch_tick()
    check("сторож: кошачье зрение (гамма 2.2 вне конфига) не откатывает",
          state["calls"] == [], str(state["calls"]))
    win._gamma_applied_values = (0.9, 0.95, 1.15, 5500)

    # 3.8 цель ещё не ставилась → не судим
    state["calls"].clear()
    win._gamma_applied_values = None
    state["read"] = win.gamma_controller.build_ramp(1.0, 1.0, 1.0, 6500)
    win.gamma_watch_tick()
    check("сторож: без применённой цели спит", state["calls"] == [], str(state["calls"]))
    win._gamma_applied_values = (0.9, 0.95, 1.15, 5500)

    # 4. возврат не удался → молчим, без ложного «восстановлена»
    state["calls"].clear()
    win.set_status("—")
    state["read"] = win.gamma_controller.build_ramp(1.0, 1.0, 1.0, 6500)
    state["apply_result"] = False
    win.gamma_watch_tick()
    check("сторож: неудачный возврат вызван, но статус не врёт",
          state["calls"] == [(0.9, 0.95, 1.15, 5500)] and win.status_label.text() == "—",
          f"{state['calls']} / {win.status_label.text()!r}")
    state["apply_result"] = True
finally:
    win.gamma_controller.read_current = real_read
    win.gamma_controller.apply = real_apply

# --- 5. apply_values: честные статусы ----------------------------------------
real_hdr = gd_display.hdr_active
real_apply = win.gamma_controller.apply
win.gamma_controller.apply = fake_apply
state["apply_result"] = True

try:
    state["apply_result"] = False
    gd_display.hdr_active = lambda: True
    win.apply_values(0.9, 0.95, 1.15, 5500, save=False)
    check("отказ при HDR: статус называет HDR",
          "HDR" in win.status_label.text()
          and win.status_label.text() == i18n.t(
              "Гамма не применяется: на мониторе включён HDR — Windows не разрешает менять гамму, пока HDR активен."),
          repr(win.status_label.text()))
    check("отказ: флаг факта применения снят", win._gamma_applied_ok is False)

    gd_display.hdr_active = lambda: False
    win.apply_values(0.9, 0.95, 1.15, 5500, save=False)
    check("отказ без HDR: общий честный статус",
          win.status_label.text() == i18n.t(
              "Гамма не применяется: система или драйвер блокируют изменение гаммы."),
          repr(win.status_label.text()))

    state["apply_result"] = True
    win.apply_values(0.9, 0.95, 1.15, 5500, save=False)
    check("успех: флаг факта применения поднят", win._gamma_applied_ok is True)
    check("apply_values: цель сторожа обновляется даже при save=False",
          win._gamma_applied_values == (0.9, 0.95, 1.15, 5500),
          str(win._gamma_applied_values))
finally:
    gd_display.hdr_active = real_hdr
    win.gamma_controller.apply = real_apply

# --- 6. i18n: новые строки переведены ----------------------------------------
gamma_strings = (
    "Гамма восстановлена после сброса системой.",
    "Гамма не применяется: на мониторе включён HDR — Windows не разрешает менять гамму, пока HDR активен.",
    "Гамма не применяется: система или драйвер блокируют изменение гаммы.",
)
check("i18n: строки гаммы есть в DIRECT с переводом",
      all(k in i18n.DIRECT and i18n.DIRECT[k] for k in gamma_strings),
      str([k[:30] for k in gamma_strings]))
check("i18n: строка про админку больше не нужна",
      "Ошибка Gamma API. Попробуйте запустить от имени администратора." not in i18n.DIRECT)

# --- 7. «кошачье зрение» живьём: сторож не воюет с ним ------------------------
state["calls"].clear()
win.gamma_controller.apply = fake_apply
win.gamma_controller.read_current = lambda: state["read"]
state["apply_result"] = True
win.gamma_watch_timer.stop()

try:
    win.set_ui_values(1.0, 1.0, 1.0, 6500, apply_now=True, save=False)
    win.toggle_cat_vision()
    pump(600)   # анимация 350 мс

    check("кошачье зрение: гамма 2.2 применена и стала целью сторожа",
          win.cat_active and win._gamma_applied_values is not None
          and abs(win._gamma_applied_values[2] - 2.2) < 0.01,
          str(win._gamma_applied_values))
    check("кошачье зрение: в конфиг не записалось (гамма осталась 1.0)",
          abs(win.config["settings"]["current"].get("gamma", 1.0) - 1.0) < 0.01,
          str(win.config["settings"]["current"]))

    # рампа совпадает с целью — сторож не трогает
    state["calls"].clear()
    state["read"] = win.gamma_controller.build_ramp(*win._gamma_applied_values)
    win.gamma_watch_tick()
    check("кошачье зрение: сторож видит его как цель и не откатывает",
          state["calls"] == [], str(state["calls"]))

    # система сбросила рампу — сторож возвращает 2.2, а не значение из конфига
    state["calls"].clear()
    state["read"] = win.gamma_controller.build_ramp(1.0, 1.0, 1.0, 6500)
    win.gamma_watch_tick()
    check("кошачье зрение: сброс системы возвращается к 2.2, а не к конфигу",
          len(state["calls"]) == 1 and abs(state["calls"][0][2] - 2.2) < 0.01,
          str(state["calls"]))

    # выключение: цель возвращается к прежним значениям
    state["calls"].clear()
    win.stop_cat_vision()
    pump(700)   # анимация 450 мс
    check("кошачье зрение: выключение вернуло цель к прежним значениям",
          not win.cat_active and win._gamma_applied_values is not None
          and abs(win._gamma_applied_values[2] - 1.0) < 0.01,
          str(win._gamma_applied_values))
finally:
    win.gamma_controller.apply = real_apply
    win.gamma_controller.read_current = real_read

# --- 8. ручная правка и пресет снимают «кошачье зрение» без отката ------------
state["calls"].clear()
win.gamma_controller.apply = fake_apply
win.gamma_controller.read_current = lambda: state["read"]
state["apply_result"] = True

try:
    win.set_ui_values(1.0, 1.0, 1.0, 6500, apply_now=True, save=False)
    win.toggle_cat_vision()
    pump(600)
    check("ручная правка: перед проверкой кошачье зрение включено", win.cat_active)

    SAVES.clear()
    win.brightness_slider.setValue(90)   # пользователь двигает яркость
    pump(120)

    check("ручная правка: режим снят, кнопка отпущена",
          not win.cat_active and not win.cat_button.isChecked(),
          f"cat_active={win.cat_active}, checked={win.cat_button.isChecked()}")
    check("ручная правка: о снятии честно сказано в статусе",
          win.status_label.text() == i18n.t(
              "Кошачье зрение снято: значения изменены вручную."),
          repr(win.status_label.text()))
    check("ручная правка: цель сторожа — значения пользователя (без отката)",
          win._gamma_applied_values is not None
          and abs(win._gamma_applied_values[0] - 0.9) < 0.01
          and abs(win._gamma_applied_values[2] - 2.2) < 0.01,
          str(win._gamma_applied_values))
    check("ручная правка: запись конфига отложена, не на каждый тик",
          SAVES == [], str(SAVES))
    pump(gd_main.CONFIG_SAVE_DELAY_MS + 150)
    check("ручная правка: новые значения дошли до конфига (запись одна)",
          len(SAVES) == 1 and abs(SAVES[-1].get("brightness", 0) - 0.9) < 0.01,
          str(SAVES))

    # повторное включение/выключение не теряет правку пользователя
    win.toggle_cat_vision()
    pump(600)
    win.stop_cat_vision()
    pump(700)
    check("ручная правка: цикл вкл/выкл не возвращает старые значения",
          win._gamma_applied_values is not None
          and abs(win._gamma_applied_values[0] - 0.9) < 0.01,
          str(win._gamma_applied_values))

    # пресет тоже снимает режим и не теряет свои значения
    win.toggle_cat_vision()
    pump(600)
    check("пресет: перед проверкой режим снова включён", win.cat_active)

    preset = win.config["presets"][1]
    state["calls"].clear()
    win.apply_preset_by_index(1)
    pump(900)   # анимация 700 мс

    check("пресет: снял кошачье зрение и оставил свои значения",
          not win.cat_active and not win.cat_button.isChecked()
          and win._gamma_applied_values is not None
          and abs(win._gamma_applied_values[0]
                  - preset.get("brightness", 1.0)) < 0.01,
          str(win._gamma_applied_values))
finally:
    win.gamma_controller.apply = real_apply
    win.gamma_controller.read_current = real_read

check("кошачье зрение: мёртвый cat_timer убран", not hasattr(win, "cat_timer"))

# --- 9. конфиг: атомарность, честный отказ, восстановление, отложенная запись --
import json as _json  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

real_config_path = gd_main.config_path

try:
    with tempfile.TemporaryDirectory() as td:
        cfg = _Path(td) / "presets.json"
        gd_main.config_path = lambda: cfg
        gd_main.save_config = _real_save_config

        # атомарная запись: файл валиден, временный не остаётся
        ok = gd_main.save_config({"settings": {"current": {}}, "presets": []})
        check("конфиг: атомарная запись валидна и не оставляет .tmp",
              ok is True and not (_Path(td) / "presets.json.tmp").exists()
              and _json.loads(cfg.read_text(encoding="utf-8"))["presets"] == [],
              f"ok={ok}")

        # недоступный путь: честный False, без исключения
        gd_main.config_path = lambda: _Path(td) / "нет-каталога" / "presets.json"
        check("конфиг: недоступный путь — честный False без исключения",
              gd_main.save_config({"settings": {}, "presets": []}) is False)

        # битый файл: копия .bak остаётся, о замене честно
        gd_main.config_path = lambda: cfg
        cfg.write_text("{битый json", encoding="utf-8")
        data = gd_main.load_config()
        check("конфиг: битый файл сохранён как .bak, загружены дефолты",
              (_Path(td) / "presets.json.bak").exists()
              and isinstance(data.get("presets"), list) and data["presets"]
              and gd_main.CONFIG_RECOVERY == "backup",
              f"recovery={gd_main.CONFIG_RECOVERY}")
        gd_main.CONFIG_RECOVERY = None

        # мусорная форма (строка вместо настроек, не-объекты в пресетах) не валит старт
        cfg.write_text(_json.dumps({
            "settings": "мусор",
            "presets": ["строка", 42, {"name": "Ок", "brightness": "мусор",
                                       "temperature": None, "hotkey": 7}],
        }, ensure_ascii=False), encoding="utf-8")
        data = gd_main.load_config()
        preset = data["presets"][0]
        check("конфиг: мусорная форма нормализуется, без исключений",
              isinstance(data["settings"], dict) and len(data["presets"]) == 1
              and preset["brightness"] == 1.0 and preset["temperature"] == 6500
              and preset["hotkey"] == "" and preset["name"] == "Ок",
              str(preset))
finally:
    gd_main.config_path = real_config_path
    gd_main.save_config = _no_save

# отложенная запись окна: схлопывание, немедленный сброс, честный отказ
SAVES.clear()
win._save_config()
check("конфиг окна: запись отложена (таймер идёт, файл ещё не писан)",
      win._config_save_timer.isActive() and SAVES == [], str(SAVES))
pump(gd_main.CONFIG_SAVE_DELAY_MS + 150)
check("конфиг окна: отложенная запись сработала один раз", len(SAVES) == 1, str(SAVES))

SAVES.clear()
win._save_config(now=True)
check("конфиг окна: now=True пишет немедленно", len(SAVES) == 1, str(SAVES))

gd_main.save_config = lambda config: False
win._save_config(now=True)
check("конфиг окна: отказ записи честно сказан в статусе",
      win.status_label.text() == i18n.t(
          "Настройки не сохранены: presets.json недоступен для записи."),
      repr(win.status_label.text()))
gd_main.save_config = _no_save

# честный старт: о восстановлении конфига окно говорит заметкой (тот же метод,
# что зовёт __init__ — второго окна в проверке не создаём: teardown двух окон
# в offscreen-среде роняет процесс)
gd_main.CONFIG_RECOVERY = "backup"
try:
    win._config_recovery_note()
    check("конфиг окна: о восстановлении честно сказано при старте",
          win.status_label.text() == i18n.t(
              "presets.json был повреждён — загружены настройки по умолчанию; "
              "копия сохранена как presets.json.bak"),
          repr(win.status_label.text()))

    gd_main.CONFIG_RECOVERY = "no_backup"
    win._config_recovery_note()
    check("конфиг окна: без копии .bak заметка честно короче",
          win.status_label.text() == i18n.t(
              "presets.json был повреждён — загружены настройки по умолчанию"),
          repr(win.status_label.text()))
finally:
    gd_main.CONFIG_RECOVERY = None

# --- итог --------------------------------------------------------------------
win.close()
print()
print("ИТОГ:", "все проверки пройдены" if not FAILS else f"провалов {len(FAILS)}: {FAILS}")
sys.exit(1 if FAILS else 0)
