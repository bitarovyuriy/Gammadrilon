import ctypes
import json
import os
import time

import pytest

import gd_lite_temps as temps


def test_fresh_full_version_sample_only(tmp_path, monkeypatch):
    sample = tmp_path / 'Gammadrilon' / 'sensors.json'
    sample.parent.mkdir()
    sample.write_text(json.dumps({'ok': True, 'cpu': 56.4}), encoding='utf-8')
    monkeypatch.setenv('ProgramData', str(tmp_path))
    monkeypatch.setattr(temps, '_cached_cpu', None)
    assert temps.cpu_temperature() == 56.4
    old = time.time() - 6
    os.utime(sample, (old, old))
    assert temps.cpu_temperature() is None


def test_gpu_temperature_reads_nvml_and_shuts_down(monkeypatch):
    class Function:
        def __call__(self, device, sensor, value):
            assert sensor == 0
            ctypes.cast(value, ctypes.POINTER(ctypes.c_uint)).contents.value = 63
            return 0

    class GPU:
        _lib = type('Library', (), {'nvmlDeviceGetTemperature': Function()})()
        _device = ctypes.c_void_p(123)
        closed = False

        def available(self):
            return True

        def shutdown(self):
            self.closed = True

    gpu = GPU()
    monkeypatch.setattr(temps, '_nvml', lambda: gpu)
    assert temps.gpu_temperature() == 63.0
    assert gpu.closed


def test_cpu_measure_decodes_exit_code_and_caches(monkeypatch):
    monkeypatch.setattr(temps, '_run_helper', lambda: 3654)
    monkeypatch.setattr(temps, '_cached_cpu', None)
    assert temps.cpu_measure() == 65.4
    assert temps.cpu_temperature() == 65.4


def test_cpu_measure_rejects_error_exit_codes(monkeypatch):
    monkeypatch.setattr(temps, '_run_helper', lambda: 1)
    monkeypatch.setattr(temps, '_cached_cpu', None)
    assert isinstance(temps.cpu_measure(), str)
    assert temps._cached_cpu is None


@pytest.mark.parametrize('value', [None, True, -1, 151, '52', float('nan'), float('inf')])
def test_invalid_full_sample_is_not_returned(tmp_path, monkeypatch, value):
    sample = tmp_path / 'Gammadrilon' / 'sensors.json'
    sample.parent.mkdir()
    sample.write_text(json.dumps({'ok': True, 'cpu': value}), encoding='utf-8')
    monkeypatch.setenv('ProgramData', str(tmp_path))
    monkeypatch.setattr(temps, '_cached_cpu', None)
    assert temps.cpu_temperature() is None


def test_gpu_unavailable_is_cleanly_shutdown(monkeypatch):
    class GPU:
        closed = False
        def available(self):
            return False
        def shutdown(self):
            self.closed = True
    gpu = GPU()
    monkeypatch.setattr(temps, '_nvml', lambda: gpu)
    assert temps.gpu_temperature() is None
    assert gpu.closed


def test_cancelled_cpu_measure_returns_message(monkeypatch):
    def cancelled():
        raise RuntimeError('CPU measurement was cancelled')
    monkeypatch.setattr(temps, '_run_helper', cancelled)
    assert 'cancelled' in temps.cpu_measure()


def test_shell_helper_uses_fixed_args_and_closes_handle(monkeypatch):
    class Fn:
        def __init__(self, func):
            self.func = func
        def __call__(self, *args):
            return self.func(*args)

    seen = {}
    def execute(ptr):
        info = ctypes.cast(ptr, ctypes.POINTER(temps._ShellExecuteInfo)).contents
        seen.update(verb=info.lpVerb, args=info.lpParameters, path=info.lpFile)
        info.hProcess = 42
        return 1
    def exit_code(handle, ptr):
        ctypes.cast(ptr, ctypes.POINTER(ctypes.wintypes.DWORD)).contents.value = 3678
        return 1
    class Shell:
        ShellExecuteExW = Fn(execute)
    class Kernel:
        WaitForSingleObject = Fn(lambda handle, timeout: 0)
        GetExitCodeProcess = Fn(exit_code)
        CloseHandle = Fn(lambda handle: seen.update(closed=handle) or 1)
    monkeypatch.setattr(temps.Path, 'is_file', lambda self: True)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **kwargs: Shell() if name == 'shell32' else Kernel())
    assert temps._run_helper() == 3678
    assert seen['verb'] == 'runas'
    assert seen['args'] == '--once'
    assert seen['path'].endswith('vendor\\lhm\\LiteSensors.exe')
    assert seen['closed'] == 42
