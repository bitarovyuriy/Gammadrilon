# -*- coding: utf-8 -*-
"""ВРЕМЕННАЯ проверка этапа 8 (локальный API для инструментов): ядро и вкладка.

Запуск:  .venv\\Scripts\\python.exe _tmp_agent.py

Без админа и без изменений системы: изменяющие действия выполняются на
заглушках страниц (реальные профили CPU/GPU и DNS не трогаются). Токен, адрес
и аудит пишутся в свою папку %LOCALAPPDATA%\\Gammadrilon — как в бою.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

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

from PyQt5.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt5.QtWidgets import QApplication, QDialog  # noqa: E402

import gd_agent  # noqa: E402
import gd_i18n as i18n  # noqa: E402

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


def http(method: str, url: str, token: str = "", body=None):
    """Запрос к локальному API. Возвращает (код, объект) — даже для ошибок."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw}


def in_thread(fn, timeout: float = 40.0):
    """Выполнить запрос в отдельном потоке, пока GUI-поток крутит события.

    Это повторяет боевую схему: HTTP-поток ждёт мост, GUI-поток обрабатывает.
    """
    out: dict = {}

    def work():
        try:
            out["res"] = fn()
        except Exception as exc:  # noqa: BLE001
            out["res"] = ("EXC", f"{type(exc).__name__}: {exc}")

    t = threading.Thread(target=work, daemon=True)
    t.start()
    end = time.monotonic() + timeout
    while t.is_alive() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    t.join(timeout=1.0)
    return out.get("res")


print("=== ядро: валидация белого списка ===", flush=True)
ok_params, err = gd_agent.validate("cpu_profile", {"profile": "eco"})
check("валидация: cpu_profile=eco проходит", ok_params == {"profile": "eco"} and err is None,
      str((ok_params, err)))
_, err = gd_agent.validate("rm_rf", {})
check("валидация: неизвестное действие отклонено", err is not None and "rm_rf" in str(err), str(err))
_, err = gd_agent.validate("cpu_profile", {"profile": "turbo"})
check("валидация: чужой профиль отклонён", err is not None and "eco" in str(err), str(err))
_, err = gd_agent.validate("cpu_profile", {"profile": "eco", "shell": "cmd /c whoami"})
check("валидация: лишний параметр отклонён", err is not None and "shell" in str(err), str(err))
_, err = gd_agent.validate("gpu_limit", {"watts": 5000})
check("валидация: 5000 Вт вне диапазона", err is not None and "600" in str(err), str(err))
_, err = gd_agent.validate("gpu_limit", {"watts": True})
check("валидация: bool вместо числа отклонён", err is not None, str(err))
_, err = gd_agent.validate("gpu_limit", {"watts": "abc"})
check("валидация: «abc» вместо числа отклонён", err is not None, str(err))
clean, err = gd_agent.validate("gpu_limit", {"watts": "200"})
check("валидация: число строкой приводится (query)", clean == {"watts": 200} and err is None,
      str((clean, err)))
clean, err = gd_agent.validate("journal", {})
check("валидация: journal без n берёт умолчание 50", clean == {"n": 50}, str(clean))
_, err = gd_agent.validate("dns_set", {"provider": "custom"})
check("валидация: «custom» в DNS не предлагается агенту", err is not None, str(err))
kinds = {a.name: a.kind for a in gd_agent.ACTIONS}
check("белый список: destructive-действий нет (только read/mutate)",
      set(kinds.values()) == {"read", "mutate"}, str(sorted(set(kinds.values()))))

print("=== ядро: сервер, токен, аудит ===", flush=True)
runs: list = []


def fake_runner(action, params, mutate):
    runs.append((action, params, mutate))
    if action == "status":
        return {"ok": True, "data": {"hello": "world"}}
    if action == "cpu_profile":
        return {"ok": True, "data": {"applied": params["profile"]}}
    return {"ok": False, "error": "нет данных"}


server = gd_agent.AgentServer(runner=fake_runner, version="test")
started, start_err = server.start()
check("сервер: поднялся", started and server.port > 0, f"{started} {start_err} {server.port}")
check("сервер: слушает только 127.0.0.1", gd_agent.HOST == "127.0.0.1", gd_agent.HOST)
token = server.token()
check("токен: файл создан и непустой", os.path.exists(gd_agent.token_path()) and len(token) == 64,
      f"{gd_agent.token_path()} len={len(token)}")
