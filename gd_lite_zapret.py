"""Configure the already-installed zapret service without executing batch files."""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import tempfile
import winreg
from ctypes import wintypes as w
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gd_lite_network import _QUERY, _RUNNING, _STOPPED, _Service, zapret_apply

_KEY = r'SYSTEM\CurrentControlSet\Services\zapret'
_TAG = 'zapret-discord-youtube'
_DEFAULT = '1024-65535'
_PLACEHOLDER = b'203.0.113.113/32'
_GAME = Path('utils/game_filter.enabled')
_IPSET = Path('lists/ipset-all.txt')
_NAME = re.compile(r'general[\w ().+\-]{0,112}', re.I | re.ASCII)
_PORTS = re.compile(r'[0-9]{1,5}(?:-[0-9]{1,5})?(?:,[0-9]{1,5}(?:-[0-9]{1,5})?)*')
_START = re.compile(r'^\s*start\s+"[^"]*"\s+(?:/min\s+)?"%BIN%winws\.exe"\s+(.+)$', re.I | re.M)
_ARGS = re.compile(r'--([a-z0-9-]+)(?:=(?:"([^"\r\n]*)"|([^\s"]+)))?(?=\s|$)')
_FILES = {'hostlist', 'hostlist-exclude', 'ipset', 'ipset-exclude', 'dpi-desync-fake-tls', 'dpi-desync-fake-http',
          'dpi-desync-fake-quic', 'dpi-desync-fake-unknown', 'dpi-desync-fake-unknown-udp', 'dpi-desync-fake-discord',
          'dpi-desync-fake-stun', 'dpi-desync-split-seqovl-pattern', 'dpi-desync-fakedsplit-pattern'}
_SIMPLE = {'wf-tcp', 'wf-udp', 'filter-tcp', 'filter-udp', 'filter-l3', 'filter-l7', 'new', 'hostlist-domains',
           'hostlist-exclude-domains', 'ip-id', 'dpi-desync', 'dpi-desync-repeats', 'dpi-desync-fooling',
           'dpi-desync-split-pos', 'dpi-desync-any-protocol', 'dpi-desync-cutoff',
           'dpi-desync-fake-tls-mod', 'dpi-desync-split-seqovl', 'dpi-desync-hostfakesplit-mod',
           'dpi-desync-badseq-increment', 'dpi-desync-autottl', 'dpi-desync-ttl'}


def ports(text):
    text = ''.join(text.split())
    if len(text) > 512 or not _PORTS.fullmatch(text):
        raise ValueError('Диапазон портов: например 1024–65535 (через дефис) или 443,3478-3482.')
    for part in text.split(','):
        numbers = list(map(int, part.split('-')))
        if not 1 <= numbers[0] <= numbers[-1] <= 65535:
            raise ValueError('Порты должны быть в диапазоне 1–65535.')
    return text


@dataclass(frozen=True)
class Settings:
    strategy: str
    game: str
    tcp: str
    udp: str
    ipset: str

    def validated(self):
        if not _NAME.fullmatch(self.strategy):
            raise ValueError('Выбери стратегию из списка установленного zapret.')
        if self.game not in {'off', 'tcp', 'udp', 'all'} or self.ipset not in {'keep', 'none', 'loaded', 'any'}:
            raise ValueError('Неизвестный режим фильтрации.')
        return Settings(self.strategy, self.game, ports(self.tcp), ports(self.udp), self.ipset)


def _safe_file(path):
    if path.resolve() != path or (path.exists() and path.stat().st_nlink != 1):
        raise ValueError('Файлы и папки zapret не должны быть перенаправлены ссылками.')
    return path


