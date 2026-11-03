# -*- coding: utf-8 -*-
"""Экран первого запуска: тема, автозапуск, проверка окружения."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gd_onboarding  # noqa: E402


_APP = None


def app():
    global _APP
    from PyQt5.QtWidgets import QApplication

    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_dialog_offers_theme_and_startup():
    app()
    dialog = gd_onboarding.FirstRunDialog(None, startup_on=True)

    themes = [dialog.theme_combo.itemData(index)
              for index in range(dialog.theme_combo.count())]
    assert themes == ["dark", "light"]
    assert dialog.chk_startup.isChecked() is True


def test_diagnostics_hidden_until_asked():
    app()
    dialog = gd_onboarding.FirstRunDialog(None, startup_on=False)

    assert dialog.diag.isHidden() is True, "текст диагностики не должен висеть пустым"
    assert dialog.btn_diag.isEnabled() is True


def test_diagnostic_result_is_shown(monkeypatch):
    app()
    dialog = gd_onboarding.FirstRunDialog(None)

    dialog._diag_done("OK  Windows: Windows 11")

    assert dialog.diag.isHidden() is False
    assert "Windows 11" in dialog.diag.toPlainText()
    assert dialog.btn_diag.isEnabled() is True


def test_run_skips_when_already_done():
    config = {"settings": {"first_run_done": True}}

    assert gd_onboarding.should_show(config) is False
    assert gd_onboarding.run(config) is False