api = gd_agent.read_api_file() or {}
check("конфиг: фактический порт записан в agent_api.json",
      int(api.get("port") or 0) == server.port, str(api))
check("конфиг: путь к токену указан рядом с адресом",
      str(api.get("token_file") or "").endswith("agent_token.txt"), str(api.get("token_file")))

base = f"http://127.0.0.1:{server.port}"
code, resp = http("GET", f"{base}/v1/ping", token)
check("HTTP: ping с токеном — 200", code == 200 and resp.get("ok"), f"{code} {resp}")
code, resp = http("GET", f"{base}/v1/ping")
check("HTTP: ping без токена — 401", code == 401 and not resp.get("ok"), f"{code} {resp}")
code, resp = http("GET", f"{base}/v1/ping", "0" * 64)
check("HTTP: ping с чужим токеном — 401", code == 401, f"{code} {resp}")
code, resp = http("GET", f"{base}/v1/actions", token)
names = {a["name"] for a in resp.get("data") or []}
check("HTTP: /v1/actions отдаёт белый список", code == 200 and names == set(kinds), str(sorted(names)))
code, resp = http("GET", f"{base}/v1/read/status", token)
check("HTTP: чтение status — 200 и данные исполнителя",
      code == 200 and resp.get("data", {}).get("hello") == "world", f"{code} {resp}")
check("HTTP: read-действие вызвано без подтверждения", runs[-1][2] is False, str(runs[-1]))
code, resp = http("GET", f"{base}/v1/read/cpu_profile", token)
check("HTTP: mutate через /v1/read — 400", code == 400, f"{code} {resp}")
code, resp = http("POST", f"{base}/v1/action/cpu_profile", token, {"profile": "eco"})
check("HTTP: mutate через POST — 200, runner получил mutate=True",
      code == 200 and runs[-1][2] is True and runs[-1][1] == {"profile": "eco"}, f"{code} {runs[-1]}")
code, resp = http("POST", f"{base}/v1/action/nope", token, {})
check("HTTP: неизвестное действие — 404", code == 404, f"{code} {resp}")
code, resp = http("POST", f"{base}/v1/action/cpu_profile", token, {"profile": "turbo"})
check("HTTP: неверный профиль — 400 (валидация)", code == 400, f"{code} {resp}")

req = urllib.request.Request(f"{base}/v1/action/status", data=b"{not json", method="POST")
req.add_header("Authorization", f"Bearer {token}")
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        code = r.status
except urllib.error.HTTPError as exc:
    code = exc.code
check("HTTP: битый JSON — 400", code == 400, str(code))
big = json.dumps({"pad": "x" * (70 * 1024)}).encode()
req = urllib.request.Request(f"{base}/v1/action/status", data=big, method="POST")
req.add_header("Authorization", f"Bearer {token}")
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        code = r.status
except urllib.error.HTTPError as exc:
    code = exc.code
check("HTTP: слишком большое тело — 413", code == 413, str(code))

audit = gd_agent.audit_tail(200)
decisions = {e.get("decision") for e in audit}
check("аудит: есть отказ по токену", "denied" in decisions and any(
    e.get("kind") == "auth" for e in audit), str(sorted(decisions)))
check("аудит: есть успешные вызовы с параметрами", any(
    e.get("decision") == "ok" and e.get("params") == {"profile": "eco"} for e in audit))
check("аудит: есть отказ валидации", any(e.get("decision") == "rejected" for e in audit))
check("аудит: записи только дописываются (журнал растёт)",
      len(audit) >= 6 and all("ts" in e and "action" in e for e in audit), str(len(audit)))
server.stop()
check("сервер: остановлен и адрес убран",
      not server.running() and not os.path.exists(gd_agent.api_path()))
check("аудит: старт и остановка записаны",
      any(e.get("decision") == "start" for e in gd_agent.audit_tail(200))
      and any(e.get("decision") == "stop" for e in gd_agent.audit_tail(200)))

print("=== окно: вкладка и мост в GUI-поток ===", flush=True)
import gd_agent_ui  # noqa: E402
import main as gd_main  # noqa: E402