def strategy_args(file, mode, tcp, udp):
    text = _safe_file(file).read_text(encoding='utf-8-sig', errors='replace')
    lines = re.sub(r'\^[ \t]*\n[ \t]*', ' ', text)
    matches = _START.findall(lines)
    if len(matches) != 1:
        raise ValueError('В стратегии должна быть одна стандартная строка запуска winws.exe.')
    line = matches[0].strip()
    args = []
    while line:
        match = _ARGS.match(line)
        if not match:
            raise ValueError('Стратегия содержит нестандартную команду. BAT не выполнялся.')
        key, quoted, bare = match.groups()
        value = quoted if quoted is not None else bare
        if key in _FILES:
            target = re.fullmatch(r'%(BIN|LISTS)%([\w .+()\-]+)', value or '', re.I | re.ASCII)
            if key.startswith('dpi-desync-') and re.fullmatch(r'0x[\da-fA-F]+|\^!', value or ''):
                value = '!' if value == '^!' else value
            elif not target or target[2] in {'.', '..'} or target[2].endswith(('.', ' ')):
                raise ValueError(f'Файл для --{key} должен быть в папке bin или lists установленного zapret.')
            else:
                path = file.parent / ('bin' if target[1].upper() == 'BIN' else 'lists') / target[2]
                value = str(_safe_file(path))
        elif key in _SIMPLE:
            if value is not None:
                value = re.sub('%GameFilterTCP%', tcp if mode in ('tcp', 'all') else '12', value, flags=re.I)
                value = re.sub('%GameFilterUDP%', udp if mode in ('udp', 'all') else '12', value, flags=re.I)
                value = re.sub('%GameFilter%', (udp if mode == 'udp' else tcp) if mode != 'off' else '12',
                               value, flags=re.I)
            if (key == 'new' and value is not None) or (key != 'new' and (
                    value is None or not re.fullmatch(r'[a-zA-Z0-9_.:/,+@=\-]+', value))):
                raise ValueError(f'Неподдерживаемое значение --{key}.')
        else:
            raise ValueError(f'Неподдерживаемый параметр --{key}.')
        args.append(f'--{key}' + (f'={value}' if value is not None else ''))
        line = line[match.end():].lstrip()
    return args


def service_binary(image):
    match = re.match(r'^\s*(?:"([^"\r\n]+)"|(\S+))(?:\s|$)', image or '')
    if not match:
        raise ValueError('Не удалось прочитать путь службы zapret.')
    path = Path(os.path.expandvars(match[1] or match[2]))
    if not path.is_absolute() or len(path.drive) != 2 or path.name.lower() != 'winws.exe' or path.parent.name.lower() != 'bin':
        raise ValueError('Lite работает с установленной службой zapret, запускающей bin/winws.exe.')
    return path


def _registered():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _KEY) as key:
            image = winreg.QueryValueEx(key, 'ImagePath')[0]
            try:
                strategy = winreg.QueryValueEx(key, _TAG)[0]
            except FileNotFoundError:
                strategy = None
    except FileNotFoundError:
        raise OSError('Служба zapret не установлена.') from None
    return image, strategy


def _running():
    with _Service(_QUERY) as service:
        state = service.state()
    if state not in (_RUNNING, _STOPPED):
        raise OSError('Дождись, пока служба zapret запустится или остановится, и повтори.')
    return state == _RUNNING


def _change(image, strategy):
    with _Service(0x0002) as service:
        if not service.handle:
            raise OSError('Служба zapret не установлена.')
        api = service.api.ChangeServiceConfigW
        api.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.LPCWSTR, w.LPCWSTR,
                        ctypes.POINTER(w.DWORD), w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.LPCWSTR]
        api.restype = w.BOOL
        if not api(service.handle, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, image, None, None, None, None, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _KEY, 0, winreg.KEY_SET_VALUE) as key:
        if strategy is not None:
            winreg.SetValueEx(key, _TAG, 0, winreg.REG_SZ, strategy)
        else:
            try:
                winreg.DeleteValue(key, _TAG)
            except FileNotFoundError:
                pass


def _read(file):
    try:
        with _safe_file(file).open('rb') as handle:
            data = handle.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise OSError('Список zapret слишком большой.')
            return data
    except FileNotFoundError:
        return None


def _write(path, value):
    _safe_file(path)
    if value is None:
        path.unlink(missing_ok=True)
        return
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            handle.write(value)
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def game_settings(data):
    values = {'mode': 'off', 'tcp': _DEFAULT, 'udp': _DEFAULT}
    for line in (data or b'').decode('ascii', errors='replace').splitlines():
        key, _, val = line.strip().lower().partition('=')
        if key == 'mode' and val in {'off', 'tcp', 'udp', 'all', 'disabled'}:
            values['mode'] = 'off' if val == 'disabled' else val
        elif key in {'tcp', 'udp', 'all'}:
            if not val or key == 'all':
                values['mode'] = key
            if val:
                try:
                    for protocol in ('tcp', 'udp') if key == 'all' else (key,):
                        values[protocol] = ports(val)
                except ValueError:
                    pass
    return values


