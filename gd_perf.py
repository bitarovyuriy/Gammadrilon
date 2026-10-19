# -*- coding: utf-8 -*-
"""Gammadrilon v3 — переключатель производительности одной горячей клавишей.

Клавиша (settings.hotkeys.perf_toggle) переключает пару профилей
«ограничение ↔ производительность»: применяет выбранные профили CPU и GPU
одним нажатием и издаёт свой звук — два мотива, разные на слух: вниз и глухо,
когда ограничения включаются, и вверх и ярче, когда снимаются. Что применять,
задаёт пользователь в диалоге «Глобальные клавиши» (settings.perf_profiles):
пустой профиль в блоке = «этот блок не трогаем».

Логика здесь без Qt: окно вызывает `toggle()`, `play_sound()` и
`build_wav()`, а тест проверяет их на заглушках страниц и на самом сигнале
(частоты мотива читаются из WAV преобразованием Фурье — звуковую карту для
этого иметь не нужно).
"""
from __future__ import annotations

import array
import io
import math
import os
import sys
import tempfile
import threading
import wave

import gd_i18n as i18n

#: профили CPU, которые принимает вкладка «Питание CPU» (gd_power.PROFILES)
CPU_IDS = ("eco", "normal", "boost")


def target_state(limited_now: bool) -> str:
    """Следующее состояние: ограничение включено → включаем «производительность»."""
    return "boost" if limited_now else "limited"


def toggle(config: dict, power_page, gpu_page) -> dict:
    """Переключить ограничения CPU+GPU по настройкам. Возвращает отчёт.

    Ничего не бросает наружу: CPU и GPU применяются независимо, отказ одной
    половины не мешает другой. `config["settings"]["perf_limited"]` обновляется
    только когда что-то реально ушло в работу; файл сохраняет вызывающий —
    окно знает свой save_fn.
    """
    settings = config.setdefault("settings", {})
    profiles = settings.get("perf_profiles")
    if not isinstance(profiles, dict):
        profiles = {}
    limited_now = bool(settings.get("perf_limited", False))
    state = target_state(limited_now)
    spec = profiles.get(state)
    if not isinstance(spec, dict):
        spec = {}
    cpu = str(spec.get("cpu") or "")
    gpu = str(spec.get("gpu") or "")

    report = {"ok": False, "state": state, "limited": not limited_now,
              "cpu": "", "gpu": "", "errors": []}
    if not cpu and not gpu:
        return report

    source = i18n.t("горячая клавиша")
    if cpu:
        if cpu not in CPU_IDS:
            report["errors"].append(i18n.t("профиль CPU не распознан: {v0}", v0=cpu))
        elif power_page is not None:
            try:
                power_page.apply_profile(cpu, source=source)
                report["cpu"] = cpu
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(i18n.t("Профиль не применён: {v0}", v0=exc))
    if gpu:
        import gd_gpu

        if gpu not in gd_gpu.GPU_PROFILES:
            report["errors"].append(i18n.t("профиль GPU не распознан: {v0}", v0=gpu))
        elif gpu_page is not None:
            try:
                gpu_page.apply_profile(gpu, source=source)
                report["gpu"] = gpu
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(i18n.t("Профиль GPU не применён: {v0}", v0=exc))

    if report["cpu"] or report["gpu"]:
        settings["perf_limited"] = not limited_now
        report["ok"] = True
    return report


# --- звук переключателя -------------------------------------------------------
# Мотив синтезируем сами: в сборке нет ни одного звукового файла, а системный
# `winsound.Beep` пищит меандром и на 262 Гц его за корпусом ноутбука не слышно.
# Частоты те же, что были у писка (392→262 вниз, 523→784 вверх) — звук узнаваем,
# но теперь это тембр: мягкая атака, спад и яркие гармоники у «производительности».
SAMPLE_RATE = 44100        # Гц: стандарт для WAV, 16 бит моно
SOUND_PEAK = 0.45          # амплитуда в долях шкалы: слышно, но не бьёт по ушам

