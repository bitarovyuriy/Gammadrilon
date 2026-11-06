"""Регрессии интерфейса по ревью 3.6.2, без внешних действий."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gd_bg  # noqa: E402
import gd_cs2  # noqa: E402
import gd_cs2_ui as cs2ui  # noqa: E402
import gd_dns_ui as dnsui  # noqa: E402
import gd_i18n as i18n  # noqa: E402
import main  # noqa: E402

_APP = None


def app():
    global _APP
    from PyQt5.QtWidgets import QApplication

    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_single_cs2_choice_can_be_reverted_through_ui(monkeypatch, tmp_path):
    from test_cs2 import FakeRegistry, FakePower

    app()
    registry, power = FakeRegistry(), FakePower()
    backup = tmp_path / "backup.json"
    monkeypatch.setattr(gd_cs2, "backup_path", lambda: backup)
    snapshot = gd_cs2.snapshot
    monkeypatch.setattr(gd_cs2, "snapshot", lambda: snapshot(registry=registry, power=power))
    monkeypatch.setattr(gd_cs2, "find_cs2", lambda: "")
    monkeypatch.setattr(gd_cs2, "cs2_pids", lambda: [])
    monkeypatch.setattr(gd_cs2, "diagnostics", lambda: [])
    monkeypatch.setattr(gd_cs2, "video_notes", lambda: [])
    monkeypatch.setattr(gd_cs2, "video_file", lambda: None)
    monkeypatch.setattr(gd_bg, "submit", lambda fn, done, **kw: done(fn()))
    page = cs2ui.Cs2Widget(config={"settings": {"cs2_items": ["game_mode"]}})

    def call(request, **kw):
        report = (gd_cs2.revert(registry=registry, power=power) if request["cmd"] == "cs2_revert"
                  else gd_cs2.apply(request["items"], registry=registry, power=power))
        return {"ok": report["ok"], "data": report}

    page._client = NS(call=call)
    assert page.checkboxes["game_mode"].isChecked()
    page.btn_toggle.click()
    assert page._on, "снимок приложения должен давать доступ к откату даже одного пункта"
    assert "Выключить" in page.btn_toggle.text()
    page.btn_toggle.click()
    assert not backup.exists()
    assert not page._on
    page.deleteLater()


def test_reset_waits_for_all_results_and_reports_partial_failure(monkeypatch):
    import gd_toast
    import gd_journal

    jobs, reports = [], []
    toast = Mock()
    monkeypatch.setattr(gd_bg, "submit", lambda fn, done, **kw: jobs.append((fn, done)))
    monkeypatch.setattr(gd_toast, "show_toast", toast)
    monkeypatch.setattr(gd_journal, "record", Mock())
    window = NS(_ensure_page=lambda key: None, set_status=Mock(),
                gamma_animation_timer=NS(stop=Mock()), _cancel_cat_vision=Mock(),
                set_ui_values=Mock(), _gamma_pending=None)
    window._reset_system_done = lambda result: main.MainWindow._reset_system_done(window, result)
    window._reset_gamma_done = lambda ok, error="": main.MainWindow._reset_gamma_done(window, ok, error)
    window._finish_reset = lambda: main.MainWindow._finish_reset(window)
    main.MainWindow.reset_everything(window, on_done=reports.append)
    assert not reports
    assert not toast.called
    assert jobs
    jobs[0][1]({"done": ["CPU"], "errors": ["GPU: отказ"]})
    assert not reports, "гамма ещё не подтвердила запись"
    window._reset_gamma_done(True)
    assert len(reports) == 1
    assert not reports[0]["ok"]
    assert "гамма" in reports[0]["done"]
    assert toast.call_args.args[1] == "warn"


def test_doh_failure_is_not_shown_as_success(monkeypatch):
    import gd_toast

    resolver = next(r for r in dnsui.gd_dns.RESOLVERS if r.get("doh"))
    client = NS(call=Mock(side_effect=[{"ok": True}, {"ok": False, "error": "DoH denied"}]))
    window = NS(_apply_worker=None, _selected_key=lambda: resolver["key"],
                _ensure=lambda: client, chk_doh=NS(isChecked=lambda: True),
                btn_apply=NS(setEnabled=Mock()), _set_status=Mock(), _refresh_now=Mock())
    window._apply_done = lambda result, r, servers: dnsui.DnsWidget._apply_done(window, result, r, servers)
    monkeypatch.setattr(dnsui.gd_dns, "remember_before_change", lambda: (True, {"iface": "test"}))
    monkeypatch.setattr(gd_bg, "submit", lambda fn, done, **kw: done(fn()))
    toast = Mock()
    monkeypatch.setattr(gd_toast, "show_toast", toast)
    dnsui.DnsWidget._apply(window)
    assert window._set_status.call_args.kwargs.get("ok") is False
    assert "DoH" in window._set_status.call_args.args[0]
    assert not toast.called or toast.call_args.args[1] != "ok"


def test_onboarding_describes_actual_gpu_policy():
    import gd_onboarding

    source = Path(gd_onboarding.__file__).read_text(encoding="utf-8")
    assert "откат автоматический, если окно закрылось нештатно" not in source
    for lang in ("ru", "en"):
        previous = i18n.current()
        try:
            i18n.set_language(lang)
            text = i18n.t(gd_onboarding.INTRO_SOURCE)
            assert ("по умолчанию" in text.lower() and "сохраняется" in text) if lang == "ru" else (
                "by default" in text and "kept" in text)
            assert "auto-rollback if the window crashes" not in text
        finally:
            i18n.set_language(previous)


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_transfer_buttons_fit_narrow_page(monkeypatch, lang):
    import gd_extra_ui as ui
    import gd_theme as th

    app()
    monkeypatch.setattr(ui.gd_extra, "timer_ms", lambda: 15.6)
    monkeypatch.setattr(ui.gd_profiles, "load_quiet", lambda: {})
    previous = i18n.current()
    try:
        i18n.set_language(lang)
        page = ui.ExtraPanel()
        page.setObjectName("PanelRoot")
        page.setStyleSheet(th.STYLESHEET)
        page.ensurePolished()
        assert page.minimumSizeHint().width() <= 650
        page.deleteLater()
    finally:
        i18n.set_language(previous)


def test_long_path_is_elided_but_kept_in_tooltip():
    import gd_zapret_ui as ui
    from PyQt5.QtWidgets import QSizePolicy

    app()
    label = ui.ElidedPathLabel()
    text = "C:/Users/Александр/Downloads/zapret-discord-youtube"
    label.setText(text)
    label.resize(180, 30)
    label.show()
    app().processEvents()
    assert label.toolTip() == text
    assert label.text() != text
    assert "…" in label.text()
    assert label.sizePolicy().horizontalPolicy() == QSizePolicy.Ignored
    label.deleteLater()


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("active", [False, True])
def test_navigation_icons_have_graphical_contrast(monkeypatch, theme, active):
    import gd_theme as th
    from test_contrast import contrast_ratio
    from PyQt5.QtGui import QIcon

    app()
    previous = th.active_name()
    colors = []
    monkeypatch.setattr(main.gd_icons, "icon", lambda name, size, color: colors.append(color) or QIcon())
    try:
        th.apply(theme)
        item = main.NavItem("tab.gpu", "GPU", "gpu")
        item.set_active(active)
        background = th.PRESSED if active else th.PAPER
        assert contrast_ratio(colors[-1], background) >= 3
        item.deleteLater()
    finally:
        th.apply(previous)


def test_checkbox_border_has_graphical_contrast():
    import gd_theme as th
    from test_contrast import contrast_ratio

    previous = th.active_name()
    try:
        th.apply("dark")
        style = th.STYLESHEET.split("QCheckBox::indicator,", 1)[1].split("}", 1)[0]
        import re

        color = re.search(r"border: 1px solid (#[0-9A-Fa-f]+)", style).group(1)
        assert contrast_ratio(color, th.SURFACE) >= 3
    finally:
        th.apply(previous)


def test_gamma_slider_has_accessible_name():
    app()
    window = NS(bind_text=lambda widget, key: widget.setText(i18n.t(key)),
                set_value_text=main.MainWindow.set_value_text)
    container, slider, value = main.MainWindow.make_slider_row(window, "Гамма", 50, 250, 100, "%")
    assert slider.accessibleName() == "Гамма"
    container.deleteLater()


def test_info_and_tile_titles_are_keyboard_buttons(monkeypatch):
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QAbstractButton, QWhatsThis
    import gd_power_ui
    import gd_dashboard_ui

    app()
    frame, _ = gd_power_ui.make_card("CPU", "Пояснение")
    info = frame.findChild(QAbstractButton, "CardInfo")
    assert info is not None
    assert info.accessibleName() and info.focusPolicy() != Qt.NoFocus
    shown = Mock()
    monkeypatch.setattr(QWhatsThis, "showText", shown)
    QTest.keyClick(info, Qt.Key_Space)
    assert shown.called
    title = gd_dashboard_ui.ClickableLabel("GPU")
    assert isinstance(title, QAbstractButton)
    clicked = Mock()
    title.clicked.connect(clicked)
    QTest.keyClick(title, Qt.Key_Space)
    clicked.assert_called_once()
    frame.deleteLater()
    title.deleteLater()


def test_a11y_checker_includes_sliders():
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QWidget, QSlider
    from tools.a11y_check import check_page

    app()
    page = QWidget()
    slider = QSlider(Qt.Horizontal, page)
    page.show()
    slider.show()
    app().processEvents()
    assert any("QSlider" in problem for problem in check_page(page, "test"))
    page.deleteLater()


def test_cpu_gpu_sliders_have_accessible_names(monkeypatch):
    from PyQt5.QtWidgets import QSlider
    import gd_gpu_ui
    import gd_power_ui

    app()
    monkeypatch.setattr(gd_bg, "submit", lambda *a, **kw: None)
    for page in (gd_gpu_ui.GpuWidget(standalone=False, config={}), gd_power_ui.PowerWindow(standalone=False)):
        assert all(slider.accessibleName() for slider in page.findChildren(QSlider))
        page.deleteLater()


def test_gpu_failed_samples_do_not_refresh_success_time(monkeypatch):
    import gd_metrics as metrics

    monkeypatch.setattr(metrics, "start", lambda: None)
    monkeypatch.setattr(metrics, "_state", {"cpu": None, "ram": None, "gpu": None, "dns": None})
    monkeypatch.setattr(metrics, "_ts", {"cpu": 0, "gpu": 0, "dns": 0})
    monkeypatch.setattr(metrics, "_gpu_last_success", 0, raising=False)
    monkeypatch.setattr(metrics, "_gpu_miss", 0)
    monkeypatch.setattr(metrics.time, "monotonic", lambda: 110)
    metrics._record_gpu({"name": "GPU"}, 100)
    for now in (102, 104, 108):
        metrics._record_gpu(None, now)
    info = metrics.gpu_status()
    assert info["age"] == 10
    assert info["stale"] and not info["available"]
    assert metrics.gpu() is None


def test_gpu_loss_disables_controls_after_success(monkeypatch):
    import gd_gpu_ui as ui
    import gd_metrics as metrics

    monkeypatch.setattr(metrics, "gpu_status", lambda: {"state": {"name": "previous GPU"}, "age": 10,
                                                        "stale": True, "available": False}, raising=False)
    monkeypatch.setattr(metrics, "gpu", lambda: None)
    monkeypatch.setattr(metrics, "gpu_unavailable", lambda: True)
    window = NS(_state={"name": "previous GPU"}, _pending_gpu_probe=False,
                lbl_gpu=NS(setText=Mock()), _gpu_warn=NS(setText=Mock(), setVisible=Mock()),
                _set_controls_enabled=Mock(), _gpu_absent_note=lambda: "absent")
    for name in ("lbl_temp", "lbl_clk", "lbl_pl", "lbl_util", "lbl_mem"):
        setattr(window, name, NS(setText=Mock()))
    ui.GpuWidget._tick(window)
    window._set_controls_enabled.assert_called_with(False)
    assert "GPU" in window.lbl_gpu.setText.call_args.args[0]
    assert window._gpu_warn.setVisible.call_args.args == (True,)


@pytest.mark.parametrize("lang,expected", [("ru", "Лимит мощности и мониторинг GPU"),
                                          ("en", "GPU power limit and monitoring")])
def test_gpu_subtitle_matches_available_controls(lang, expected):
    previous = i18n.current()
    try:
        i18n.set_language(lang)
        assert i18n.t("tab.gpu.sub") == expected
    finally:
        i18n.set_language(previous)


def test_user_instructions_use_consistent_address():
    import re

    for name in ("gd_cs2_ui.py", "gd_utils_ui.py", "gd_i18n.py"):
        source = (Path(main.__file__).parent / name).read_text(encoding="utf-8")
        assert not re.search(r"\b(?:Отметь|Проверь)\b", source), name


@pytest.mark.parametrize("ok,actual", [(True, 5000), (False, 5000), (True, 0)])
def test_timer_journal_preserves_milliseconds(monkeypatch, ok, actual):
    import gd_extra_ui as ui
    import gd_toast

    values = iter([0.5, None])
    monkeypatch.setattr(ui.gd_extra, "timer_ms", lambda: next(values))
    monkeypatch.setattr(ui.gd_extra, "set_timer_resolution", lambda enabled: (ok, actual))
    record = Mock()
    monkeypatch.setattr(ui.gd_journal, "record", record)
    monkeypatch.setattr(gd_toast, "show_toast", Mock())
    window = NS(lbl_timer=NS(setText=Mock()), lbl_net=object(), _set_status=Mock())
    ui.ExtraPanel._timer_toggle(window, True)
    assert record.call_args.args[2] == 0.5
