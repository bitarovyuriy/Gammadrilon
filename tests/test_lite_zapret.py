from pathlib import Path

import pytest

import gd_lite_zapret as z


def test_reads_strategy_without_running_batch(tmp_path):
    file = tmp_path / 'general (ALT2).bat'
    file.write_text('call anything.bat\nstart "name" /min "%BIN%winws.exe" '
                    '--wf-tcp=80,%GameFilterTCP% --wf-udp=443,%GameFilterUDP% ^\n'
                    '--hostlist="%LISTS%list general.txt" --new\n')
    args = z.strategy_args(file, 'tcp', '100-110', '3000')
    assert args == ['--wf-tcp=80,100-110', '--wf-udp=443,12',
                    f'--hostlist={tmp_path / "lists/list general.txt"}', '--new']


@pytest.mark.parametrize('params', ['--new & whoami', '--debug=@hi.txt', '--hostlist="%LISTS%../other.txt"',
                                   '--wf-tcp=%BAD%', '@unknown.txt', '--hostlist="C:/other.txt"'])
def test_rejects_unhandled_or_unsafe_batch_arguments(tmp_path, params):
    file = tmp_path / 'general.bat'
    file.write_text(f'start "name" /min "%BIN%winws.exe" --wf-tcp=80 {params}\n')
    with pytest.raises(ValueError):
        z.strategy_args(file, 'off', '1024-65535', '1024-65535')


def test_ipset_can_roundtrip_original_list(tmp_path):
    ipset = tmp_path / 'ipset-all.txt'
    backup = tmp_path / 'ipset-all.txt.backup'
    ipset.write_bytes(b'192.0.2.0/24\r\n')
    for path, data in z.ipset_changes(ipset, 'none').items():
        path.write_bytes(data)
    assert backup.read_bytes() == b'192.0.2.0/24\r\n'
    assert z.ipset_mode(ipset.read_bytes()) == 'none'
    for path, data in z.ipset_changes(ipset, 'loaded').items():
        path.write_bytes(data)
    assert ipset.read_bytes() == b'192.0.2.0/24\r\n'


def test_failed_restart_rolls_back_image_and_files(tmp_path, monkeypatch):
    exe = tmp_path / 'bin/winws.exe'
    exe.parent.mkdir()
    exe.touch()
    for sub in ('utils', 'lists'):
        (tmp_path / sub).mkdir()
    game = tmp_path / 'utils/game_filter.enabled'
    game.write_bytes(b'all\r\n')
    (tmp_path / 'general.bat').write_text('start "name" /min "%BIN%winws.exe" --wf-tcp=80,%GameFilterTCP%')
    live = {'image': f'"{exe}" --new', 'strategy': 'old', 'active': True}
    old_image = live['image']
    log = []

    def power(mode):
        log.append(mode)
        if mode == 'on' and live['image'] != old_image:
            raise OSError('new image failed')
        live['active'] = mode == 'on'

    monkeypatch.setattr(z, '_registered', lambda: (live['image'], live['strategy']))
    monkeypatch.setattr(z, '_running', lambda: live['active'])
    monkeypatch.setattr(z, '_change', lambda image, strategy: live.update(image=image, strategy=strategy))
    monkeypatch.setattr(z, 'zapret_apply', power)
    with pytest.raises(OSError, match='восстановлены'):
        z.apply_settings(z.Settings('general', 'tcp', '1024-65535', '1024-65535', 'keep'))
    assert live == {'image': old_image, 'strategy': 'old', 'active': True}
    assert game.read_bytes() == b'all\r\n'
    assert log == ['off', 'on', 'off', 'on']


def test_parse_service_binary_requires_existing_winws():
    with pytest.raises(ValueError):
        z.service_binary(r'C:\Windows\System32\cmd.exe /c nonsense')
    assert z.service_binary(r'"D:\dir with spaces\bin\winws.exe" --abc') == Path(r'D:\dir with spaces\bin\winws.exe')