def ipset_mode(data):
    if data is None:
        return 'missing'
    if not data.strip():
        return 'any'
    return 'none' if data.strip() == _PLACEHOLDER else 'loaded'


def ipset_changes(file, mode):
    if mode == 'keep':
        return {}
    current = _read(file)
    old = ipset_mode(current)
    if old == mode:
        return {}
    if current is None:
        raise OSError('ipset-all.txt отсутствует в папке lists.')
    backup = file.with_suffix('.txt.backup')
    if mode == 'loaded':
        source = _read(backup)
        if ipset_mode(source) != 'loaded':
            raise OSError('Нет резервной копии списка IPSet. Загрузи список через service.bat.')
        return {file: source}
    changes = {backup: current} if old == 'loaded' else {}
    changes[file] = _PLACEHOLDER + b'\r\n' if mode == 'none' else b''
    return changes


def inspect():
    image, current = _registered()
    binary = _safe_file(service_binary(image))
    if not binary.is_file():
        raise OSError('winws.exe из настроек службы не найден.')
    root = binary.parent.parent
    strategies = tuple(sorted((p.stem for p in root.glob('general*.bat') if _NAME.fullmatch(p.stem)),
                              key=lambda n: tuple(int(x) if x.isdigit() else x.casefold() for x in re.split(r'(\d+)', n))))
    return dict(root=root, current=current, strategies=strategies, running=_running(),
                game=game_settings(_read(root / _GAME)), ipset=ipset_mode(_read(root / _IPSET)))


def apply_settings(settings):
    cfg = settings.validated()
    old_image, old_strategy = _registered()
    binary = _safe_file(service_binary(old_image))
    if not binary.is_file():
        raise OSError('winws.exe из настроек службы не найден.')
    root = binary.parent.parent
    args = strategy_args(root / (cfg.strategy + '.bat'), cfg.game, cfg.tcp, cfg.udp)
    image = subprocess.list2cmdline([str(binary), *args])
    game_path = root / _GAME
    changes = ipset_changes(root / _IPSET, cfg.ipset)
    mode = 'disabled' if cfg.game == 'off' else cfg.game
    game_value = f'mode={mode}\r\ntcp={cfg.tcp}\r\nudp={cfg.udp}\r\n'.encode('ascii')
    if game_settings(_read(game_path)) != {'mode': cfg.game, 'tcp': cfg.tcp, 'udp': cfg.udp}:
        changes[game_path] = game_value
    backup = {path: _read(path) for path in changes}
    if not changes and (image, cfg.strategy) == (old_image, old_strategy):
        return
    was_running = _running()
    if _registered() != (old_image, old_strategy):
        raise OSError('Конфигурация службы успела измениться. Нажми «Обновить» и повтори.')
    if was_running:
        zapret_apply('off')
    written = []
    service_edit = False
    try:
        for path, value in changes.items():
            _write(path, value)
            written.append(path)
        service_edit = True
        _change(image, cfg.strategy)
        if _registered() != (image, cfg.strategy):
            raise OSError('Windows не подтвердила настройки службы.')
        if was_running:
            zapret_apply('on')
    except (OSError, ValueError, RuntimeError) as exc:
        failures = []
        recovery: list[Callable[[], object]] = [lambda: zapret_apply('off')] if service_edit and was_running else []
        recovery.extend(lambda p=p: _write(p, backup[p]) for p in reversed(written))
        if service_edit:
            recovery.append(lambda: _change(old_image, old_strategy))
        if was_running:
            recovery.append(lambda: zapret_apply('on'))
        for step in recovery:
            try:
                step()
            except (OSError, ValueError, RuntimeError) as restore_error:
                failures.append(str(restore_error))
        if failures:
            raise OSError(f'{exc} Откат неполный: {"; ".join(failures)}') from exc
        raise OSError(f'{exc} Прежние настройки восстановлены.') from exc
