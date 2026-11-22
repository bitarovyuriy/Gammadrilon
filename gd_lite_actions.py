"""The fixed commands exposed to the one-shot elevated worker."""
from __future__ import annotations

import ctypes

TITLE = 'Gammadrilon Lite'
ACTIONS = {
    'cpu': (('eco', 'Эко'), ('normal', 'Обычный'), ('boost', 'Буст'), ('restore', 'Вернуть')),
    'gpu': (('quiet', 'Тихий'), ('default', 'По умолчанию'), ('game', 'Игровой'), ('max', 'Максимум')),
    'dns': (('cloudflare', 'Cloudflare'), ('google', 'Google'), ('quad9', 'Quad9'),
            ('adguard', 'AdGuard'), ('yandex', 'Яндекс'), ('restore', 'Вернуть')),
    'zapret': (('on', 'Включить'), ('off', 'Выключить')),
}


def validate_action(section: str, value: str) -> None:
    if value not in {key for key, _ in ACTIONS.get(section, ())}:
        raise ValueError('Неизвестная команда Lite.')


def backend(section: str):
    if section in ('cpu', 'gpu'):
        import gd_lite_power as module
    elif section in ('dns', 'zapret'):
        import gd_lite_network as module
    else:
        raise ValueError('Неизвестный раздел Lite.')
    return getattr(module, section + '_status'), getattr(module, section + '_apply')


def worker(section: str, value: str) -> int:
    try:
        validate_action(section, value)
    except ValueError:
        return 2
    if not ctypes.windll.shell32.IsUserAnAdmin():
        return 3
    try:
        backend(section)[1](value)
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), TITLE, 0x10010)
        return 1
    return 0
