# -*- coding: utf-8 -*-
"""Смоук-проверка приложения (offscreen): фут, темп метрик, DNS, трей, правила, GPU.

Запуск из корня проекта:  .venv\\Scripts\\python.exe tests\\smoke_check.py

Ничего не пишет в presets.json, не применяет профили, не поднимает компаньонов.
Снимки фута в двух состояниях кладёт в build/shots/ (foot-off.png, foot-on.png).
Код возврата: 0 — все проверки прошли, 1 — есть провалы (см. build/shots/verify.log).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)   # скрипт лежит в tests/, проект — на уровень выше
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

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from PyQt5.QtGui import QFontDatabase  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

import gd_i18n as i18n  # noqa: E402
import gd_metrics  # noqa: E402
import gd_rules  # noqa: E402
import main as gd_main  # noqa: E402

OUT = os.path.join(ROOT, "build", "shots")
os.makedirs(OUT, exist_ok=True)

app = QApplication([])
_fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
for _f in ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf"):
    _p = os.path.join(_fonts_dir, _f)
    if os.path.isfile(_p):
        QFontDatabase.addApplicationFont(_p)
app.setStyleSheet(gd_main.build_app_stylesheet())

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


# --- 1. DNS: быстрый интерфейс вместо PowerShell ----------------------------
import gd_dns  # noqa: E402

t0 = time.perf_counter()
iface = gd_dns.active_interface(force=True)
t_native = (time.perf_counter() - t0) * 1000
check("DNS: интерфейс найден нативно", iface == "Ethernet", f"{iface!r} за {t_native:.1f} мс")

t0 = time.perf_counter()
state = gd_dns.current_state()
t_state = (time.perf_counter() - t0) * 1000
check("DNS: состояние читается быстро (netsh, без PowerShell)",
      t_state < 500 and bool(state.get("iface")),
      f"{t_state:.0f} мс, {state.get('mode')}, {state.get('servers')}")

t0 = time.perf_counter()
for _ in range(100):
    gd_dns.active_interface()
per_call = (time.perf_counter() - t0) / 100 * 1e6
check("DNS: повторный вызов из кэша (≤50 мкс)", per_call <= 50.0, f"{per_call:.1f} мкс/вызов")

src = open(os.path.join(ROOT, "gd_dns.py"), encoding="utf-8").read()
check("DNS: в чтении состояния не осталось powershell",
      src.count('"powershell", "-NoProfile", "-Command"') == 1,
      f"вызовов powershell в модуле: {src.count(chr(34) + 'powershell' + chr(34))} (ожидается 1 — только DoH)")

# --- 2. gd_metrics: адаптивный темп; DNS — только по запросу ----------------
gd_metrics.set_activity(visible=True, gpu_focus=False)
t_win = gd_metrics._ttls()
gd_metrics.set_activity(visible=True, gpu_focus=True)
t_focus = gd_metrics._ttls()
gd_metrics.set_activity(visible=False, gpu_focus=False)
t_tray = gd_metrics._ttls()
gd_metrics.set_activity(visible=True, gpu_focus=False)
check("метрики: окно → CPU 1 с, GPU 5 с, DNS вне таймера",
      t_win[0] == 1.0 and t_win[1] == 5.0 and len(t_win) == 3, str(t_win))
check("метрики: вкладка GPU → GPU 2 с", t_focus[1] == 2.0, str(t_focus))
check("метрики: трей → GPU 20 с, CPU 3 с",
      t_tray[0] == 3.0 and t_tray[1] == 20.0, str(t_tray))
check("метрики: DNS-таймера в модуле не осталось",
      "DNS_TTL" not in open(os.path.join(ROOT, "gd_metrics.py"), encoding="utf-8").read())

# DNS сам не читается: даём циклу 2 с, чтобы ошибиться, и проверяем, что кэш пуст
gd_metrics.start()
time.sleep(2.0)
check("метрики: DNS по таймеру не читается",
      gd_metrics.dns() is None and gd_metrics.dns_age() is None,
      f"dns={gd_metrics.dns()!r}")
gd_metrics.set_dns({"iface": "Ethernet", "mode": "static", "servers": ["1.1.1.1"]})
age = gd_metrics.dns_age()
check("метрики: set_dns кладёт готовое состояние без чтения",
      (gd_metrics.dns() or {}).get("servers") == ["1.1.1.1"] and age is not None and age < 1.0,
      f"{gd_metrics.dns()} · возраст {age if age is None else round(age, 2)} с")
gd_metrics.request_dns()
pump(2500)                       # фон: ровно один вызов netsh
st_dns = gd_metrics.dns() or {}
check("метрики: request_dns → фоновое чтение заполняет кэш",
      bool(st_dns.get("iface")) and bool(st_dns.get("servers")), str(st_dns))

# --- 3. правила: снимок окружения только когда нужен -----------------------
check("правила: без правил снимок не нужен", gd_rules.needs_env([]) is False)
check("правила: триггер процесса → снимок нужен",
      gd_rules.needs_env([{"enabled": True, "trigger": {"type": "process_start", "process": "game.exe"},
                           "condition": {}, "action": {"kind": "cpu_profile", "profile": "eco"}}]) is True)
check("правила: расписание без условий → снимок не нужен",
      gd_rules.needs_env([{"enabled": True, "trigger": {"type": "schedule", "at": "23:00"},
                           "condition": {"load_below": None},
                           "action": {"kind": "cpu_profile", "profile": "eco"}}]) is False)
check("правила: условие по загрузке → снимок нужен",
      gd_rules.needs_env([{"enabled": True, "trigger": {"type": "schedule", "at": "23:00"},
                           "condition": {"load_below": 20},
                           "action": {"kind": "cpu_profile", "profile": "eco"}}]) is True)

# --- 3.0 фон правил: снимаем только те части окружения, которые кто-то читает ---
_sched_load = {"enabled": True, "trigger": {"type": "schedule", "at": "23:00"},
               "condition": {"load_below": 20}, "action": {"kind": "cpu_profile", "profile": "eco"}}
_proc_rule = {"enabled": True, "trigger": {"type": "process_start", "process": "game.exe"},
              "condition": {}, "action": {"kind": "cpu_profile", "profile": "eco"}}
_batt_rule = {"enabled": True, "trigger": {"type": "on_battery"},
              "condition": {}, "action": {"kind": "gpu_profile", "profile": "quiet"}}
_off_rule = dict(_proc_rule, enabled=False)
_need = (gd_rules.env_need([_sched_load]), gd_rules.env_need([_proc_rule]),
         gd_rules.env_need([_batt_rule]), gd_rules.env_need([_off_rule]))
check("правила: снимок окружения берёт только нужные части",
      _need == ({"load"}, {"procs"}, {"on_ac"}, set()), str(_need))

_scan = {"n": 0}
_real_scan = gd_rules.scan_processes


def _count_scan():
    _scan["n"] += 1
    return set()


gd_rules.scan_processes = _count_scan
try:
    _env_load = gd_rules.collect_env({"load"})
finally:
    gd_rules.scan_processes = _real_scan
check("правила: «тихий час» (расписание + загрузка) процессы не сканирует",
      _scan["n"] == 0 and set(_env_load) == {"load"},
      f"сканов: {_scan['n']}, ключи: {sorted(_env_load)}")

gd_rules.update_env({"procs": {"game.exe"}, "on_ac": True, "load": 5.0})
gd_rules.update_env({"load": 7.0})
check("правила: перестав следить за процессами, снимок их не хранит",
      gd_rules._env["procs"] == set() and gd_rules._env["load"] == 7.0,
      f"procs={len(gd_rules._env['procs'])}, load={gd_rules._env['load']}")

# --- 3.1 действия правил из «Утилит»: доступны и честны ---------------------
check("правила: «Сменить DNS» и «Очистить standby list» больше не заглушки",
      "set_dns" in gd_rules.ACTIONS and "purge_standby" in gd_rules.ACTIONS
      and gd_rules.UNAVAILABLE_ACTIONS == set()
      and gd_rules.ADMIN_ACTIONS == {"purge_standby", "set_dns"},
      f"{gd_rules.ACTIONS.get('set_dns')!r} · недоступных: {len(gd_rules.UNAVAILABLE_ACTIONS)}")

_desc = gd_rules.describe({"trigger": {"type": "on_battery"}, "condition": {},
                           "action": {"kind": "set_dns", "dns": "cloudflare"}})[2]
check("правила: в таблице видно, на какой DNS переключает правило",
      _desc == "Сменить DNS: Cloudflare" and "недоступно" not in _desc, _desc)

check("правила: «Свой» в правилах не предлагается (ему нужны адреса из вкладки)",
      "custom" not in dict(gd_rules.DNS_PROFILES)
      and gd_rules.dns_servers("cloudflare") == ["1.1.1.1", "1.0.0.1"]
      and gd_rules.dns_servers("custom") == [] and gd_rules.dns_servers(None) == [],
      f"провайдеров: {len(gd_rules.DNS_PROFILES)}, cloudflare={gd_rules.dns_servers('cloudflare')}")

_engine = gd_rules.RuleEngine()
_engine.prime()
_past = {"id": "t1", "enabled": True, "name": "тест DNS",
         "trigger": {"type": "schedule", "at": (datetime.now() - timedelta(minutes=1)).strftime("%H:%M")},
         "condition": {}, "action": {"kind": "set_dns", "dns": "quad9"}}
check("правила: пропущенное время не «догоняется» — старт после времени правила ничего не меняет",
      _engine.evaluate([_past]) == [] and _engine.evaluate([_past]) == [],
      f"at={_past['trigger']['at']} (прошлая минута)")

_cross = {"id": "t2", "enabled": True, "name": "тест расписания",
          "trigger": {"type": "schedule", "at": "23:00"},
          "condition": {}, "action": {"kind": "set_dns", "dns": "quad9"}}
_engine._last_check = datetime(2026, 1, 1, 22, 59, 50)
check("правила: расписание срабатывает на переходе времени",
      [x[0] for x in _engine.evaluate([_cross], now=datetime(2026, 1, 1, 23, 0, 2))] == [_cross])
check("правила: второй раз в те же сутки не срабатывает",
      _engine.evaluate([_cross], now=datetime(2026, 1, 1, 23, 0, 20)) == [])
_engine._last_check = datetime(2026, 1, 2, 22, 59, 55)
check("правила: на следующий день срабатывает снова",
      [x[0] for x in _engine.evaluate([_cross], now=datetime(2026, 1, 2, 23, 0, 1))] == [_cross])
_engine._last_check = datetime(2026, 1, 3, 22, 0, 0)
check("правила: после сна (разрыв больше окна) расписание не догоняет",
      _engine.evaluate([_cross], now=datetime(2026, 1, 3, 23, 0, 5)) == [],
      f"окно {gd_rules.SCHEDULE_MAX_GAP} с")
_engine._last_check = datetime(2026, 1, 4, 22, 59, 50)
check("правила: битое время в расписании не роняет движок и не срабатывает",
      _engine.evaluate([{"id": "t3", "enabled": True, "trigger": {"type": "schedule", "at": "25:99"},
                         "condition": {}, "action": {"kind": "set_dns", "dns": "quad9"}}],
                       now=datetime(2026, 1, 4, 23, 0, 5)) == [])
_engine._last_check = datetime(2026, 1, 5, 22, 59, 50)
check("правила: действие set_dns не отбрасывается движком",
      [x[0] for x in _engine.evaluate([_cross], now=datetime(2026, 1, 5, 23, 0, 1))] == [_cross])

# --- 3.2 хелпер: фоновое действие не поднимает UAC -------------------------
import gd_helper  # noqa: E402

_no_sess = gd_helper.HelperClient()
_launched = {"n": 0}


def _forbid_launch(*a, **kw):
    """Поднимать хелпер в проверке нельзя: это отдельный процесс и окно UAC."""
    _launched["n"] += 1
    raise RuntimeError("запуск хелпера запрещён в проверке")


_no_sess._launch = _forbid_launch
_no_sess._load_session = lambda: None
_res_bg = _no_sess.call_existing({"cmd": "ping"})
check("хелпер: фоновый вызов без сессии не поднимает UAC",
      _res_bg.get("ok") is False and _launched["n"] == 0 and "UAC" in str(_res_bg.get("error")),
      f"запусков: {_launched['n']} · {_res_bg.get('error')!r}")
_res_tab = _no_sess.call({"cmd": "ping"})
check("хелпер: обычный вызов (вкладка) по-прежнему умеет поднимать хелпер",
      _launched["n"] == 1 and _res_tab.get("ok") is False, f"запусков: {_launched['n']}")

_live = gd_helper.HelperClient()
_live._load_session = lambda: {"port": 1, "token": "ab" * 16}
_live._connect = lambda port, token: True
_res_live = _live.call_existing({"cmd": "ping"})
check("хелпер: с живой сессией фоновый вызов идёт в хелпер, а не в отказ",
      _res_live.get("ok") is False and "UAC" not in str(_res_live.get("error")),
      str(_res_live.get("error")))


# --- 4. главное окно: фут, трей и DNS по запросу ---------------------------
# считаем чтения DNS: окно не должно читать её само, вкладка — ровно один раз
dns_reads = {"n": 0}
_real_dns_state = gd_dns.current_state

# кэш наполнила проверка request_dns() выше — чистим, чтобы увидеть старт «с нуля»
with gd_metrics._lock:
    gd_metrics._state["dns"] = None
    gd_metrics._ts["dns"] = 0.0


def _counting_dns_state(*a, **kw):
    dns_reads["n"] += 1
    return _real_dns_state(*a, **kw)


gd_dns.current_state = _counting_dns_state
try:
    win = gd_main.MainWindow()
    win.resize(win.compact_size)
    win.show()
    pump(1500)
    check("окно: при сборке окна DNS не читается (на старте нет netsh)",
          dns_reads["n"] == 0, f"чтений: {dns_reads['n']}")
    check("статусбар: DNS пуст, пока не спросили", win._sb_dns.text() == "DNS —",
          win._sb_dns.text())
    win.tabs.setCurrentIndex(win._key_to_index["tab.dns"])   # открытие = запрос
    pump(1500)
    check("окно: открытие вкладки «DNS» → одно чтение в фоне", dns_reads["n"] == 1,
          f"чтений: {dns_reads['n']}")
    win._refresh_statusbar()
    exp = (gd_metrics.dns() or {}).get("servers") or []
    check("статусбар: DNS подтянулся из кэша вкладки (без второго netsh)",
          bool(exp) and win._sb_dns.text() == f"DNS {exp[0]}", f"{win._sb_dns.text()!r}")
    real_age_fn = gd_metrics.dns_age
    gd_metrics.dns_age = lambda: 600.0        # данные постарше — возраст должен быть виден
    win._refresh_statusbar()
    aged = win._sb_dns.text()
    gd_metrics.dns_age = real_age_fn
    check("статусбар: возраст данных DNS показан", aged.endswith("(10 мин)"), aged)
    win.tabs.setCurrentIndex(win._key_to_index["tab.gamma"])
    pump(400)
finally:
    gd_dns.current_state = _real_dns_state

# --- вкладка «Утилиты»: карточка обновлений ---
try:
    win.tabs.setCurrentIndex(win._key_to_index["tab.utils"])
    pump(700)
    utils = getattr(win, "_utils_page", None)
    check("утилиты: вкладка собралась", utils is not None, "нет _utils_page")
    check("утилиты: карточка обновлений на месте",
          utils is not None and hasattr(utils, "chk_updates") and hasattr(utils, "btn_updates"),
          "нет chk_updates/btn_updates")
    check("утилиты: проверка обновлений выключена по умолчанию (обещание «без облака»)",
          gd_main.DEFAULT_CONFIG["settings"]["check_updates"] is False,
          repr(gd_main.DEFAULT_CONFIG["settings"].get("check_updates")))
    expected = bool((win.config.get("settings") or {}).get("check_updates", False))
    check("утилиты: галочка обновлений показывает конфиг, а не саму себя",
          utils is not None and utils.chk_updates.isChecked() == expected,
          f"галочка={utils.chk_updates.isChecked() if utils else None}, конфиг={expected}")
    utils.set_update_status("проверка связи", ok=True, url="https://example.invalid/release")
    check("утилиты: подпись обновлений принимает результат и ссылку",
          "example.invalid" in utils.lbl_updates.text(), utils.lbl_updates.text()[:90])
    win.tabs.setCurrentIndex(win._key_to_index["tab.gamma"])
    pump(300)
except Exception as exc:  # noqa: BLE001
    check("утилиты: вкладка открылась без исключения", False, f"{type(exc).__name__}: {exc}")

check("гамма: сторож запущен с заданным интервалом",
      win.gamma_watch_timer.isActive()
      and win.gamma_watch_timer.interval() == gd_main.GAMMA_WATCH_INTERVAL_MS,
      f"active={win.gamma_watch_timer.isActive()}, {win.gamma_watch_timer.interval()} мс")
check("гамма: флаг факта применения — честный bool",
      isinstance(win._gamma_applied_ok, bool), repr(win._gamma_applied_ok))
try:
    win.gamma_watch_tick()
    tick_ok = True
except Exception as exc:  # noqa: BLE001
    tick_ok = False
    print("тик сторожа упал:", exc)
check("гамма: тик сторожа не падает на живом окне", tick_ok)

# GPU: лимит слетает и внутри сессии (сон, TDR, сброс драйвера) — вкладку о факте
# спрашивает тот же двухсекундный тик статусбара, что и об обходе DPI.
_drift = {"n": 0}
_real_drift = win._gpu_page.check_drift
win._gpu_page.check_drift = lambda: _drift.__setitem__("n", _drift["n"] + 1)
win.is_quitting = False
win._check_gpu_drift()
check("GPU: тик статусбара спрашивает вкладку о слетевшем лимите", _drift["n"] == 1,
      f"вызовов: {_drift['n']}")
quitting_silent = True
try:
    win.is_quitting = True          # выход сам решает судьбу лимита — тост был бы ложным
    win._check_gpu_drift()
    quitting_silent = _drift["n"] == 1
finally:
    win.is_quitting = False
    win._gpu_page.check_drift = _real_drift
check("GPU: на выходе сверка молчит", quitting_silent, f"вызовов: {_drift['n']}")

# Питание CPU: компаньон — единственный сторож отката профиля; его смерть в работе
# ловит тот же тик статусбара (фут сайдбара виден только при открытом окне).
_svc = {"n": 0}
_real_svc = win._power_page.check_service_gone
win._power_page.check_service_gone = lambda: _svc.__setitem__("n", _svc["n"] + 1)
win.is_quitting = False
win._check_power_service()
check("CPU: тик статусбара спрашивает вкладку о смерти компаньона", _svc["n"] == 1,
      f"вызовов: {_svc['n']}")
quitting_svc = True
try:
    win.is_quitting = True          # выход сам просит компаньон уйти: тост был бы ложным
    win._check_power_service()
    quitting_svc = _svc["n"] == 1
finally:
    win.is_quitting = False
    win._power_page.check_service_gone = _real_svc
check("CPU: на выходе доклад о смерти компаньона молчит", quitting_svc,
      f"вызовов: {_svc['n']}")

check("QSS: правило для погашенного LED есть", '[state="off"]' in gd_main.build_app_stylesheet())

foot_widget = win._foot_led.parentWidget()
off_name = os.path.join(OUT, "foot-off.png")
foot_widget.grab().save(off_name)

check("фут: компаньона нет → «не запущена» и LED off",
      win._foot_label.text() == "Служба питания не запущена"
      and win._foot_led.property("state") == "off",
      f"{win._foot_label.text()!r}, state={win._foot_led.property('state')!r}")
check("фут: длинная подпись не обрезается",
      win._foot_label.width() >= win._foot_label.sizeHint().width(),
      f"ширина {win._foot_label.width()} при sizeHint {win._foot_label.sizeHint().width()}")
check("фут: подсказка про ленивый компаньон",
      "профиль CPU" in win._foot_label.toolTip(), win._foot_label.toolTip())

# живой компаньон: подменяем только признак соединения (без пайпа и процессов)
client = win._power_page.client
client._conn = object()
win._foot_state = None
win._sync_foot()
on_name = os.path.join(OUT, "foot-on.png")
foot_widget.grab().save(on_name)
check("фут: компаньон жив → «активна» и LED on",
      win._foot_label.text() == "Служба питания активна"
      and win._foot_led.property("state") == "on",
      f"{win._foot_label.text()!r}, state={win._foot_led.property('state')!r}")
check("фут: в подсказке — исходная схема",
      "исходная схема" in win._foot_label.toolTip(), win._foot_label.toolTip())

win._power_page._profile = "eco"
win._foot_state = None
win._sync_foot()
check("фут: применённый профиль виден в подсказке",
      "ЭКО" in win._foot_label.toolTip(), win._foot_label.toolTip())

i18n.set_language("en")
win._foot_state = None
win._sync_foot()
check("фут: подпись переводится (EN)", win._foot_label.text() == "Power service active",
      win._foot_label.text())
check("фут: подсказка переводится (EN)", "Profile: ECO" in win._foot_label.toolTip(),
      win._foot_label.toolTip())
# подсказки статусбара переводит set_language — повторяем его проход по биндингам
for _w, _key in win._tooltip_bindings:
    _w.setToolTip(i18n.t(_key))
check("статусбар: подсказка DNS переводится (EN)", "on demand" in win._sb_dns.toolTip(),
      win._sb_dns.toolTip())
i18n.set_language("ru")
for _w, _key in win._tooltip_bindings:
    _w.setToolTip(i18n.t(_key))
check("статусбар: подсказка DNS возвращается в RU", "по запросу" in win._sb_dns.toolTip(),
      win._sb_dns.toolTip())

client._conn = None
win._power_page._profile = None
win._foot_state = None
win._sync_foot()
check("фут: компаньон умер → снова «не запущена»",
      win._foot_label.text() == "Служба питания не запущена"
      and win._foot_led.property("state") == "off")

# трей: explorer не трогаем, если ничего не изменилось
tray = win.tray
calls = {"icon": 0, "tip": 0}
real_icon, real_tip = tray.setIcon, tray.setToolTip


def _icon(*a, **kw):
    calls["icon"] += 1
    return real_icon(*a, **kw)


def _tip(*a, **kw):
    calls["tip"] += 1
    return real_tip(*a, **kw)


tray.setIcon, tray.setToolTip = _icon, _tip
try:
    tray.refresh("eco", "game", "ЭКО · макс 50% · RAM 47%")
    tray.refresh("eco", "game", "ЭКО · макс 50% · RAM 47%")
    tray.refresh("eco", "game", "ЭКО · макс 50% · RAM 47%")
    same = dict(calls)
    tray.refresh("eco", "game", "ЭКО · макс 50% · RAM 48%")      # тултип изменился
    tray.refresh("boost", "game", "БУСТ · макс 100% · RAM 48%")  # профиль изменился
    after = dict(calls)
finally:
    tray.setIcon, tray.setToolTip = real_icon, real_tip
check("трей: 3 одинаковых тика → одна отрисовка и один тултип",
      same["icon"] == 1 and same["tip"] == 1, str(same))
# значок перерисовывается только при смене профиля (тултип меняется чаще — °C, RAM)
check("трей: тултип обновляется, значок — только по профилю",
      after["tip"] == 3 and after["icon"] == 2, str(after))

# трей: галка переключателя производительности (то же действие, что горячая клавиша)
check("трей: пункт переключателя с галкой",
      hasattr(tray, "_perf_act") and tray._perf_act.isCheckable())
tray.set_perf_limited(True)
check("трей: галка переключателя включается", tray._perf_act.isChecked())
tray.set_perf_limited(False)

# окружение правил: метод окна не снимает то, что не нужно
env = win._collect_rule_env()
check("окно: _collect_rule_env согласован с needs_env",
      (env is None) == (not gd_rules.needs_env()), f"env={'есть' if env else 'нет'}")

# --- 4.2 действия правил: реальный результат в лог, без UAC из фона ---------
import gd_dns  # noqa: E402
import gd_journal  # noqa: E402
import gd_rules_ui  # noqa: E402
import gd_toast  # noqa: E402

_records = {"journal": 0, "toast": 0, "dns_req": 0}
_real_journal, _real_toast = gd_journal.record, gd_toast.show_toast
_real_request_dns = gd_metrics.request_dns
_real_base, _real_state_path = gd_rules._base_dir, gd_dns.state_path
_tmpdir = Path(tempfile.mkdtemp(prefix="gd_rules_"))
# лог правил и «как было» для DNS — во временную папку: настоящие данные не трогаем
gd_rules._base_dir = lambda: _tmpdir
gd_dns.state_path = lambda: _tmpdir / "dns_state.json"
gd_journal.record = lambda *a, **kw: _records.__setitem__("journal", _records["journal"] + 1)
gd_toast.show_toast = lambda *a, **kw: _records.__setitem__("toast", _records["toast"] + 1)
gd_metrics.request_dns = lambda: _records.__setitem__("dns_req", _records["dns_req"] + 1)


class _FakeHelper:
    """Хелпер-заглушка: настоящие процессы, пайпы и UAC в проверке не нужны."""

    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def call_existing(self, obj, timeout=15.0):
        self.calls.append(dict(obj))
        return dict(self.resp)


try:
    fake_ok = _FakeHelper({"ok": True, "data": {"before": {"Free": 0},
                                                "after": {"Free": 420 * 1024 ** 2}}})
    win._rule_client = lambda: fake_ok
    first = win._run_rule_action({"name": "Ночью чистить ОЗУ", "action": {"kind": "purge_standby"}})
    check("правила: очистка ОЗУ идёт в фоне (в лог — по факту, а не наперёд)", first is None)
    pump(500)
    fires = gd_rules.recent_fires(5)
    got = (fires[0].get("result") if fires else "") or ""
    check("правила: в логе — реальный результат очистки",
          "standby list очищен, свободно +420 МБ" in got, got)
    check("правила: очистка из правила попала в журнал и тост",
          _records["journal"] == 1 and _records["toast"] == 1, str(_records))
    check("правила: очистка ОЗУ не дёргает чтение DNS", _records["dns_req"] == 0, str(_records))

    fake_no = _FakeHelper({"ok": False, "error": "нужны права администратора"})
    win._rule_client = lambda: fake_no
    win._run_rule_action({"name": "Чистить ОЗУ", "action": {"kind": "purge_standby"}})
    pump(500)
    fires = gd_rules.recent_fires(5)
    got = (fires[0].get("result") if fires else "") or ""
    check("правила: отказ без прав попадает в лог честно",
          "очистка не выполнена" in got and "нужны права" in got, got)
    check("правила: неудачная очистка не попала в журнал изменений",
          _records["journal"] == 1, str(_records))

    fake_dns = _FakeHelper({"ok": True, "data": "ok"})
    win._rule_client = lambda: fake_dns
    rule_dns = {"name": "На батарее — Quad9", "action": {"kind": "set_dns", "dns": "quad9"}}
    check("правила: смена DNS тоже уходит в фон", win._run_rule_action(rule_dns) is None)
    pump(800)
    fires = gd_rules.recent_fires(5)
    got = (fires[0].get("result") if fires else "") or ""
    check("правила: в логе — какой DNS выставлен, с адресами",
          "DNS переключён на Quad9 (9.9.9.9, 149.112.112.112)" in got, got)
    check("правила: смена DNS обратилась к хелперу в режиме static с адресами провайдера",
          fake_dns.calls[-1:] == [{"cmd": "dns_apply", "mode": "static",
                                   "servers": ["9.9.9.9", "149.112.112.112"]}],
          str(fake_dns.calls[-1:]))
    check("правила: смена DNS по правилу обновила статусбар запросом (не таймером)",
          _records["dns_req"] == 1, str(_records))
    check("правила: смена DNS попала в журнал и тост",
          # тостов три: успешная очистка, отказ очистки (теперь тоже говорит) и DNS
          _records["journal"] == 2 and _records["toast"] == 3, str(_records))
    check("правила: «как было» для DNS записано до смены",
          gd_dns.state_path().exists(), str(gd_dns.state_path()))

    # --- 4.2.1 переключатель производительности: отказ и успех видны ------------
    # Раньше set_status принимал только текст, а переключатель передавал ok=False:
    # при незаданных профилях из слота горячей клавиши летел TypeError, и вместо
    # честного «профили не заданы» пользователь не видел ничего.
    import gd_perf  # noqa: E402
    import gd_theme as th  # noqa: E402

    _perf_settings = win.config["settings"]
    _had_profiles = "perf_profiles" in _perf_settings
    _perf_profiles = _perf_settings.get("perf_profiles")
    _perf_toasts: list = []
    gd_toast.show_toast = lambda text, kind="ok": _perf_toasts.append((text, kind))

    _perf_settings["perf_profiles"] = {"limited": {"cpu": "", "gpu": ""},
                                       "boost": {"cpu": "", "gpu": ""}}
    _perf_crash = None
    try:
        win._on_perf_toggle()
    except Exception as _exc:  # noqa: BLE001
        _perf_crash = _exc
    check("переключатель: незаданные профили не роняют слот горячей клавиши",
          _perf_crash is None, repr(_perf_crash))
    check("переключатель: отказ назван словами и подсвечен тревогой",
          win.status_label.text() == i18n.t("Переключатель производительности: профили не заданы")
          and th.WARN in win.status_label.styleSheet(),
          f"{win.status_label.text()!r} / {win.status_label.styleSheet()!r}")
    check("переключатель: отказ подтверждён warn-тостом",
          bool(_perf_toasts) and _perf_toasts[-1][1] == "warn",
          str(_perf_toasts[-1:] or None))

    _real_toggle, _real_sound = gd_perf.toggle, gd_perf.play_sound
    _save_before = win._save_config
    _sounds: list = []
    gd_perf.toggle = lambda cfg, power, gpu: {"ok": True, "state": "limited",
                                              "limited": True, "cpu": "eco",
                                              "gpu": "quiet", "errors": []}
    gd_perf.play_sound = lambda limited: _sounds.append(limited)
    win._save_config = lambda *a, **kw: None      # конфиг пользователя не трогаем
    try:
        win._on_perf_toggle()
    finally:
        gd_perf.toggle, gd_perf.play_sound = _real_toggle, _real_sound
        win._save_config = _save_before
        if _had_profiles:
            _perf_settings["perf_profiles"] = _perf_profiles
        else:
            _perf_settings.pop("perf_profiles", None)
    check("переключатель: успех сказан словами и обычным тостом",
          win.status_label.text() == i18n.t("Переключатель производительности: {v0}",
                                            v0=i18n.t("ограничение включено"))
          and win.status_label.styleSheet() == ""
          and bool(_perf_toasts) and _perf_toasts[-1][1] == "ok",
          f"{win.status_label.text()!r} / {_perf_toasts[-1:] or None}")
    check("переключатель: звук отклика — по факту (ограничение включено)",
          _sounds == [True], str(_sounds))
    gd_toast.show_toast = lambda *a, **kw: _records.__setitem__("toast", _records["toast"] + 1)

    # --- 4.3 отказ внешних путей: не только в логе, но и в тосте ---------------
    # Правило срабатывает само, профиль приходит из трея, «тихий час» — по часам:
    # окно в этот момент свёрнуто в трей, и статусбар отказа никто не увидит.
    # Успех этих путей тостом показывался, а отказ молчал — теперь наоборот тоже.
    import gd_profiles  # noqa: E402

    _fail_toasts: list = []
    gd_toast.show_toast = lambda text, kind="ok": _fail_toasts.append((text, kind))

    win._rule_client = lambda: _FakeHelper({"ok": False, "error": "нужны права администратора"})
    win._run_rule_action({"name": "Чистить ОЗУ", "action": {"kind": "purge_standby"}})
    pump(500)
    check("правила: отказ очистки ОЗУ виден тревожным тостом с причиной",
          bool(_fail_toasts) and _fail_toasts[-1][1] == "warn"
          and "очистка не выполнена" in _fail_toasts[-1][0]
          and "нужны права" in _fail_toasts[-1][0],
          str(_fail_toasts[-1:] or None))
    check("правила: тот же отказ подсвечен и в статусе окна",
          win.status_label.text() == i18n.t("Правило «{v0}»: очистка ОЗУ не выполнена.",
                                            v0="Чистить ОЗУ")
          and th.WARN in win.status_label.styleSheet(),
          f"{win.status_label.text()!r} / {win.status_label.styleSheet()!r}")

    _real_cpu_apply = win._power_page.apply_profile

    def _boom(*_a, **_kw):
        raise RuntimeError("нет компаньона")

    win._power_page.apply_profile = _boom
    _fail_toasts.clear()
    _res = win._run_rule_action({"name": "Сломать", "action": {"kind": "cpu_profile"}})
    check("правила: исключение в действии названо и в логе, и в тосте",
          "ошибка — нет компаньона" in _res
          and bool(_fail_toasts) and _fail_toasts[-1][1] == "warn"
          and "нет компаньона" in _fail_toasts[-1][0],
          f"{_res!r} / {_fail_toasts[-1:] or None}")

    _fail_toasts.clear()
    _res = win._run_rule_action({"name": "Странное", "action": {"kind": "нет-такого"}})
    win._power_page.apply_profile = _real_cpu_apply
    check("правила: недоступное действие не молчит",
          "действие недоступно" in _res
          and bool(_fail_toasts) and _fail_toasts[-1][1] == "warn"
          and "действие недоступно" in _fail_toasts[-1][0],
          f"{_res!r} / {_fail_toasts[-1:] or None}")

    _real_gpu_apply = win._gpu_page.apply_profile
    win._gpu_page.apply_profile = _boom
    _fail_toasts.clear()
    win.on_gpu_profile("quiet")          # трей: отказ не должен пропасть молча
    win._gpu_page.apply_profile = _real_gpu_apply
    check("трей: отказ профиля GPU виден тревожным тостом",
          bool(_fail_toasts) and _fail_toasts[-1][1] == "warn"
          and "нет компаньона" in _fail_toasts[-1][0],
          str(_fail_toasts[-1:] or None))

    _fail_toasts.clear()
    win._power_page._apply_done({"ok": False, "error": "нужны права администратора"},
                                {"cmd": "apply", "profile": "eco"})
    check("CPU: отказ профиля виден тостом, а не только в строке карточки",
          bool(_fail_toasts) and _fail_toasts[-1][1] == "warn"
          and "не применён" in _fail_toasts[-1][0],
          str(_fail_toasts[-1:] or None))

    # --- 4.4 «тихий час»: не выдавать попытку за успех -------------------------
    _real_gamma_apply = win.gamma_controller.apply
    _real_quiet_path = gd_profiles.quiet_path
    _quiet_dir = Path(tempfile.mkdtemp(prefix="gd_quiet_"))
    gd_profiles.quiet_path = lambda: _quiet_dir / "quiet_hour.json"
    _power_calls: list = []
    win._power_page.apply_profile = lambda *a, **kw: _power_calls.append(a[:1])

    win.gamma_controller.apply = lambda *a, **kw: False
    _fail_toasts.clear()
    win.apply_values(0.9, 0.95, 1.15, 5500, save=False)
    check("гамма: отказ подсвечен тревогой в статусе окна",
          th.WARN in win.status_label.styleSheet(), repr(win.status_label.styleSheet()))

    _fail_toasts.clear()
    win._quiet_apply({"enabled": True, "start": "23:00", "end": "08:00"})
    check("тихий час: отказ гаммы не выдаётся за «ЭКО включён»",
          bool(_fail_toasts) and _fail_toasts[-1] == (i18n.t("toast.quiet_no_dim"), "warn"),
          str(_fail_toasts[-1:] or None))
    check("тихий час: ЭКО при этом всё равно запущен (яркость — не повод молчать)",
          _power_calls[-1:] == [("eco",)], str(_power_calls[-1:]))
    check("тихий час: состояние «применён» записано, чтобы тик не спамил тостами",
          bool(gd_profiles.load_quiet().get("applied")))

    win.gamma_controller.apply = lambda *a, **kw: True
    _fail_toasts.clear()
    win._quiet_apply({"enabled": True, "start": "23:00", "end": "08:00"})
    check("тихий час: полный успех — прежний спокойный тост",
          _fail_toasts[-1:] == [(i18n.t("toast.quiet_on"), "info")],
          str(_fail_toasts[-1:] or None))

    win.gamma_controller.apply = lambda *a, **kw: False
    _fail_toasts.clear()
    win._quiet_release({"enabled": True, "start": "23:00", "end": "08:00"})
    check("тихий час: неудачный возврат яркости не выдаётся за успех",
          bool(_fail_toasts) and _fail_toasts[-1] == (i18n.t("toast.quiet_keep_dim"), "warn"),
          str(_fail_toasts[-1:] or None))

    win.gamma_controller.apply = _real_gamma_apply
    win._power_page.apply_profile = _real_cpu_apply
    gd_profiles.quiet_path = _real_quiet_path
    shutil.rmtree(_quiet_dir, ignore_errors=True)
    gd_toast.show_toast = lambda *a, **kw: _records.__setitem__("toast", _records["toast"] + 1)

    _dlg = gd_rules_ui.RuleDialog({"name": "x", "trigger": {"type": "process_start", "process": "game.exe"},
                                   "condition": {}, "action": {"kind": "set_dns", "dns": "quad9"}})
    check("диалог правила: у «Сменить DNS» список провайдеров и подпись по делу",
          _dlg.act_profile.count() == len(gd_rules.DNS_PROFILES)
          and _dlg.act_profile.currentData() == "quad9"
          and _dlg._profile_label.text() == "Провайдер DNS",
          f"{_dlg.act_profile.count()} пунктов, выбран {_dlg.act_profile.currentData()!r}, "
          f"подпись {_dlg._profile_label.text()!r}")
    saved = gd_rules_ui.RulesWidget._apply_dialog(_dlg, dict(_dlg.rule))
    check("диалог правила: провайдер DNS сохраняется в правило",
          saved["action"] == {"kind": "set_dns", "dns": "quad9"}, str(saved["action"]))

    _dlg2 = gd_rules_ui.RuleDialog({"name": "y", "trigger": {"type": "schedule", "at": "23:00"},
                                    "condition": {}, "action": {"kind": "cpu_profile", "profile": "boost"}})
    check("диалог правила: правка не сбрасывает выбранный профиль",
          _dlg2.act_profile.currentData() == "boost" and _dlg2.act_profile.count() == 3,
          f"выбран {_dlg2.act_profile.currentData()!r} из {_dlg2.act_profile.count()}")
    _dlg2.act_kind.setCurrentIndex(_dlg2.act_kind.findData("set_dns"))
    pump(60)
    check("диалог правила: смена действия меняет список и подпись",
          _dlg2._profile_label.text() == "Провайдер DNS"
          and _dlg2.act_profile.count() == len(gd_rules.DNS_PROFILES),
          f"{_dlg2._profile_label.text()!r}, пунктов {_dlg2.act_profile.count()}")

    _dlg3 = gd_rules_ui.RuleDialog({"name": "z", "trigger": {"type": "schedule", "at": "23:00"},
                                    "condition": {}, "action": {"kind": "cpu_profile", "profile": "eco"}})
    check("диалог правила: подсказка расписания не обещает срабатывание задним числом",
          "не «догоняется»" in _dlg3.trig_hint.text(), _dlg3.trig_hint.text())

    # лог правил: запись не за сегодня должна показывать дату, иначе «23:00» читается как сегодня
    _old = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    with gd_rules.log_path().open("a", encoding="utf-8") as _fh:
        _fh.write(json.dumps({"ts": _old, "rule": "старое правило", "action": "ЭКО",
                              "result": "ок"}, ensure_ascii=False) + "\n")
    _page = gd_rules_ui.RulesWidget()
    _line = _page.log.item(0).text()
    check("журнал правил: вчерашняя запись показана с датой",
          _line.startswith(f"{_old[8:10]}.{_old[5:7]} {_old[11:16]}"), _line)
    _page.deleteLater()

    # живой прогон всей цепочки: таймер окна → движок → действие → лог правил.
    # Отсчёт движка ставим за секунду до времени правила — так это выглядит для
    # таймера, который тикает раз в 3 с и застаёт наступление времени между тиками.
    _deny = _FakeHelper({"ok": False, "error": "нужны права администратора"})
    win._rule_client = lambda: _deny
    _at_live = datetime.now().strftime("%H:%M")
    gd_rules.save_rules([{"id": "live1", "enabled": True, "name": "живое расписание",
                          "trigger": {"type": "schedule", "at": _at_live},
                          "condition": {}, "action": {"kind": "purge_standby"}}])
    _before = len(gd_rules.recent_fires(50))
    _boundary = datetime.now().replace(second=0, microsecond=0)
    win._rule_engine._last_check = _boundary - timedelta(seconds=1)
    pump(4500)                      # тик правил — раз в 3 с
    _fires = gd_rules.recent_fires(50)
    _last = _fires[0] if _fires else {}
    check("правила: живой прогон — таймер окна применил расписание по наступлении времени",
          len(_fires) > _before and _last.get("rule") == "живое расписание"
          and "очистка не выполнена" in (_last.get("result") or ""),
          f"at={_at_live}, записей: {_before} → {len(_fires)}, последняя: {_last.get('result')!r}")
    gd_rules.save_rules([])
finally:
    gd_journal.record, gd_toast.show_toast = _real_journal, _real_toast
    gd_metrics.request_dns = _real_request_dns
    gd_rules._base_dir, gd_dns.state_path = _real_base, _real_state_path
    shutil.rmtree(_tmpdir, ignore_errors=True)
    win._rule_client = gd_main.MainWindow._rule_client.__get__(win)

# --- 4.3 клавиатура: сайдбар достижим, обход Tab в читаемом порядке ---------
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402

nav = list(win._nav_items.values())
check("сайдбар: все пункты достижимы с клавиатуры",
      bool(nav) and all(x.focusPolicy() != Qt.NoFocus for x in nav),
      f"{sum(1 for x in nav if x.focusPolicy() != Qt.NoFocus)} из {len(nav)}")

_ring, _seen, _keep = [], set(), []
_w = win
for _ in range(4000):
    _w = _w.nextInFocusChain()
    if _w is win or _w is None or id(_w) in _seen:
        break
    _seen.add(id(_w))
    _keep.append(_w)
    if _w.focusPolicy() != Qt.NoFocus and _w.isVisible() and _w.isEnabled():
        _ring.append(_w)


def _oname(x) -> str:
    return x.objectName() or x.metaObject().className()


check("обход Tab: меню первым, кнопки шапки и окна — последними",
      len(_ring) >= 8 and all(isinstance(x, gd_main.NavItem) for x in _ring[:6])
      and _oname(_ring[-1]) == "WinClose" and _oname(_ring[-7]) == "ModeChip",
      f"{[_oname(x) for x in _ring[:3]]} … {[_oname(x) for x in _ring[-3:]]}")

_hits = []
_item = nav[0]
_item.clicked.connect(lambda k: _hits.append(k))
QTest.keyClick(_item, Qt.Key_Return)
QTest.keyClick(_item, Qt.Key_Space)
check("сайдбар: Enter и Space на пункте работают как клик",
      _hits == [_item.key, _item.key], str(_hits))



win.hide()
pump(300)
check("окно: скрытие → трей-темп", gd_metrics._activity["visible"] is False,
      str(gd_metrics._activity))
win.show()
pump(300)
check("окно: показ → обычный темп", gd_metrics._activity["visible"] is True,
      str(gd_metrics._activity))

# переключение вкладок: activate() подменён заглушкой (сенсоры и опросы вкладок
# трогать не будем — тест про темп метрик, а не про живую систему)
real_act_gpu = win._gpu_page.activate
real_act_power = win._power_page.activate
win._gpu_page.activate = lambda: None
win._power_page.activate = lambda: None
try:
    win.tabs.setCurrentIndex(win._key_to_index["tab.gpu"])
    pump(400)
    check("окно: вкладка GPU → учащённый опрос (2 с)",
          gd_metrics._activity["gpu_focus"] is True and gd_metrics._ttls()[1] == 2.0,
          str(gd_metrics._activity))
    win.tabs.setCurrentIndex(win._key_to_index["tab.gamma"])
    pump(400)
    check("окно: другая вкладка → обычный опрос (5 с)",
          gd_metrics._activity["gpu_focus"] is False and gd_metrics._ttls()[1] == 5.0,
          str(gd_metrics._activity))
finally:
    win._gpu_page.activate = real_act_gpu
    win._power_page.activate = real_act_power
    win.tabs.setCurrentIndex(win._key_to_index["tab.gamma"])

# --- 4.4 CPU: состояние читается без компаньона, компаньон — только на запись --
import gd_power  # noqa: E402
import gd_power_service  # noqa: E402
import gd_power_ui  # noqa: E402

_snap = gd_power.state_snapshot()
_svc_status = gd_power_service.handle({"cmd": "status"})["data"]
check("CPU: снимок состояния совпадает с ответом компаньона на status",
      sorted(_snap) == sorted(_svc_status), f"{sorted(_snap)} vs {sorted(_svc_status)}")

_client = gd_power_ui.PowerClient(pipe=r"\\.\pipe\GammadrilonPower-" + str(os.getpid()))
_resp_no = _client.call_existing({"cmd": "ping"})
check("CPU: без живой сессии «спросить существующего» честно отказывает и никого не поднимает",
      _resp_no.get("ok") is False and _client._proc is None, str(_resp_no))
_resp_local = gd_power_ui.read_status(_client)
check("CPU: опрос статуса не поднимает компаньона — читает powercfg сам",
      _resp_local.get("ok") is True and _client._proc is None
      and "values" in (_resp_local.get("data") or {}),
      f"proc={_client._proc}, ok={_resp_local.get('ok')}")

_power = win._power_page
_power.client._conn = None
_power.client._proc = None
check("CPU: вкладка построена на старте, но процесса компаньона у неё нет",
      _power is not None and _power.service_alive() is False, str(_power.service_alive()))

_pui_src = open(os.path.join(ROOT, "gd_power_ui.py"), encoding="utf-8").read()
check("CPU: статус опрашивается только честным путём (read_status, без спавна)",
      _pui_src.count("read_status(self.client)") == 2
      and 'self.client.call({"cmd": "status"})' not in _pui_src,
      f"read_status: {_pui_src.count('read_status(self.client)')}")

# профиль от прошлой сессии без компаньона откатывается сам — один раз за сессию
_power._status_periodic.stop()      # опрос выключаем: проверяем логику напрямую
_power._sensor_timer.stop()
_hits = []
_real_leftover = gd_power_ui.PowerWindow._rollback_leftover
gd_power_ui.PowerWindow._rollback_leftover = lambda self: _hits.append(1)
try:
    _power._leftover_checked = False
    _power._refresh_done({"ok": True, "data": {"profile": "eco", "values": {}}})
    check("CPU: профиль от прошлой сессии без компаньона уходит в автооткат",
          _hits == [1], str(_hits))
    _power._refresh_done({"ok": True, "data": {"profile": "eco", "values": {}}})
    check("CPU: повторный опрос не запускает откат по кругу", _hits == [1], str(_hits))
    _power._refresh_done({"ok": False, "error": "нет powercfg"})
    check("CPU: недоступное чтение состояния показано честно, без автоотката",
          _power.status_name.text() == "состояние питания недоступно" and _hits == [1],
          f"{_power.status_name.text()!r}")
finally:
    gd_power_ui.PowerWindow._rollback_leftover = _real_leftover
    _power._leftover_checked = True

try:
    win.tray.hide()
    gd_metrics.stop()
    import gd_bg

    gd_bg.wait_all(1200)
except Exception:  # noqa: BLE001
    pass

print()
print(f"снимки фута: {off_name}, {on_name}")
print(("ВСЁ ОК" if not FAILS else "ПРОВАЛЫ: " + "; ".join(FAILS)), flush=True)
# UTF-8-лог: консоль PowerShell 5.1 искажает кириллицу в пайпе, а файл читается как есть
with open(os.path.join(OUT, "verify.log"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(LOG) + "\n")
print(f"LOG: {os.path.join(OUT, 'verify.log')}", flush=True)
print("RESULT: " + ("OK" if not FAILS else f"FAIL ({len(FAILS)})"), flush=True)
sys.exit(1 if FAILS else 0)
