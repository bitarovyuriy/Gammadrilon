# -*- coding: utf-8 -*-
"""Прочие утилиты (ТЗ 6.3, п.4–5): экспорт/импорт настроек и планировщик «Тихий час».

Экспорт — обычный JSON, чтобы профилями можно было делиться. Импорт валидирует поля
и не падает на мусоре. «Тихий час» в заданные часы включает ЭКО CPU и уводит яркость
экрана в минимум, а по окончании окна возвращает прежнее состояние.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import gd_i18n as i18n
import gd_state

APP_TAG = "Gammadrilon"
#: 4 — в файле появился раздел "settings" (настройки приложения и пресеты гаммы);
#: импорт читает и старые файлы: разделы просто смотрятся по наличию.
FORMAT_VERSION = 4


# -- пути --------------------------------------------------------------------
def _base_dir() -> Path:
    import gd_rules

    return gd_rules._base_dir()


def quiet_path() -> Path:
    return _base_dir() / "quiet_hour.json"


# -- «Тихий час» --------------------------------------------------------------
DEFAULT_QUIET = {"enabled": False, "start": "23:00", "end": "08:00", "applied": False}


def load_quiet() -> dict:
    data = gd_state.read_json(quiet_path(), {})
    if not isinstance(data, dict):
        return dict(DEFAULT_QUIET)
    out = dict(DEFAULT_QUIET)
    out.update({k: data[k] for k in DEFAULT_QUIET if k in data})
    return out


def save_quiet(data: dict) -> bool:
    """Записать «тихий час» атомарно. False — не получилось (вызывающий скажет)."""
    out = dict(DEFAULT_QUIET)
    out.update({k: data[k] for k in DEFAULT_QUIET if k in data})
    return gd_state.write_json_atomic(quiet_path(), out)


def _parse_hhmm(text: str):
    try:
        h, m = text.strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except Exception:  # noqa: BLE001
        pass
    return None


def in_window(data: dict, now: _dt.datetime | None = None) -> bool:
    """Сейчас внутри окна «тихого часа»? Корректно обрабатывает переход через полночь."""
    if not data.get("enabled"):
        return False
    s, e = _parse_hhmm(data.get("start", "")), _parse_hhmm(data.get("end", ""))
    if not s or not e:
        return False
    now = now or _dt.datetime.now()
    cur = now.hour * 60 + now.minute
    a = s[0] * 60 + s[1]
    b = e[0] * 60 + e[1]
    if a == b:
        return False
    if a < b:
        return a <= cur < b
    return cur >= a or cur < b


# -- экспорт / импорт ---------------------------------------------------------
def export_all(path: str | Path, app_settings: dict | None = None) -> tuple[bool, str]:
    """Собрать всё настраиваемое в один файл.

    ``app_settings`` — конфиг приложения (presets.json) как есть: настройки,
    пресеты гаммы, горячие клавиши. Без него экспорт прежний — правила, тихий
    час, автоочистка, состояние DNS.
    """
    import gd_dns
    import gd_gpu
    import gd_power
    import gd_rules

    payload = {
        "app": APP_TAG,
        "format": FORMAT_VERSION,
        "exported": _dt.datetime.now().isoformat(timespec="seconds"),
        "cpu_profiles": {k: v for k, v in getattr(gd_power, "PROFILES", {}).items()},
        "gpu_profiles": {k: (v if not isinstance(v, tuple) else list(v))
                         for k, v in getattr(gd_gpu, "GPU_PROFILES", {}).items()},
        "rules": gd_rules.load_rules(),
        "quiet_hour": load_quiet(),
        "dns": gd_dns.current_state(),
        "autoclean": _read_autoclean(),
    }
    if isinstance(app_settings, dict) and app_settings:
        payload["settings"] = app_settings
    try:
        p = Path(path)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        msg = i18n.t("Экспортировано в {v0}: правила — {v1}, настройки — таймер/тихий час/DNS.",
                     v0=p.name, v1=len(payload['rules']))
        if "settings" in payload:
            msg += " " + i18n.t("Настройки приложения и пресеты гаммы — внутри.")
        return True, msg
    except OSError as exc:
        return False, i18n.t("не удалось записать файл: {v0}", v0=exc)


def _read_autoclean() -> dict:
    data = gd_state.read_json(_base_dir() / "autoclean.json", {})
    return data if isinstance(data, dict) else {}


def _write_autoclean(data: dict) -> bool:
    return gd_state.write_json_atomic(_base_dir() / "autoclean.json", data)


def import_all(path: str | Path, apply_settings=None) -> tuple[bool, str]:
    """Применить файл переноса к этой машине.

    ``apply_settings`` — callback ``(settings: dict) -> (bool, str)``: применить
    настройки приложения. Путь конфига и его проверка живут в окне, поэтому
    применяет их именно оно, а не этот модуль. Без callback'а профили
    применятся, а про настройки будет честно сказано, что они остались в файле.
    """
    import gd_rules

    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return False, i18n.t("файл не читается как JSON: {v0}", v0=exc)
    if not isinstance(data, dict) or data.get("app") != APP_TAG:
        return False, i18n.t("это не файл настроек Gammadrilon")

    done = []
    failed = []
    notes = []
    rules = data.get("rules")
    if isinstance(rules, list) and all(isinstance(r, dict) for r in rules):
        if gd_rules.save_rules(rules):
            done.append(i18n.t("правила ({v0})", v0=len(rules)))
        else:
            failed.append(i18n.t("правила"))

    quiet = data.get("quiet_hour")
    if isinstance(quiet, dict):
        if save_quiet(quiet):
            done.append(i18n.t("тихий час"))
        else:
            failed.append(i18n.t("тихий час"))

    autoclean = data.get("autoclean")
    if isinstance(autoclean, dict) and autoclean:
        if _write_autoclean(autoclean):
            done.append(i18n.t("автоочистка ОЗУ"))
        else:
            failed.append(i18n.t("автоочистка ОЗУ"))

    settings = data.get("settings")
    if isinstance(settings, dict) and settings:
        if apply_settings is None:
            notes.append(i18n.t(
                "Настройки приложения в файле есть, но перенос идёт без окна: применяю только профили."))
        else:
            try:
                settings_ok, note = apply_settings(settings)
            except Exception as exc:  # noqa: BLE001 — чужой конфиг не должен ронять импорт
                settings_ok = False
                note = i18n.t("настройки приложения применить не удалось: {v0}", v0=exc)
            if settings_ok:
                done.append(i18n.t("настройки приложения"))
                if note:
                    notes.append(note)
            else:
                notes.append(note or i18n.t("настройки приложения применить не удалось"))

    if not done:
        if failed:
            return False, i18n.t("Не сохранено: {v0} — файл недоступен для записи.",
                                 v0=", ".join(failed))
        if notes:
            return False, " ".join(notes)
        return False, i18n.t("в файле нет знакомых разделов")
    msg = i18n.t("импортировано: {v0}. Профили CPU/GPU встроенные — их не перезаписываю.",
                 v0=", ".join(done))
    msg += "".join(" " + note for note in notes)
    if failed:
        # отказ записи не прячем: «импортировано» без оговорки было бы ложью
        msg += " " + i18n.t("Не сохранено: {v0} — файл недоступен для записи.",
                            v0=", ".join(failed))
    return True, msg