# Тест не должен менять настройки пользователя: запись presets.json перехватываем
# (окно сохраняет флаг автостарта API), а сам файл сверяем байтами до/после.
_cfg_path = gd_main.config_path()
_cfg_before = _cfg_path.read_bytes() if _cfg_path.exists() else None
_cfg_writes: list = []


def _no_save(cfg=None, *a, **kw):
    _cfg_writes.append(cfg)


gd_main.save_config = _no_save

app.setStyleSheet(gd_main.build_app_stylesheet())
win = gd_main.MainWindow()
win.tray.hide()

orig_flag = bool((win.config.get("settings") or {}).get("agent_api", False))


class AutoDialog(QObject):
    """Подмена диалога подтверждения: сразу «Разрешить»."""

    finished = pyqtSignal(int)

    def __init__(self, spec, params, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.params = params

    def open(self):
        self.finished.emit(QDialog.Accepted)


class RejectDialog(AutoDialog):
    def open(self):
        self.finished.emit(QDialog.Rejected)


class HangDialog(AutoDialog):
    """Диалог, который «ждёт пользователя»: открыт, решения нет."""

    def open(self):
        pass


check("окно: tab.agent в стеке и сайдбаре",
      "tab.agent" in win._key_to_index and "tab.agent" in win._nav_items)
check("окно: индекс вкладки — 7 (жёсткий индекс палитры)",
      win._key_to_index.get("tab.agent") == 7, str(win._key_to_index.get("tab.agent")))
check("окно: имя вкладки — «API агентов»", i18n.t("tab.agent") == "API агентов", i18n.t("tab.agent"))
check("окно: команда палитры объявлена", bool(i18n.t("pal.open_agent")), i18n.t("pal.open_agent"))
apage = win._agent_page
check("окно: до включения сервер выключен и состояние показано честно",
      not win.agent_state()["running"] and apage.lbl_state.text() == "Выключен",
      apage.lbl_state.text())
check("окно: таблица действий заполнена белым списком",
      apage.table.rowCount() == len(gd_agent.ACTIONS), str(apage.table.rowCount()))
# вкладка синхронизирует состояние с окном при открытии (activate)
win.tabs.setCurrentIndex(win._key_to_index["tab.agent"])
pump(300)
check("окно: галка автостарта отражает конфиг",
      apage.chk_autostart.isChecked() == win.agent_autostart(),
      f"{apage.chk_autostart.isChecked()} vs {win.agent_autostart()}")

started, start_err = win.start_agent_api()
st = win.agent_state()
check("окно: сервер включён из вкладки", started and st["running"] and st["port"] > 0,
      f"{started} {start_err} {st}")
check("окно: вкладка показывает адрес", "Работает" in apage.lbl_state.text(), apage.lbl_state.text())
check("окно: включение запомнено в конфиге (автостарт)", win.agent_autostart() is True)

w_token = win._agent_server.token()
w_base = f"http://127.0.0.1:{st['port']}"
code, resp = in_thread(lambda: http("GET", f"{w_base}/v1/read/status", w_token))
data = resp.get("data") or {}
check("мост: status вернулся в HTTP-поток",
      code == 200 and data.get("app", {}).get("version") == gd_main.APP_VERSION, f"{code} {resp}")
check("мост: в сводке есть разделы cpu/gpu/dns/zapret",
      {"cpu", "gpu", "dns", "zapret"} <= set(data), str(sorted(data)))
code, resp = in_thread(lambda: http("GET", f"{w_base}/v1/read/sensors", w_token))
check("мост: sensors отвечают (значения могут быть пустыми)", code == 200, f"{code} {resp}")
code, resp = in_thread(lambda: http("GET", f"{w_base}/v1/read/journal?n=5", w_token))
check("мост: journal с n=5 (query-число)",
      code == 200 and isinstance(resp.get("data"), list) and len(resp["data"]) <= 5, f"{code} {resp}")
code, resp = in_thread(lambda: http("GET", f"{w_base}/v1/read/rules", w_token))
check("мост: rules отвечают списком", code == 200 and isinstance(resp.get("data"), list),
      f"{code} {resp}")
code, resp = in_thread(lambda: http("GET", f"{w_base}/v1/read/zapret", w_token), timeout=60)
zdata = resp.get("data") or {}
check("мост: снимок zapret сериализуется целиком",
      code == 200 and isinstance(zdata, dict) and "strategies" in zdata,
      f"{code} {str(resp)[:200]}")

# изменяющее действие: подтверждение + заглушка страницы (система не трогается)
calls: list = []


def fake_apply_profile(pid, source=None, on_done=None):
    calls.append((pid, source))
    gd_agent.finish(on_done, True)


win._power_page.apply_profile = fake_apply_profile
RealDialog = gd_agent_ui.AgentConfirmDialog      # вернуть перед проверкой EN
gd_agent_ui.AgentConfirmDialog = AutoDialog
code, resp = in_thread(lambda: http("POST", f"{w_base}/v1/action/cpu_profile", w_token, {"profile": "eco"}))
check("подтверждение: «Разрешить» → действие выполнено",
      code == 200 and resp.get("ok") and calls == [("eco", i18n.t("агент"))],
      f"{code} {resp} {calls}")

gd_agent_ui.AgentConfirmDialog = RejectDialog
calls.clear()
code, resp = in_thread(lambda: http("POST", f"{w_base}/v1/action/cpu_profile", w_token, {"profile": "eco"}))
check("подтверждение: «Отклонить» → 403 и действие не выполнено",
      code == 403 and not resp.get("ok") and not calls, f"{code} {resp} {calls}")

gd_agent_ui.AgentConfirmDialog = HangDialog
first: dict = {}
t1 = threading.Thread(
    target=lambda: first.update(
        res=http("POST", f"{w_base}/v1/action/cpu_profile", w_token, {"profile": "eco"})),
    daemon=True)
t1.start()
pump(800)      # первый запрос доходит до диалога и «ждёт пользователя»
code, resp = in_thread(
    lambda: http("POST", f"{w_base}/v1/action/cpu_profile", w_token, {"profile": "eco"}), timeout=15)
check("подтверждение: пока открыт один запрос, второй отклоняется",
      code == 403 and "запрос" in str(resp.get("error")), f"{code} {resp}")
win._agent_confirm_open = False
win._agent_confirm_dialog = None

gpu_calls: list = []


def fake_apply_limit(watts, source=None, on_done=None):
    gpu_calls.append((watts, source))
    gd_agent.finish(on_done, True)


win._gpu_page.apply_limit = fake_apply_limit
gd_agent_ui.AgentConfirmDialog = AutoDialog
code, resp = in_thread(lambda: http("POST", f"{w_base}/v1/action/gpu_limit", w_token, {"watts": 200}))
check("действия: gpu_limit=200 проходит после подтверждения",
      code == 200 and gpu_calls == [(200, i18n.t("агент"))], f"{code} {resp} {gpu_calls}")
check("действия: к ответу GPU добавлена честная приписка о 12 с",
      "12" in str(resp.get("note")), str(resp.get("note")))
code, resp = in_thread(lambda: http("POST", f"{w_base}/v1/action/gpu_limit", w_token, {"watts": 900}))
check("действия: gpu_limit=900 отклонён валидацией (до подтверждения)",
      code == 400 and not gpu_calls[1:], f"{code} {resp}")

dns_calls: list = []


def fake_set_provider(key, source=None, on_done=None):
    dns_calls.append((key, source))
    gd_agent.finish(on_done, True)


win._dns_page.set_provider = fake_set_provider
code, resp = in_thread(lambda: http("POST", f"{w_base}/v1/action/dns_set", w_token, {"provider": "cloudflare"}))
check("действия: dns_set=cloudflare проходит",
      code == 200 and dns_calls == [("cloudflare", i18n.t("агент"))], f"{code} {resp} {dns_calls}")

pump(300)
audit = gd_agent.audit_tail(200)
check("аудит окна: вкладка видит записи", apage.audit.count() > 0, str(apage.audit.count()))
check("аудит окна: источник записан как 127.0.0.1",
      any(e.get("remote") == "127.0.0.1" for e in audit))

# EN: вкладка и диалог не должны содержать русских строк (кроме данных аудита)
import re  # noqa: E402

from PyQt5.QtWidgets import QCheckBox, QLabel, QPushButton  # noqa: E402

i18n.set_language("en")
gd_agent_ui.AgentConfirmDialog = RealDialog       # подмены теста больше не нужны
try:
    w_en = gd_agent_ui.AgentWidget(standalone=True)
    cyr = []
    for lbl in w_en.findChildren(QLabel):
        if re.search(r"[А-Яа-яЁё]", lbl.text()):
            cyr.append(lbl.text())
    for btn in w_en.findChildren(QPushButton):
        if re.search(r"[А-Яа-яЁё]", btn.text()):
            cyr.append(btn.text())
    for chk in w_en.findChildren(QCheckBox):
        if re.search(r"[А-Яа-яЁё]", chk.text()):
            cyr.append(chk.text())
    for row in range(w_en.table.rowCount()):
        for col in (1, 2):     # «Действие» — имя команды, оно и должно быть латиницей
            item = w_en.table.item(row, col)
            if item is not None and re.search(r"[А-Яа-яЁё]", item.text()):
                cyr.append(item.text())
    check("EN: во вкладке «API агентов» нет русских подписей", not cyr, "; ".join(cyr)[:200])
    dlg_en = gd_agent_ui.AgentConfirmDialog(gd_agent.find("gpu_limit"), {"watts": 200})
    dlg_texts = [dlg_en.windowTitle(), dlg_en.countdown.text()]
    dlg_texts += [b.text() for b in dlg_en.findChildren(QPushButton)]
    dlg_cyr = [t for t in dlg_texts if re.search(r"[А-Яа-яЁё]", t)]
    check("EN: диалог подтверждения переведён", not dlg_cyr, "; ".join(dlg_cyr)[:200])
    dlg_en.reject()
    w_en.deleteLater()
finally:
    i18n.set_language("ru")

win.stop_agent_api()
check("окно: выключение останавливает сервер и убирает адрес",
      not win.agent_state()["running"] and not os.path.exists(gd_agent.api_path()))
check("окно: выключение сбрасывает автостарт в конфиге", win.agent_autostart() is False)
check("окно: вкладка показывает «Выключен»", apage.lbl_state.text() == "Выключен",
      apage.lbl_state.text())

# конфиг в памяти возвращаем как было, а файл на диске тест не трогал вовсе
win.config.setdefault("settings", {})["agent_api"] = orig_flag
win._config_pending = False
_cfg_writes.clear()
win.set_agent_autostart(False)          # привести к известному состоянию
win._flush_config_save()                # запись отложенная — сбрасываем сами
_cfg_writes.clear()
win.set_agent_autostart(False)          # значение не менялось — записи быть не должно
win._flush_config_save()
check("окно: флаг автостарта API молчит, когда значение не менялось",
      len(_cfg_writes) == 0, f"записей: {len(_cfg_writes)}")
win.set_agent_autostart(True)           # изменилось — конфиг помечен на запись
check("окно: флаг автостарта API меняет значение в конфиге",
      win.config["settings"]["agent_api"] is True,
      repr(win.config["settings"]["agent_api"]))
win._flush_config_save()
check("окно: флаг автостарта API пишется в конфиг одной записью",
      len(_cfg_writes) == 1, f"записей: {len(_cfg_writes)}")
win.set_agent_autostart(orig_flag)
win._config_save_timer.stop()           # отложенную запись не выпускаем: файл не трогаем
win._config_pending = False
_cfg_after = _cfg_path.read_bytes() if _cfg_path.exists() else None
check("конфиг: presets.json пользователя не тронут (запись перехвачена)",
      _cfg_after == _cfg_before,
      f"попыток записи: {len(_cfg_writes)}, файл изменён: {_cfg_after != _cfg_before}")

try:
    win.tray.hide()
    import gd_metrics

    gd_metrics.stop()
    import gd_bg

    gd_bg.wait_all(1500)
except Exception:  # noqa: BLE001
    pass

print(("ВСЕ ОК" if not FAILS else "ПРОВАЛЫ: " + "; ".join(FAILS)), flush=True)
with open(os.path.join(HERE, "build", "shots", "agent.log"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(LOG) + "\n")
print("RESULT: " + ("OK" if not FAILS else f"FAIL ({len(FAILS)})"), flush=True)
sys.exit(1 if FAILS else 0)





