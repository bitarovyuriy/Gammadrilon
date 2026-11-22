"""Exercise the actual GUI event callbacks without writing Windows settings."""
from unittest.mock import Mock

import pytest

import gd_lite_actions
import gd_lite_window
from gd_lite_window import LiteWindow


def test_brightness_shortcut_is_a_repeatable_toggle():
    brightness = Mock()
    window = LiteWindow(hide=True, brightness=brightness)
    try:
        window._toggle_brightness()
        window._toggle_brightness()
        assert [call.args for call in brightness.apply.call_args_list] == [(65,), (100,)]
        assert len(window.tabs.tabs()) == 2
    finally:
        window._close()


def test_window_only_reads_after_an_explicit_request(monkeypatch):
    status = Mock(return_value='Готово')
    apply = Mock()
    backend = Mock(return_value=(status, apply))
    monkeypatch.setattr(gd_lite_window, 'backend', backend)
    monkeypatch.setattr(gd_lite_window, 'elevated_apply', apply)
    brightness = Mock()
    window = LiteWindow(hide=True, brightness=brightness)
    try:
        assert not window.tk.call('after', 'info')
        backend.assert_not_called()
        brightness.apply.assert_not_called()
        window.after(0, lambda: window._start('zapret', 'on'))
        window.bind('<<ActionDone>>', lambda _: window.quit(), add='+')
        timer = window.after(5000, window.quit)
        window.mainloop()
        window.after_cancel(timer)
        assert window.rows['zapret'].get() == 'Готово'
        assert not window._busy
        assert not window.tk.call('after', 'info')
        apply.assert_called_once_with('zapret', 'on')
        status.assert_called_once_with()
        window._bright_button(75)
        brightness.apply.assert_called_once_with(75)
    finally:
        window._close()
    brightness.restore.assert_called_once_with()


@pytest.mark.parametrize('section,value', [('dns', 'any.exe'), ('power', 'eco'), ('cpu', 'custom'), ('zapret', 'install')])
def test_wrong_worker_requests_are_rejected_before_backend_import(monkeypatch, section, value):
    backend = Mock()
    monkeypatch.setattr(gd_lite_actions, 'backend', backend)
    assert gd_lite_actions.worker(section, value) == 2
    backend.assert_not_called()
