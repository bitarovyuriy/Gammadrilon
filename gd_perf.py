# -*- coding: utf-8 -*-
"""Gammadrilon v3 — переключатель производительности одной горячей клавишей.

Клавиша (settings.hotkeys.perf_toggle) переключает пару профилей
«ограничение ↔ производительность»: применяет выбранные профили CPU и GPU
одним нажатием и издаёт характерный звук (низкие тона вниз — ограничение
включено, высокие вверх — снято). Что применять, задаёт пользователь в диалоге
«Глобальные клавиши» (settings.perf_profiles): пустой профиль в блоке =
«этот блок не трогаем».

Логика здесь без Qt: окно вызывает `toggle()` и `play_sound()`, а тест
проверяет их на заглушках страниц.
"""
from __future__ import annotations

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


def play_sound(limited: bool) -> None:
    """Характерный щелчок переключателя — в фоне, не морозя GUI.

    Два тона: вниз (392→262 Гц) — ограничение включено, вверх (523→784 Гц) —
    снято. `Beep` блокирует поток на длительность звука, поэтому играем в
    отдельном потоке. Нет звукового драйвера — исключение глушится: звук —
    приятный бонус переключателя, а не обязанность.
    """
    import threading

    def beep():
        try:
            import winsound

            tones = ((392, 110), (262, 170)) if limited else ((523, 110), (784, 150))
            for freq, dur in tones:
                winsound.Beep(freq, dur)
        except Exception:  # noqa: BLE001
            pass

    try:
        threading.Thread(target=beep, daemon=True).start()
    except Exception:  # noqa: BLE001
        pass
