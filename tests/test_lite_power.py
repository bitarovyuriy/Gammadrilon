from unittest.mock import Mock

import pytest

import gd_lite_power as lite


def test_cpu_status_reads_snapshot_without_mutation(monkeypatch):
    power = Mock()
    power.state_snapshot.return_value = {
        'active_name': 'Balanced', 'active_scheme': 'abc', 'profile': 'eco'
    }
    monkeypatch.setattr(lite, '_power', lambda: power)
    result = lite.cpu_status()
    assert 'Balanced' in result
    assert 'eco' in result.lower() or 'эконом' in result.lower()
    power.apply_profile.assert_not_called()
    power.restore_report.assert_not_called()


def make_power():
    power = Mock()
    power.PROFILES = {'eco': {'max_state': 50}, 'normal': {'max_state': 99},
                      'boost': {'max_state': 100}}
    power.read_state_report.return_value = (None, '')
    power.apply_profile.return_value = {'clone_scheme': 'clone', 'profile': 'eco'}
    power.state_snapshot.return_value = {'active_scheme': 'clone', 'profile': 'eco',
                                         'values': {'max_state': {'ac': 50, 'dc': 50}}}
    return power


@pytest.mark.parametrize('profile,target', [('eco', 50), ('normal', 99), ('boost', 100)])
def test_cpu_apply_uses_existing_transaction_and_verifies(monkeypatch, profile, target):
    power = make_power()
    power.state_snapshot.return_value.update(profile=profile,
        values={'max_state': {'ac': target, 'dc': target}})
    monkeypatch.setattr(lite, '_power', lambda: power)
    assert isinstance(lite.cpu_apply(profile), str)
    power.apply_profile.assert_called_once_with(profile)
    power.restore_report.assert_not_called()


def test_cpu_apply_refuses_corrupt_rollback_file(monkeypatch):
    power = make_power()
    power.read_state_report.return_value = (None, 'bad JSON')
    monkeypatch.setattr(lite, '_power', lambda: power)
    with pytest.raises(RuntimeError, match='bad JSON'):
        lite.cpu_apply('eco')
    power.apply_profile.assert_not_called()


def test_cpu_apply_detects_failed_verification(monkeypatch):
    power = make_power()
    power.state_snapshot.return_value['values']['max_state']['dc'] = 99
    monkeypatch.setattr(lite, '_power', lambda: power)
    with pytest.raises(RuntimeError):
        lite.cpu_apply('eco')


def test_cpu_restore_reports_failures(monkeypatch):
    power = make_power()
    power.restore_report.return_value = {'ok': False, 'note': 'ошибка отката'}
    monkeypatch.setattr(lite, '_power', lambda: power)
    with pytest.raises(RuntimeError, match='ошибка отката'):
        lite.cpu_apply('restore')


def test_cpu_restore_succeeds(monkeypatch):
    power = make_power()
    power.restore_report.return_value = {'ok': True, 'applied': True, 'note': ''}
    monkeypatch.setattr(lite, '_power', lambda: power)
    assert isinstance(lite.cpu_apply('restore'), str)
    power.restore_report.assert_called_once_with()
    power.apply_profile.assert_not_called()


def test_cpu_restore_checks_that_original_was_activated(monkeypatch):
    power = make_power()
    power.read_state_report.return_value = ({'clone_scheme': 'clone',
                                              'original_scheme': 'original'}, '')
    power.get_active_scheme.side_effect = ['clone', 'clone']
    power.restore_report.return_value = {'ok': True, 'applied': True, 'note': ''}
    monkeypatch.setattr(lite, '_power', lambda: power)
    with pytest.raises(RuntimeError, match='подтвердить'):
        lite.cpu_apply('restore')


def test_cpu_restore_keeps_user_selected_scheme(monkeypatch):
    power = make_power()
    power.read_state_report.return_value = ({'clone_scheme': 'clone',
                                              'original_scheme': 'original'}, '')
    power.get_active_scheme.return_value = 'user-choice'
    power.restore_report.return_value = {'ok': True, 'applied': True, 'note': ''}
    monkeypatch.setattr(lite, '_power', lambda: power)
    assert isinstance(lite.cpu_apply('restore'), str)
    power.get_active_scheme.assert_called_once()