#: Мотив: (частота в начале, частота в конце, длительность в секундах, яркость).
#: Яркость — вес 2-й и 3-й гармоник: ближе к 0 — глухо и мягко, ближе к 1 — звонко.
#: `click` — щелчок выключателя в начале мотива, `body` — низкая подложка «придавило».
SOUND_SPECS: dict[str, dict] = {
    "limited": {
        "notes": ((392.0, 366.0, 0.110, 0.22), (261.6, 246.9, 0.190, 0.16)),
        "click": 0.55,
        "body": 0.20,
    },
    "boost": {
        "notes": ((523.25, 560.0, 0.100, 0.45), (784.0, 830.0, 0.180, 0.55)),
        "click": 0.50,
        "body": 0.0,
    },
}

#: Состояние переключателя → мотив (True — ограничения включены)
SOUND_STATES = {True: "limited", False: "boost"}

#: Последний откат для систем, не играющих WAV: те же ноты, что были у писка
BEEP_TONES = {True: ((392, 110), (262, 170)), False: ((523, 110), (784, 150))}

_ATTACK_SECONDS = 0.004     # 4 мс на атаку: без щелчка в начале ноты
_WAV_CACHE: dict[str, bytes] = {}
_LAST_WAV = b""             # последний отданный мотив: winmm читает буфер и после возврата


def _tone(freq_from: float, freq_to: float, dur: float, bright: float) -> list[float]:
    """Нота мотива: мягкая атака, спад и скольжение частоты от `freq_from` к `freq_to`.

    Скольжение и даёт характер «поехало вниз» против «разгоняется вверх», а не
    сухой писк фиксированной частоты.
    """
    count = max(1, int(SAMPLE_RATE * dur))
    rise = max(1.0, SAMPLE_RATE * _ATTACK_SECONDS)
    out: list[float] = []
    phase = 0.0
    for i in range(count):
        pos = i / count
        phase += 2.0 * math.pi * (freq_from + (freq_to - freq_from) * pos) / SAMPLE_RATE
        env = math.exp(-3.4 * pos) * min(1.0, (i + 1) / rise)
        out.append((math.sin(phase)
                    + 0.55 * bright * math.sin(2.0 * phase)
                    + 0.28 * bright * math.sin(3.0 * phase)) * env)
    return out


def _click(level: float, dur: float = 0.006) -> list[float]:
    """Щелчок выключателя: короткий шум по спаду.

    Шум двусторонний (от −1 до 1) и потому широкополосный: односторонний давал бы
    не щелчок, а низкий «тык» и съедал запас громкости. Генератор свой, с
    фиксированным зерном: тест должен повторить сигнал, а не гадать на случайном.
    """
    if level <= 0:
        return []
    count = max(1, int(SAMPLE_RATE * dur))
    out: list[float] = []
    seed = 20261014
    for i in range(count):
        seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
        out.append(((seed / 0x7FFFFFFF) * 2.0 - 1.0) * level * (1.0 - i / count) ** 2)
    return out


def _body(level: float, dur: float = 0.160, freq: float = 130.8) -> list[float]:
    """Низкая подложка под мотивом: вес «придавило» у ограничения."""
    if level <= 0:
        return []
    count = max(1, int(SAMPLE_RATE * dur))
    rise = max(1.0, SAMPLE_RATE * _ATTACK_SECONDS)
    out: list[float] = []
    for i in range(count):
        pos = i / count
        out.append(math.sin(2.0 * math.pi * freq * i / SAMPLE_RATE)
                   * level * math.exp(-6.0 * pos) * min(1.0, (i + 1) / rise))
    return out


def _synthesize(state: str) -> list[float]:
    """Сэмплы мотива: ноты друг за другом, плюс щелчок и подложка в начале.

    Уровень выравнивается по максимальному пику: мотивы разной длины и яркости
    не должны отличаться громкостью так, что один из них не слышно.
    """
    spec = SOUND_SPECS[state]
    notes = [_tone(*note) for note in spec["notes"]]
    click, body = _click(spec["click"]), _body(spec["body"])
    total = max(sum(len(note) for note in notes), len(click), len(body))
    buf = [0.0] * total
    offset = 0
    for note in notes:
        for i, value in enumerate(note):
            buf[offset + i] += value
        offset += len(note)
    for part in (click, body):
        for i, value in enumerate(part):
            buf[i] += value
    peak = max((abs(value) for value in buf), default=0.0) or 1.0
    scale = SOUND_PEAK / peak
    return [value * scale for value in buf]


