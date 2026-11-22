"""Точечное управление питанием Lite без фоновых служб."""

import ctypes
import threading

_CPU_LOCK = threading.Lock()
_GPU_LOCK = threading.Lock()


def _power():
    import gd_power
    return gd_power


def cpu_status() -> str:
    with _CPU_LOCK:
        state = _power().state_snapshot()
    name = state.get('active_name') or state.get('active_scheme') or 'неизвестна'
    profile = state.get('profile') or 'не выбран'
    problem = state.get('state_problem')
    return f"CPU: {name}; профиль: {profile}" + (f"; ошибка состояния: {problem}" if problem else '')


def cpu_apply(profile: str) -> str:
    if profile not in ('eco', 'normal', 'boost', 'restore'):
        raise ValueError(f'Неизвестный профиль CPU: {profile}')
    with _CPU_LOCK:
        power = _power()
        if profile == 'restore':
            old, _ = power.read_state_report()
            active = power.get_active_scheme() if old and old.get('clone_scheme') else None
            report = power.restore_report()
            if not report['ok']:
                raise RuntimeError(report.get('note') or 'Не удалось восстановить схему питания')
            if (old and old.get('original_scheme') and active
                    and active.upper() == old['clone_scheme'].upper()
                    and power.get_active_scheme().upper() != old['original_scheme'].upper()):
                raise RuntimeError('Не удалось подтвердить восстановление схемы питания')
            return 'CPU: исходная схема восстановлена' if report.get('applied') else 'CPU: восстанавливать нечего'

        _, problem = power.read_state_report()
        if problem:
            raise RuntimeError(f'Файл отката CPU повреждён: {problem}')
        result = power.apply_profile(profile)
        state = power.state_snapshot()
        clone = result.get('clone_scheme') or ''
        if (not clone or (state.get('active_scheme') or '').upper() != clone.upper()
                or state.get('profile') != profile):
            raise RuntimeError('Не удалось подтвердить активную схему CPU; данные отката сохранены')
        for key, expected in power.PROFILES[profile].items():
            actual = state.get('values', {}).get(key) or {}
            if actual.get('ac') != expected or actual.get('dc') != expected:
                raise RuntimeError(f'Не удалось подтвердить настройку CPU {key}; данные отката сохранены')
        return f'CPU: профиль {profile} применён'


def _nvml():
    import gd_nvml
    return gd_nvml.Nvml()


def _read_uint(gpu, name):
    value = ctypes.c_uint()
    result = getattr(gpu._lib, name)(gpu._device, ctypes.byref(value))
    return value.value if result == 0 else None


def _limits(gpu):
    low, high = ctypes.c_uint(), ctypes.c_uint()
    result = gpu._lib.nvmlDeviceGetPowerManagementLimitConstraints(
        gpu._device, ctypes.byref(low), ctypes.byref(high))
    return {
        'current': _read_uint(gpu, 'nvmlDeviceGetPowerManagementLimit'),
        'default': _read_uint(gpu, 'nvmlDeviceGetPowerManagementDefaultLimit'),
        'min': low.value if result == 0 else None,
        'max': high.value if result == 0 else None,
    }


def _check_gpu(gpu):
    if not gpu.available():
        raise RuntimeError(gpu.problem() or 'NVML недоступен')


def _watts(value):
    return 'недоступно' if value is None else f'{value / 1000:g} Вт'


def gpu_status() -> str:
    with _GPU_LOCK:
        gpu = _nvml()
        try:
            _check_gpu(gpu)
            limits = _limits(gpu)
            if limits['current'] is None:
                raise RuntimeError('Лимит мощности NVIDIA недоступен')
            return (f"GPU: лимит {_watts(limits['current'])}; "
                    f"диапазон {_watts(limits['min'])} — {_watts(limits['max'])}; "
                    f"заводской {_watts(limits['default'])}")
        finally:
            gpu.shutdown()


def gpu_apply(profile: str) -> str:
    if profile not in ('quiet', 'default', 'game', 'max'):
        raise ValueError(f'Неизвестный профиль GPU: {profile}')
    with _GPU_LOCK:
        gpu = _nvml()
        try:
            _check_gpu(gpu)
            limits = _limits(gpu)
            low, high = limits['min'], limits['max']
            if low is None or high is None or low <= 0 or high < low:
                raise RuntimeError('Диапазон лимита мощности NVIDIA недоступен')
            if profile == 'quiet':
                target = 150000
            elif profile == 'default':
                target = limits['default']
                if target is None or target <= 0:
                    raise RuntimeError('Заводской лимит мощности NVIDIA недоступен')
            elif profile == 'game':
                target = round(high / 1000 * 0.93) * 1000
            else:
                target = high
            target = max(low, min(high, target))
            ok, note = gpu.set_power_limit(target / 1000)
            if not ok:
                raise RuntimeError(note or 'Не удалось установить лимит мощности NVIDIA')
            actual = _read_uint(gpu, 'nvmlDeviceGetPowerManagementLimit')
            if actual != target:
                raise RuntimeError('Не удалось подтвердить лимит мощности NVIDIA')
            return f'GPU: профиль {profile}, лимит {_watts(actual)}'
        finally:
            gpu.shutdown()
