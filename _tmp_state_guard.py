# -*- coding: utf-8 -*-
"""ВРЕМЕННАЯ проверка «честных state-файлов»: атомарная запись, восстановление, отказы.

Всё гоняется на временном каталоге (TEMP): пути модулей подменяются, реальные
state-файлы в %ProgramData% и presets.json не трогаются.

  1. gd_state: атомарная запись/перезапись, терпимое чтение, карантин в .bak, отказ без исключений;
  2. gd_rules: битый файл → копия .bak + RECOVERY + честная заметка; мусор фильтруется;
     save_rules → bool; обрезка лога атомарна;
  3. gd_rules_ui: отказ сохранения виден в lbl_status; заметка о восстановлении — один раз;
  4. gd_dns: save_state → bool, remember_before_change → (bool, dict), отказ не бросает;
  5. gd_profiles: save_quiet → bool; битый quiet-файл → дефолты;
  6. gd_zapret: save_our_state → bool; битый state → None;
  7. gd_agent: _write_api_file → bool, файл валиден;
  8. gd_gpu_ui: _write_setting_file → bool; битый presets.json не затирается;
  9. gd_journal: обрезка журнала атомарна и держит хвост;
 10. импорт настроек: отказ записи честно назван, успеха не обещаем;
 11. журнал: отказ записи назван причиной и один раз говорит тостом, probe и clear честные,
     окно «Что произошло» показывает путь, число записей и повреждённые строки.
 12. питание: файл состояния различает «профиля нет» и «файл непригоден», неполный откат
     не выдаётся за успех и оставляет файл для повтора, компаньон называет провал в журнале,
     вкладка CPU показывает непригодный файл и наши копии схем, найденные по метке.
 13. список процессов: «не прочитан» отличается от «процессов нет» и в коде, и в диалоге.

Запуск:  .venv\\Scripts\\python.exe _tmp_state_guard.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
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

import gd_agent  # noqa: E402
import gd_dns  # noqa: E402
import gd_gpu_ui  # noqa: E402
import gd_i18n as i18n  # noqa: E402
import gd_journal  # noqa: E402
import gd_journal_ui  # noqa: E402
import gd_profiles  # noqa: E402
import gd_rules  # noqa: E402
import gd_state  # noqa: E402
import gd_toast  # noqa: E402
import gd_zapret  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(("ок      " if ok else "ПРОВАЛ  ") + name + (f" — {extra}" if extra else ""),
          flush=True)
    if not ok:
        FAILS.append(name)


app = QApplication([])
tmp = Path(tempfile.mkdtemp(prefix="gd_state_guard_"))


def blocked(name: str) -> Path:
    """Путь «внутри файла»: mkdir/запись гарантированно откажут (файл вместо каталога)."""
    guard = tmp / f"{name}_blocker"
    guard.write_text("x", encoding="utf-8")
    return guard / "state.json"


# --- 1. gd_state --------------------------------------------------------------
target = tmp / "state.json"
check("gd_state: запись атомарна",
      gd_state.write_json_atomic(target, {"a": 1}) is True)
check("gd_state: файл валиден, .tmp не остался",
      json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
      and not (tmp / "state.json.tmp").exists())
check("gd_state: перезапись заменяет содержимое",
      gd_state.write_json_atomic(target, {"b": 2}) is True
      and json.loads(target.read_text(encoding="utf-8")) == {"b": 2})
check("gd_state: отказ записи — честный False, без исключений",
      gd_state.write_json_atomic(blocked("state"), {"x": 1}) is False)

bad_json = tmp / "bad.json"
bad_json.write_text("{oops", encoding="utf-8")
check("gd_state: чтение терпимо к битому и отсутствующему",
      gd_state.read_json(tmp / "nope.json", "def") == "def"
      and gd_state.read_json(bad_json, "def") == "def"
      and gd_state.read_json(target, None) == {"b": 2})

q = tmp / "q.json"
q.write_text("{oops1", encoding="utf-8")
backup = gd_state.quarantine(q)
check("gd_state: карантин переносит файл в .bak",
      backup == tmp / "q.json.bak" and backup.exists() and not q.exists()
      and backup.read_text(encoding="utf-8") == "{oops1")
q.write_text("{oops2", encoding="utf-8")
backup2 = gd_state.quarantine(q)
check("gd_state: повторный карантин заменяет старую копию (важна последняя)",
      backup2 is not None and backup2.read_text(encoding="utf-8") == "{oops2")
check("gd_state: карантин отсутствующего файла — None, без исключений",
      gd_state.quarantine(tmp / "missing.json") is None)

# --- 2. gd_rules --------------------------------------------------------------
rules_dir = tmp / "rules"
rules_dir.mkdir()
gd_rules.rules_path = lambda: rules_dir / "rules.json"
gd_rules.log_path = lambda: rules_dir / "rules_log.jsonl"

(rules_dir / "rules.json").write_text("{битый", encoding="utf-8")
gd_rules.RECOVERY = ""
loaded = gd_rules.load_rules()
check("правила: битый файл → пусто + копия .bak + честный RECOVERY",
      loaded == [] and gd_rules.RECOVERY == "backup"
      and (rules_dir / "rules.json.bak").exists()
      and not (rules_dir / "rules.json").exists())
check("правила: заметка о восстановлении не пуста", bool(gd_rules.recovery_note()))

(rules_dir / "rules.json").write_text(
    json.dumps([{"name": "ok"}, "junk", 5, {"enabled": True}]), encoding="utf-8")
gd_rules.RECOVERY = ""
check("правила: мусорные записи отброшены, объекты остались",
      gd_rules.load_rules() == [{"name": "ok"}, {"enabled": True}]
      and gd_rules.RECOVERY == "")

(rules_dir / "rules.json").write_text(json.dumps({"rules": []}), encoding="utf-8")
gd_rules.RECOVERY = ""
check("правила: корень не список → копия .bak + честный RECOVERY",
      gd_rules.load_rules() == [] and gd_rules.RECOVERY == "backup"
      and (rules_dir / "rules.json.bak").exists())

gd_rules.RECOVERY = ""
check("правила: сохранение атомарно и честно True",
      gd_rules.save_rules([{"name": "a"}]) is True
      and json.loads((rules_dir / "rules.json").read_text(encoding="utf-8"))
      == [{"name": "a"}]
      and not (rules_dir / "rules.json.tmp").exists())

blocked_rules = tmp / "rules_blocked"
blocked_rules.write_text("x", encoding="utf-8")      # файл вместо каталога
rules_path_ok = gd_rules.rules_path
gd_rules.rules_path = lambda: blocked_rules / "rules.json"
check("правила: отказ записи — честный False, без исключений",
      gd_rules.save_rules([{"name": "b"}]) is False)
gd_rules.rules_path = rules_path_ok

log = rules_dir / "rules_log.jsonl"
log.write_text("".join(
    json.dumps({"ts": f"2026-01-01T00:00:{i % 60:02d}", "rule": f"r{i}",
                "action": "a", "result": "ok"}, ensure_ascii=False) + "\n"
    for i in range(gd_rules.MAX_FIRES + 5)), encoding="utf-8")
gd_rules.log_fire({"name": "new", "id": "x"}, "ok")
lines = log.read_text(encoding="utf-8").splitlines()
check("правила: лог обрезается до 100 и остаётся валидным",
      len(lines) == gd_rules.MAX_FIRES
      and json.loads(lines[-1])["rule"] == "new")

log_path_ok = gd_rules.log_path
gd_rules.log_path = lambda: blocked_rules / "log.jsonl"
try:
    gd_rules.log_fire({"name": "x"}, "ok")
    log_fail_ok = True
except Exception as exc:  # noqa: BLE001
    log_fail_ok = f"исключение: {exc!r}"
check("правила: отказ записи лога не бросает", log_fail_ok is True, str(log_fail_ok))
gd_rules.log_path = log_path_ok

rules_empty = tmp / "rules_empty"
rules_empty.mkdir()
gd_rules.rules_path = lambda: rules_empty / "rules.json"
gd_rules.RECOVERY = ""
check("правила: нет файла — пусто без заметки",
      gd_rules.load_rules() == [] and gd_rules.RECOVERY == ""
      and gd_rules.recovery_note() == "")
gd_rules.rules_path = rules_path_ok

# --- 3. gd_rules_ui: честные заметки вкладки ---------------------------------
import gd_rules_ui  # noqa: E402

widget = gd_rules_ui.RulesWidget()

real_save = gd_rules.save_rules
gd_rules.save_rules = lambda rules: False
widget._save()
check("вкладка «Правила»: отказ записи виден в статусе",
      not widget.lbl_status.isHidden()
      and widget.lbl_status.text() == i18n.t(
          "Правила не сохранены: файл правил недоступен для записи. "
          "Изменение будет потеряно при выходе."),
      repr(widget.lbl_status.text()))

gd_rules.save_rules = real_save
widget._save()
check("вкладка «Правила»: успешное сохранение убирает тревожную заметку",
      widget.lbl_status.text() == "" and widget.lbl_status.isHidden(),
      repr(widget.lbl_status.text()))

gd_rules.RECOVERY = "backup"
widget._recovery_shown = False
widget.reload()
first_note = widget.lbl_status.text()
widget._show_note("")                # очистим — проверим, что второй раз не повторится
widget.reload()
check("вкладка «Правила»: о восстановлении сказано один раз",
      first_note == gd_rules.recovery_note() and widget.lbl_status.text() == "",
      repr((first_note, widget.lbl_status.text())))
gd_rules.RECOVERY = ""
widget.close()

# --- 4. gd_dns: откат не молчит ----------------------------------------------
dns_dir = tmp / "dns"
dns_dir.mkdir()
gd_dns.state_path = lambda: dns_dir / "dns_state.json"
gd_dns.current_state = lambda: {"iface": "Ethernet", "mode": "dhcp", "servers": []}
check("DNS: save_state — честный True и валидный файл",
      gd_dns.save_state() is True
      and json.loads((dns_dir / "dns_state.json").read_text(encoding="utf-8"))["iface"]
      == "Ethernet")
ok, before = gd_dns.remember_before_change()
check("DNS: remember_before_change — (bool, «как было»)",
      ok is True and before.get("iface") == "Ethernet")

gd_dns.state_path = lambda: blocked("dns") / "dns_state.json"
check("DNS: отказ записи состояния — честный False",
      gd_dns.save_state() is False)
ok2, before2 = gd_dns.remember_before_change()
check("DNS: отказ запоминания виден вызывающему (False + прочитанная конфигурация)",
      ok2 is False and before2.get("iface") == "Ethernet",
      f"({ok2!r}, {before2!r})")

# --- 5. gd_profiles: «тихий час» ----------------------------------------------
prof_dir = tmp / "prof"
prof_dir.mkdir()
gd_profiles.quiet_path = lambda: prof_dir / "quiet_hour.json"
check("тихий час: сохранение — честный True",
      gd_profiles.save_quiet({"enabled": True, "start": "23:00",
                              "end": "08:00"}) is True
      and json.loads((prof_dir / "quiet_hour.json").read_text(encoding="utf-8"))["enabled"]
      is True)
gd_profiles.quiet_path = lambda: blocked("quiet") / "quiet.json"
check("тихий час: отказ записи — честный False",
      gd_profiles.save_quiet({}) is False)
gd_profiles.quiet_path = lambda: prof_dir / "quiet_hour.json"
(prof_dir / "quiet_hour.json").write_text("{oops", encoding="utf-8")
check("тихий час: битый файл → дефолты без исключений",
      gd_profiles.load_quiet() == dict(gd_profiles.DEFAULT_QUIET))

# --- 6. gd_zapret: запущенный процесс -----------------------------------------
zap_dir = tmp / "zapret"
zap_dir.mkdir()
gd_zapret.STATE_FILE = zap_dir / "zapret_state.json"
check("zapret: save_our_state — честный True и валидный файл",
      gd_zapret.save_our_state("C:/x", "st", 1234, ["--a"]) is True
      and json.loads(gd_zapret.STATE_FILE.read_text(encoding="utf-8"))["pid"] == 1234)
check("zapret: our_state читает записанное",
      (gd_zapret.our_state() or {}).get("pid") == 1234)
gd_zapret.STATE_FILE = blocked("zapret") / "state.json"
check("zapret: отказ записи — честный False",
      gd_zapret.save_our_state("C:/x", "st", 1, []) is False)
bad_state = zap_dir / "bad_state.json"
bad_state.write_text("{oops", encoding="utf-8")
gd_zapret.STATE_FILE = bad_state
check("zapret: битый state → None, без исключений",
      gd_zapret.our_state() is None)

# --- 7. gd_agent: адрес локального API ----------------------------------------
agent_dir = tmp / "agent"
agent_dir.mkdir()
gd_agent.api_path = lambda: str(agent_dir / "agent_api.json")
check("API: адрес пишется атомарно и честно",
      gd_agent._write_api_file(8123, "9.9.9") is True
      and json.loads((agent_dir / "agent_api.json").read_text(encoding="utf-8"))["port"]
      == 8123
      and not (agent_dir / "agent_api.json.tmp").exists())
gd_agent.api_path = lambda: str(blocked("agent") / "api.json")
check("API: отказ записи адреса — честный False",
      gd_agent._write_api_file(1, "x") is False)

# --- 8. gd_gpu_ui: настройки standalone-окна ----------------------------------
gpu_dir = tmp / "gpu"
gpu_dir.mkdir()
gpu_presets = gpu_dir / "presets.json"
gd_gpu_ui._presets_path = lambda: str(gpu_presets)
check("GPU standalone: файла нет — настройка сохраняется и создаёт файл",
      gd_gpu_ui._write_setting_file("gpu_keep_on_exit", False) is True
      and json.loads(gpu_presets.read_text(encoding="utf-8"))["settings"]["gpu_keep_on_exit"]
      is False)
check("GPU standalone: повторная запись сохраняет прежние ключи",
      gd_gpu_ui._write_setting_file("gpu_apply_on_start", True) is True
      and set(json.loads(gpu_presets.read_text(encoding="utf-8"))["settings"])
      == {"gpu_keep_on_exit", "gpu_apply_on_start"})
gpu_presets.write_text("{oops", encoding="utf-8")
check("GPU standalone: битый presets.json не затирается — честный False",
      gd_gpu_ui._write_setting_file("gpu_keep_on_exit", True) is False
      and gpu_presets.read_text(encoding="utf-8") == "{oops")

# --- 9. gd_journal: обрезка журнала -------------------------------------------
jrn = tmp / "journal.jsonl"
jrn.write_text("".join(json.dumps({"i": i}) + "\n"
                       for i in range(gd_journal.MAX_LINES + 5)), encoding="utf-8")
gd_journal._trim(jrn)
jlines = jrn.read_text(encoding="utf-8").splitlines()
check("журнал: обрезка атомарна и держит хвост",
      len(jlines) == gd_journal.MAX_LINES
      and json.loads(jlines[-1])["i"] == gd_journal.MAX_LINES + 4)
gd_journal.journal_path = lambda: jrn
gd_journal.record("startup", "test-probe", why="probe")
check("журнал: событие записывается и файл остаётся валидным",
      json.loads(jrn.read_text(encoding="utf-8").splitlines()[-1])["target"]
      == "test-probe")

# --- 10. импорт настроек: отказ записи не прячется -----------------------------
imp_dir = tmp / "import"
imp_dir.mkdir()
imp_file = imp_dir / "export.json"
imp_file.write_text(json.dumps({
    "app": gd_profiles.APP_TAG, "format": gd_profiles.FORMAT_VERSION,
    "rules": [{"name": "r1"}],
}), encoding="utf-8")
gd_rules.rules_path = lambda: blocked_rules / "rules.json"
ok_imp, msg_imp = gd_profiles.import_all(imp_file)
check("импорт: отказ записи честно назван, успеха не обещаем",
      ok_imp is False and msg_imp == i18n.t(
          "Не сохранено: {v0} — файл недоступен для записи.", v0=i18n.t("правила")),
      repr(msg_imp))
gd_rules.rules_path = rules_path_ok
ok_imp2, msg_imp2 = gd_profiles.import_all(imp_file)
check("импорт: успех — правила записаны и видны в файле",
      ok_imp2 is True
      and json.loads((rules_dir / "rules.json").read_text(encoding="utf-8"))
      == [{"name": "r1"}],
      repr(msg_imp2))

# --- 11. журнал: честная запись и ясное окно -----------------------------------
jdir = tmp / "journal_ok"
jdir.mkdir()
jrn2 = jdir / "journal.jsonl"
gd_journal.journal_path = lambda: jrn2
gd_journal._last_error, gd_journal._warned = "", False

TOASTS: list[tuple] = []
gd_toast.show_toast = lambda text, kind="ok": TOASTS.append((text, kind))

gd_journal.record("startup", "первое")
check("журнал: успешная запись не оставляет ошибки", gd_journal.write_error() == "")
check("журнал: probe подтверждает, что писать можно", gd_journal.probe() == (True, ""))
rep = gd_journal.read_report()
check("журнал: сводка видит запись",
      rep["total"] == 1 and len(rep["items"]) == 1 and rep["broken"] == 0)

with jrn2.open("a", encoding="utf-8") as f:
    f.write("{не json}\n")
rep = gd_journal.read_report()
check("журнал: повреждённая строка названа, а не проглочена",
      rep["broken"] == 1 and rep["total"] == 2 and len(rep["items"]) == 1
      and gd_journal.recent() == rep["items"])
check("журнал: сводка для API называет повреждённые строки",
      gd_journal.state_note() == i18n.t("повреждено строк: {v0} — они пропущены.", v0=1),
      repr(gd_journal.state_note()))

jblock = tmp / "journal_blocked"
jblock.mkdir()
(jblock / "journal.jsonl").mkdir()          # «файл», который на деле каталог
gd_journal.journal_path = lambda: jblock / "journal.jsonl"
gd_journal._warned = False
entry = gd_journal.record("startup", "отказ")
check("журнал: отказ записи не бросает и назван причиной",
      entry.get("target") == "отказ" and gd_journal.write_error() != "",
      repr(gd_journal.write_error()))
ok_pr, err_pr = gd_journal.probe()
check("журнал: probe честно говорит «писать нельзя»", ok_pr is False and err_pr != "")
check("журнал: о мёртвом журнале сказано тостом один раз",
      len(TOASTS) == 1 and TOASTS[0][1] == "warn" and gd_journal._warned is True,
      repr(TOASTS))
gd_journal.record("startup", "второй отказ")
check("журнал: второй отказ тост не повторяет", len(TOASTS) == 1)
check("журнал: clear при отказе — честный False", gd_journal.clear() is False)
check("журнал: сводка для API объясняет молчание записи",
      gd_journal.state_note() == i18n.t(
          "журнал недоступен для записи: {v0} — история может быть неполной.",
          v0=gd_journal.write_error()),
      repr(gd_journal.state_note()))

dlg = gd_journal_ui.JournalDialog()
check("окно журнала: отказ записи виден тревожной заметкой",
      not dlg.lbl_status.isHidden()
      and dlg.lbl_status.text() == i18n.t(
          "Журнал недоступен для записи: {v0}. Новые изменения в него не попадут.",
          v0=gd_journal.write_error()),
      repr(dlg.lbl_status.text()))
dlg._clear()
check("окно журнала: неудачная очистка названа, а не молчит",
      not dlg.lbl_status.isHidden()
      and dlg.lbl_status.text() == i18n.t(
          "не удалось очистить журнал: {v0}", v0=gd_journal.write_error()),
      repr(dlg.lbl_status.text()))
dlg.close()

gd_journal.journal_path = lambda: jrn2
dlg2 = gd_journal_ui.JournalDialog()
check("окно журнала: повреждённые строки названы",
      dlg2.lbl_status.text() == i18n.t("повреждено строк: {v0} — они пропущены.", v0=1),
      repr(dlg2.lbl_status.text()))
dlg2._clear()
check("окно журнала: очистка живого журнала отчитывается честно",
      dlg2.lbl_status.text() == i18n.t("Журнал очищен.")
      and jrn2.read_text(encoding="utf-8") == "",
      repr(dlg2.lbl_status.text()))
dlg2.reload()
check("окно журнала: путь и число записей видны",
      dlg2.lbl_status.text() == i18n.t("файл журнала: {v0} · записей: {v1}",
                                       v0=str(jrn2), v1=0)
      and dlg2.list.count() == 0,
      repr(dlg2.lbl_status.text()))
gd_journal.record("startup", "после очистки")
gd_journal.record("restore", "возврат")
dlg2.reload()
check("окно журнала: число записей совпадает с файлом",
      dlg2.lbl_status.text() == i18n.t("файл журнала: {v0} · записей: {v1}",
                                       v0=str(jrn2), v1=2)
      and dlg2.list.count() == 2,
      repr(dlg2.lbl_status.text()))
dlg2.close()

# --- 12. питание: файл состояния и остаток профиля ------------------------------
import gd_bg  # noqa: E402
import gd_memory  # noqa: E402
import gd_power  # noqa: E402
import gd_power_service  # noqa: E402
import gd_power_ui  # noqa: E402
import gd_utils_ui  # noqa: E402

pdir = tmp / "power"
pdir.mkdir()
PSTATE = pdir / "power_state.json"
gd_power.STATE_FILE = str(PSTATE)

# всё, что меняет систему, подменяется: живая схема питания не трогается
SWITCHES: list[tuple] = []
CLONES: list[dict] = []
gd_power.get_active_scheme = lambda: "ACTIVE"
gd_power.read_values = lambda g: {}
gd_power.scheme_name = lambda g: g
gd_power.set_active_scheme = lambda g: (SWITCHES.append(("set", g)), True)[1]
gd_power.delete_scheme = lambda g: (SWITCHES.append(("del", g)), True)[1]
gd_power.list_clones = lambda: list(CLONES)

check("питание: нет файла — это «профиль не применён», а не проблема",
      gd_power.read_state_report() == (None, "")
      and gd_power.state_problem() == "" and gd_power.load_state() is None)

gd_power.save_state({"version": 1, "original_scheme": "ORIG", "clone_scheme": "CLONE",
                     "profile": "eco"})
st_ok, problem_ok = gd_power.read_state_report()
check("питание: целый файл читается без замечаний о себе",
      st_ok["clone_scheme"] == "CLONE" and problem_ok == "")

PSTATE.write_text("{обрезанный json", encoding="utf-8")
st_bad, problem_bad = gd_power.read_state_report()
check("питание: битый файл назван причиной, а не молчаливым None",
      st_bad is None and problem_bad.startswith("JSON:"), repr(problem_bad))
check("питание: снимок состояния говорит о непригодном файле",
      gd_power.state_snapshot().get("state_problem") == problem_bad
      and gd_power.state_snapshot()["profile"] is None)

rep_bad = gd_power.restore_report()
check("питание: откат по битому файлу не выдумывает успех",
      rep_bad["ok"] is False and rep_bad["applied"] is False
      and rep_bad["note"] == i18n.t("файл состояния питания непригоден: {v0}",
                                    v0=problem_bad),
      repr(rep_bad))

CLONES[:] = [{"guid": "C1", "name": "Gammadrilon — Экономия", "active": True}]
rep_cl = gd_power.restore_report()
check("питание: оставшаяся копия схемы видна даже без файла состояния",
      rep_cl["ok"] is False and [c["guid"] for c in rep_cl["clones"]] == ["C1"]
      and i18n.t("в системе осталась наша копия схемы питания: {v0}",
                 v0="Gammadrilon — Экономия") in rep_cl["note"],
      repr(rep_cl["note"]))
CLONES.clear()

gd_power.save_state({"version": 1, "original_scheme": "ORIG", "clone_scheme": "CLONE",
                     "profile": "eco"})
SWITCHES.clear()
rep_ok = gd_power.restore_report()
check("питание: успешный откат возвращает схему, убирает клон и файл",
      rep_ok == {"ok": True, "applied": True, "note": "", "clones": []}
      and SWITCHES == [("set", "ORIG"), ("del", "CLONE")] and not PSTATE.exists(),
      f"{rep_ok} · {SWITCHES}")

gd_power.save_state({"version": 1, "original_scheme": "ORIG", "clone_scheme": "CLONE",
                     "profile": "eco"})
gd_power.delete_scheme = lambda g: False
rep_part = gd_power.restore_report()
check("питание: неполный откат назван, а файл состояния оставлен для повтора",
      rep_part["ok"] is False and rep_part["applied"] is True
      and rep_part["note"] == i18n.t("не удалось удалить клон схемы {v0}", v0="CLONE")
      and PSTATE.exists(),
      f"{rep_part} · файл есть: {PSTATE.exists()}")
gd_power.delete_scheme = lambda g: (SWITCHES.append(("del", g)), True)[1]

STATE_DIR_BLOCK = pdir / "state_as_dir.json"
STATE_DIR_BLOCK.mkdir()
gd_power.STATE_FILE = str(STATE_DIR_BLOCK)
check("питание: clear_state честно говорит, что файл остался",
      gd_power.clear_state() is False)
gd_power.STATE_FILE = str(pdir / "missing.json")
check("питание: отсутствующий файл состояния — успех, а не отказ",
      gd_power.clear_state() is True)
gd_power.STATE_FILE = str(STATE_DIR_BLOCK)
rep_clear = gd_power.restore_report({"original_scheme": "ORIG", "clone_scheme": "CLONE"})
check("питание: неудачная уборка файла состояния названа",
      rep_clear["ok"] is False
      and rep_clear["note"] == i18n.t("не удалось убрать файл состояния {v0}",
                                      v0=gd_power.STATE_FILE),
      repr(rep_clear))

# компаньон: провал отката уходит в журнал — окна и тоста у него нет
JRN: list[tuple] = []
REAL_RECORD = gd_journal.record
gd_journal.record = lambda *a, **k: JRN.append((a, k))
gd_power.STATE_FILE = str(PSTATE)
gd_power.save_state({"version": 1, "original_scheme": "ORIG", "clone_scheme": "CLONE",
                     "profile": "eco"})
gd_power.delete_scheme = lambda g: False      # откат сломается на удалении клона
resp_svc = gd_power_service.handle({"cmd": "restore"})
check("компаньон: провал отката назван окну и записан в журнал",
      resp_svc["ok"] is False and resp_svc["error"] == resp_svc["data"]["note"]
      and JRN and JRN[-1][1]["source"] == i18n.t("компаньон")
      and "CLONE" in JRN[-1][1]["why"],
      repr(resp_svc))
gd_power.delete_scheme = lambda g: (SWITCHES.append(("del", g)), True)[1]
PSTATE.unlink(missing_ok=True)
gd_power.save_state({"version": 1, "original_scheme": "ORIG", "clone_scheme": "CLONE",
                     "profile": "eco"})
resp_svc_ok = gd_power_service.handle({"cmd": "restore"})
check("компаньон: успешный откат отвечает отчётом, а не одним bool",
      resp_svc_ok["ok"] is True and resp_svc_ok["data"]["applied"] is True
      and resp_svc_ok["data"]["note"] == "" and not PSTATE.exists(),
      repr(resp_svc_ok))
gd_journal.record = REAL_RECORD

# вкладка: непригодный файл, провал автоотката и найденные копии видны текстом
pwr = gd_power_ui.PowerWindow(standalone=False)
pwr._sensor_timer.stop()
pwr._status_periodic.stop()
pwr._refresh_status = lambda: None          # детерминированность: без фоновых опросов
pwr._scan_clones = lambda: None
check("вкладка CPU: кнопка уборки копий скрыта, пока копий не нашли",
      pwr.btn_clean_clones.isHidden())

pwr._refresh_done({"ok": True, "data": {"profile": None, "values": {},
                                        "state_problem": "JSON: Expecting value"}})
check("вкладка CPU: непригодный файл состояния назван, а не проглочен",
      pwr.bottom_status.text() == i18n.t(
          "Файл состояния питания нечитаем ({v0}) — остаток профиля мог остаться в системе.",
          v0="JSON: Expecting value"),
      repr(pwr.bottom_status.text()))
pwr.bottom_status.setText("")
pwr._refresh_done({"ok": True, "data": {"profile": None, "values": {},
                                        "state_problem": "JSON: Expecting value"}})
check("вкладка CPU: о непригодном файле сказано один раз, а не каждый опрос",
      pwr.bottom_status.text() == "", repr(pwr.bottom_status.text()))

pwr._clones_done([{"guid": "C1", "name": "Gammadrilon — Экономия", "active": False}])
check("вкладка CPU: найденная копия схемы видна и названа в подсказке",
      not pwr.btn_clean_clones.isHidden()
      and pwr.bottom_status.text() == i18n.t(
          "В системе остались наши копии схем питания: {v0}. Это след прошлых "
          "сессий — их можно убрать.", v0=1)
      and "Gammadrilon — Экономия" in pwr.btn_clean_clones.toolTip(),
      repr(pwr.bottom_status.text()))

pwr._rollback_done({"ok": False, "applied": True,
                    "note": "не удалось удалить клон схемы CLONE", "clones": []})
check("вкладка CPU: провал автоотката назван причиной",
      pwr.bottom_status.text() == i18n.t(
          "Профиль прошлой сессии откатить не удалось: {v0}",
          v0="не удалось удалить клон схемы CLONE"),
      repr(pwr.bottom_status.text()))
pwr._rollback_done({"ok": True, "applied": False, "note": "", "clones": []})
check("вкладка CPU: «откатывать нечего» не выдаётся за провал",
      pwr.bottom_status.text() == i18n.t("Следов профиля не осталось."),
      repr(pwr.bottom_status.text()))
pwr._clones_cleaned({"removed": ["C1"], "failed": {}})
check("вкладка CPU: уборка копий отчитывается и прячет кнопку",
      pwr.bottom_status.text() == i18n.t("Убрано своих копий схем: {v0}.", v0=1)
      and pwr.btn_clean_clones.isHidden(),
      repr(pwr.bottom_status.text()))
pwr._clones_cleaned({"removed": [], "failed": {"C2": i18n.t(
    "копия сейчас активна: Windows не даст её удалить — сначала выберите "
    "другую схему питания")}})
check("вкладка CPU: неудачная уборка копии названа, кнопка остаётся",
      not pwr.btn_clean_clones.isHidden() and i18n.t(
          "копия сейчас активна: Windows не даст её удалить — сначала выберите "
          "другую схему питания") in pwr.bottom_status.text(),
      repr(pwr.bottom_status.text()))
pwr.close()

# --- 13. список процессов: пусто ≠ отказ --------------------------------------
rows_proc, err_proc = gd_memory.list_processes_report(5)
check("процессы: живой список читается, отсортирован и без причины отказа",
      len(rows_proc) == 5 and err_proc == ""
      and all(rows_proc[i]["rss"] >= rows_proc[i + 1]["rss"]
              for i in range(len(rows_proc) - 1)),
      f"строк {len(rows_proc)}, причина {err_proc!r}")
rows_compat = gd_memory.list_processes(5)
check("процессы: совместимая функция отдаёт тот же список",
      len(rows_compat) == 5
      and all(set(r) == {"pid", "name", "rss"} for r in rows_compat)
      and all(rows_compat[i]["rss"] >= rows_compat[i + 1]["rss"]
              for i in range(len(rows_compat) - 1)),
      f"строк {len(rows_compat)}")

REAL_PSUTIL = gd_memory.psutil
gd_memory.psutil = None
rows_no, err_no = gd_memory.list_processes_report(5)
check("процессы: без psutil отказ назван, а не выглядит как «процессов нет»",
      rows_no == [] and err_no == i18n.t("нет модуля psutil — список процессов недоступен"),
      repr(err_no))


class _BoomPsutil:
    @staticmethod
    def process_iter(_attrs):
        raise OSError("отказано в доступе")


gd_memory.psutil = _BoomPsutil
rows_boom, err_boom = gd_memory.list_processes_report(5)
check("процессы: отказ перебора процессов назван причиной",
      rows_boom == [] and err_boom.startswith("OSError:"), repr(err_boom))
gd_memory.psutil = REAL_PSUTIL

dlg_proc = gd_utils_ui.ProcessPicker()
gd_bg.wait_all(3000)
app.processEvents()
dlg_proc._fill_list(([], i18n.t("нет модуля psutil — список процессов недоступен")))
check("диалог процессов: отказ чтения виден текстом и блокирует кнопки",
      not dlg_proc._ok_btn.isEnabled() and not dlg_proc.lbl_err.isHidden()
      and dlg_proc.lbl_err.text() == i18n.t(
          "Список процессов не прочитан: {v0}. Сжать рабочие наборы сейчас нельзя.",
          v0=i18n.t("нет модуля psutil — список процессов недоступен"))
      and dlg_proc.selected_pids() == [],
      repr(dlg_proc.lbl_err.text()))
dlg_proc._fill_list(([{"pid": 4, "name": "System", "rss": 1048576}], ""))
check("диалог процессов: живой список включает кнопки обратно",
      dlg_proc._ok_btn.isEnabled() and dlg_proc.lbl_err.isHidden()
      and dlg_proc.list.count() == 1,
      f"строк {dlg_proc.list.count()}")
dlg_proc.close()

# --- итог ----------------------------------------------------------------------
print()
print("ИТОГ:", "все проверки пройдены" if not FAILS else f"провалов {len(FAILS)}: {FAILS}")
sys.exit(1 if FAILS else 0)