def build_wav(state: str) -> bytes:
    """WAV мотива целиком в памяти: 16 бит, моно, 44,1 кГц.

    Тест читает этот поток и считает частоты — звуковая карта ему не нужна.
    `state` — «limited» или «boost» (`SOUND_STATES`).
    """
    cached = _WAV_CACHE.get(state)
    if cached is not None:
        return cached
    pcm = array.array("h", [int(value * 32767) for value in _synthesize(state)])
    if sys.byteorder == "big":
        pcm.byteswap()
    stream = io.BytesIO()
    with wave.open(stream, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm.tobytes())
    data = stream.getvalue()
    _WAV_CACHE[state] = data
    return data


def _flag_set(ws, *names) -> "int | None":
    """Собрать набор флагов winsound; None — этой сборке такие флаги неизвестны."""
    value = 0
    for name in names:
        part = getattr(ws, name, None)
        if part is None:
            return None
        value |= part
    return value


def _play(ws, sound, flags) -> bool:
    """Позвать `winsound.PlaySound`.

    В CPython успех — это `None`, отказ — исключение; но на случай сборок, где
    отказ возвращается как `False`, считаем отказом именно `False`.
    """
    play = getattr(ws, "PlaySound", None)
    if play is None or flags is None:
        return False
    try:
        return play(sound, flags) is not False
    except Exception:  # noqa: BLE001
        return False


def _play_memory(ws, data: bytes) -> bool:
    """Проиграть WAV прямо из памяти — синхронно, без временных файлов.

    `SND_MEMORY` нельзя сочетать с `SND_ASYNC`: winmm отвечает «Cannot play
    asynchronously from memory» (замерено на живой Windows). Поэтому здесь только
    `SND_MEMORY|SND_SYNC|SND_NODEFAULT` — блокирующее воспроизведение на 0,3 с.
    Оно идёт в фоновом потоке, а буфер держим в `_LAST_WAV` до конца проигрывания:
    winmm читает его и после возврата из вызова.
    """
    global _LAST_WAV
    flags = _flag_set(ws, "SND_MEMORY", "SND_SYNC", "SND_NODEFAULT")
    if flags is None:
        return False
    _LAST_WAV = data
    return _play(ws, data, flags)


def _play_file(ws, state: str, data: bytes) -> bool:
    """Запасной путь: WAV во временном файле и `SND_FILENAME|SND_ASYNC`.

    Асинхронный, поэтому поток не держит; идёт вторым — на диске остаётся файл
    на 0,3 с звука, а первый путь обходится без диска совсем.
    """
    flags = _flag_set(ws, "SND_FILENAME", "SND_ASYNC", "SND_NODEFAULT")
    if flags is None:
        return False
    path = os.path.join(tempfile.gettempdir(), f"gammadrilon-perf-{state}.wav")
    try:
        with open(path, "wb") as fh:
            fh.write(data)
    except Exception:  # noqa: BLE001
        return False
    return _play(ws, path, flags)


def _beep(ws, limited: bool) -> None:
    """Последний откат: системный писк теми же нотами (меандр, но хоть что-то слышно)."""
    play = getattr(ws, "Beep", None)
    if play is None:
        return
    for freq, dur in BEEP_TONES[bool(limited)]:
        try:
            play(freq, dur)
        except Exception:  # noqa: BLE001
            return


def play_sound(limited: bool) -> None:
    """Характерный отклик переключателя — в фоне, не морозя GUI.

    `limited=True` — мотив вниз (ограничение включено), `False` — вверх (снято).
    Цепочка проигрывания: WAV из памяти → временный файл → системный писк.
    Нет звукового драйвера — исключение глушится: звук — приятный бонус
    переключателя, а не обязанность. Синтез идёт там же, в фоне: первый вызов
    строит мотив (~15 мс), дальше он берётся из кэша.
    """
    state = SOUND_STATES[bool(limited)]

    def worker():
        try:
            try:
                import winsound
            except Exception:  # noqa: BLE001
                return
            data = build_wav(state)
            if _play_memory(winsound, data) or _play_file(winsound, state, data):
                return
            _beep(winsound, limited)
        except Exception:  # noqa: BLE001
            pass

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:  # noqa: BLE001
        pass

