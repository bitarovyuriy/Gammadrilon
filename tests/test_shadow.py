# -*- coding: utf-8 -*-
"""Shadow Boost: подъём теней без «серой пелены» и без изломов кривой."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as gd_main  # noqa: E402


def ramp(shadow: float) -> list[int]:
    controller = gd_main.GammaController()
    red, _green, _blue = controller.build_ramp(1.0, 1.0, 1.0, 6500, shadow)
    return red


def test_zero_shadow_keeps_the_straight_line():
    red = ramp(0.0)

    assert red[0] == 0
    assert red[255] == 65535
    assert abs(red[128] - 32896) <= 300, "середина остаётся серединой"
    assert red == sorted(red), "кривая обязана быть неубывающей"


def test_shadow_lifts_darks_and_keeps_whites():
    straight = ramp(0.0)
    boosted = ramp(0.25)

    assert boosted[0] > straight[0], "чёрное должно подняться"
    assert boosted[16] > straight[16] * 1.3, "тени поднимаются заметно"
    assert abs(boosted[255] - straight[255]) <= 2, "белое остаётся белым"
    assert boosted[255] == 65535


def test_shadow_fades_out_towards_highlights():
    straight = ramp(0.0)
    boosted = ramp(0.25)

    lift_dark = boosted[10] - straight[10]
    lift_mid = boosted[128] - straight[128]
    lift_high = boosted[220] - straight[220]

    assert lift_dark > lift_mid > lift_high >= 0, (lift_dark, lift_mid, lift_high)


def test_shadow_keeps_the_curve_monotonic():
    red = ramp(0.25)

    assert red == sorted(red), "немонотонная кривая даёт полосы и артефакты"
    assert len(set(red)) > 200, "кривая не должна схлопываться в ступеньки"


def test_shadow_is_clamped_to_safe_range():
    over = ramp(5.0)      # больше 0.25 не разрешаем
    maximum = ramp(0.25)

    assert over == maximum, "запредельная сила не должна ломать кривую"


def test_strength_maps_to_curve_units():
    """Шкала интерфейса — проценты, кривая ждёт 0..0.25."""
    controller = gd_main.GammaController()
    full = controller.build_ramp(1.0, 1.0, 1.0, 6500, 100 / 100.0 * 0.25)
    quarter = controller.build_ramp(1.0, 1.0, 1.0, 6500, 25 / 100.0 * 0.25)

    assert full[0][0] > quarter[0][0] > ramp(0.0)[0]


def test_shadow_survives_preset_import():
    """Импорт пресетов обязан помнить тень, иначе она обнулится молча."""
    clean, dropped = gd_main.MainWindow._sanitize_presets(
        [{"name": "Ночь", "shadow": 60}, {"name": "День", "shadow": 250}])

    assert dropped == 0
    assert clean[0]["shadow"] == 60
    assert clean[1]["shadow"] == 100, "запредел обрезается, а не роняет импорт"

    broken, _dropped = gd_main.MainWindow._sanitize_presets(
        [{"name": "Мусор", "shadow": "abc"}])
    assert broken[0]["shadow"] == 0

    assert gd_main.DEFAULT_CONFIG["settings"]["current"]["shadow"] == 0
