from unittest.mock import Mock

import pytest

from gd_lite_display import Brightness, scale_ramp


def test_scaling_preserves_channels_and_saturates():
    source = (0, 160, 65535) * 256
    assert scale_ramp(source, 50)[:3] == (0, 80, 32767)
    assert scale_ramp(source, 150)[:3] == (0, 240, 65535)
    assert scale_ramp(source, 100) == source
    with pytest.raises(ValueError):
        scale_ramp(source, 0)


def test_does_not_accumulate_dimming_and_restores_original():
    original = tuple(range(768))
    api = Mock()
    api.read.side_effect = [original, scale_ramp(original, 80)]
    api.write.side_effect = lambda target: target
    brightness = Brightness(api)
    brightness.apply(60)
    brightness.apply(80)
    brightness.restore()
    assert [call.args[0] for call in api.write.call_args_list] == [
        scale_ramp(original, 60), scale_ramp(original, 80), original,
    ]
    assert api.read.call_count == 2
    brightness.restore()
    assert api.write.call_count == 3


def test_failed_write_keeps_baseline_available_for_retry():
    api = Mock()
    api.read.return_value = tuple(range(768))
    api.write.side_effect = [OSError('not accepted'), None, None]
    brightness = Brightness(api)
    with pytest.raises(OSError):
        brightness.apply(50)
    brightness.apply(80)
    brightness.restore()
    api.read.assert_called_once()
