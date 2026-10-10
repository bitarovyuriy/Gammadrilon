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

APP_TAG = "Gammadrilon"
FORMAT_VERSION = 3


# -- пути --------------------------------------------------------------------
def _base_dir() -> Path:
    import gd_rules

    return gd_rules._base_dir()


def quiet_path() -> Path:
    return _base_dir() / "quiet_hour.json"


# -- «Тихий час» --------------------------------------------------------------
DEFAULT_QUIET = {"enabled": False, "start": "23:00", "end": "08:00", "applied": False}


def load_quiet() -> dict:
    try:
        data = json.loads(quiet_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return dict(DEFAULT_QUIET)
    out = dict(DEFAULT_QUIET)
    out.update({k: data[k] for k in DEFAULT_QUIET if k in data})
    return out


def save_quiet(data: dict) -> dict:
    out = dict(DEFAULT_QUIET)
    out.update({k: data[k] for k in DEFAULT_QUIET if k in data})
    try:
        p = quiet_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return out


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
def export_all(path: str | Path) -> tuple[bool, str]:
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
    try:
        p = Path(path)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, f"Экспортировано в {p.name}: правила — {len(payload['rules'])}, " \
                     f"настройки — таймер/тихий час/DNS."
    except OSError as exc:
        return False, f"не удалось записать файл: {exc}"


def _read_autoclean() -> dict:
    try:
        return json.loads((_base_dir() / "autoclean.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_autoclean(data: dict) -> bool:
    try:
        p = _base_dir() / "autoclean.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def import_all(path: str | Path) -> tuple[bool, str]:
    import gd_rules

    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return False, f"файл не читается как JSON: {exc}"
    if not isinstance(data, dict) or data.get("app") != APP_TAG:
        return False, "это не файл настроек Gammadrilon"

    done = []
    rules = data.get("rules")
    if isinstance(rules, list) and all(isinstance(r, dict) for r in rules):
        gd_rules.save_rules(rules)
        done.append(f"правила ({len(rules)})")

    quiet = data.get("quiet_hour")
    if isinstance(quiet, dict):
        save_quiet(quiet)
        done.append("тихий час")

    autoclean = data.get("autoclean")
    if isinstance(autoclean, dict) and autoclean and _write_autoclean(autoclean):
        done.append("автоочистка ОЗУ")

    if not done:
        return False, "в файле нет знакомых разделов"
    return True, "импортировано: " + ", ".join(done) + ". Профили CPU/GPU встроенные — их не перезаписываю."