@pytest.mark.parametrize('fn,profile', [('cpu_apply', 'bad'), ('gpu_apply', 'bad')])
def test_invalid_profile_never_accesses_system(monkeypatch, fn, profile):
    monkeypatch.setattr(lite, '_power', Mock(side_effect=AssertionError))
    monkeypatch.setattr(lite, '_nvml', Mock(side_effect=AssertionError))
    with pytest.raises(ValueError):
        getattr(lite, fn)(profile)


class FakeLib:
    def __init__(self):
        self.current = 200000
        self.minimum = 100000
        self.maximum = 300000
        self.default = 220000

    def nvmlDeviceGetPowerManagementLimit(self, device, out):
        out._obj.value = self.current
        return 0

    def nvmlDeviceGetPowerManagementDefaultLimit(self, device, out):
        out._obj.value = self.default
        return 0

    def nvmlDeviceGetPowerManagementLimitConstraints(self, device, low, high):
        low._obj.value = self.minimum
        high._obj.value = self.maximum
        return 0


class FakeNvml:
    def __init__(self):
        self._lib = FakeLib()
        self._device = object()
        self.closed = False
        self.set_calls = []
        self.reject = False

    def available(self):
        return True

    def problem(self):
        return ''

    def set_power_limit(self, watts):
        self.set_calls.append(watts)
        if self.reject:
            return False, 'отказ драйвера'
        self._lib.current = round(watts * 1000)
        return True, ''

    def shutdown(self):
        self.closed = True


def test_gpu_status_uses_only_limit_queries(monkeypatch):
    gpu = FakeNvml()
    monkeypatch.setattr(lite, '_nvml', lambda: gpu)
    result = lite.gpu_status()
    assert '200' in result and '100' in result and '300' in result
    assert gpu.closed and not gpu.set_calls


@pytest.mark.parametrize('profile,watts', [('quiet', 150), ('default', 220),
                                          ('game', 279), ('max', 300)])
def test_gpu_profiles_set_and_verify_limit_only(monkeypatch, profile, watts):
    gpu = FakeNvml()
    monkeypatch.setattr(lite, '_nvml', lambda: gpu)
    result = lite.gpu_apply(profile)
    assert str(watts) in result
    assert gpu.set_calls == [watts]
    assert gpu.closed


def test_gpu_quiet_clamped_to_minimum(monkeypatch):
    gpu = FakeNvml()
    gpu._lib.minimum = 180000
    monkeypatch.setattr(lite, '_nvml', lambda: gpu)
    lite.gpu_apply('quiet')
    assert gpu.set_calls == [180]


def test_gpu_denial_propagates_and_closes(monkeypatch):
    gpu = FakeNvml()
    gpu.reject = True
    monkeypatch.setattr(lite, '_nvml', lambda: gpu)
    with pytest.raises(RuntimeError, match='отказ драйвера'):
        lite.gpu_apply('max')
    assert gpu.closed


def test_gpu_detects_unapplied_limit(monkeypatch):
    gpu = FakeNvml()
    gpu.set_power_limit = lambda watts: (True, '')
    monkeypatch.setattr(lite, '_nvml', lambda: gpu)
    with pytest.raises(RuntimeError, match='подтверд'):
        lite.gpu_apply('max')
    assert gpu.closed


def test_gpu_missing_limits_fails_without_setting(monkeypatch):
    gpu = FakeNvml()
    gpu._lib.nvmlDeviceGetPowerManagementLimitConstraints = lambda *args: 3
    monkeypatch.setattr(lite, '_nvml', lambda: gpu)
    with pytest.raises(RuntimeError):
        lite.gpu_apply('quiet')
    assert not gpu.set_calls and gpu.closed


def test_gpu_unavailable_reports_error_and_closes(monkeypatch):
    gpu = FakeNvml()
    gpu.available = lambda: False
    gpu.problem = lambda: 'драйвер недоступен'
    monkeypatch.setattr(lite, '_nvml', lambda: gpu)
    with pytest.raises(RuntimeError, match='драйвер недоступен'):
        lite.gpu_status()
    assert gpu.closed and not gpu.set_calls


def test_gpu_default_requires_a_real_default(monkeypatch):
    gpu = FakeNvml()
    gpu._lib.nvmlDeviceGetPowerManagementDefaultLimit = lambda *args: 3
    monkeypatch.setattr(lite, '_nvml', lambda: gpu)
    with pytest.raises(RuntimeError, match='Заводской'):
        lite.gpu_apply('default')
    assert gpu.closed and not gpu.set_calls
