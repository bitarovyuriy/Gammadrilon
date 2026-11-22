"""Monochrome controls for the Lite edition."""
from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from ctypes import wintypes as w
from pathlib import Path
from threading import Thread
from tkinter import messagebox, ttk

import gd_lite_style as style
from gd_lite_actions import ACTIONS, TITLE, backend
from gd_lite_display import MAXIMUM, MINIMUM, Brightness
from gd_lite_elevation import elevated_apply, elevated_zapret

DIM = 65
IPSETS = {'keep': 'Не менять', 'loaded': 'Из списка', 'none': 'Выключен', 'any': 'Любой IP'}


class LiteWindow(tk.Tk):
    def __init__(self, *, hide=False, brightness=None, preview=False):
        super().__init__(className='GammadrilonLite')
        if hide:
            self.withdraw()
        self.title(TITLE)
        style.configure(self)
        icon = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'icon-lite.ico'
        if icon.is_file():
            self.iconbitmap(str(icon))
        self._live = not hide and not preview
        self._brightness = brightness if brightness is not None else Brightness()
        self._percent = tk.DoubleVar(value=100)
        self._applied = 100
        self._bright_timer = self._sensor_timer = None
        self._busy = self._sampling = self._zap_loaded = self._zap_seen = False
        self._result: tuple[str, object] | None = None
        self._sample: list[float | None] | None = None
        self._hotkeys = self._manual_cpu = None
        self._controls = []
        self.rows = {k: tk.StringVar(value=v) for k, v in {
            'cpu': 'Схема питания Windows', 'gpu': 'Лимит мощности NVIDIA',
            'dns': 'Адаптер по умолчанию', 'zapret': 'Установленная служба Windows',
        }.items()}
        self.bright_note = tk.StringVar(value='Основной экран')
        self.bright_readout = tk.StringVar(value='100 %')
        self.cpu = tk.StringVar(value='—')
        self.gpu = tk.StringVar(value='—')
        self.cpu_note = tk.StringVar(value='Замер по кнопке')
        self.footer = tk.StringVar(value='Яркость  Ctrl+Alt+B      ·      Питание CPU  Ctrl+Alt+P')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        content = ttk.Frame(self, padding=(24, 14, 24, 12))
        content.grid(sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)
        ttk.Label(content, text=TITLE, style='Title.TLabel').grid(sticky='w', pady=(0, 12))
        self.tabs = ttk.Notebook(content)
        self.tabs.grid(sticky='nsew')
        self.tabs.enable_traversal()
        self.home = ttk.Frame(self.tabs, padding=(0, 12, 0, 0))
        self.zap = ttk.Frame(self.tabs, padding=(0, 14, 0, 0))
        self.tabs.add(self.home, text='Пульт')
        self.tabs.add(self.zap, text='zapret')
        self._home()
        self._zapret()
        ttk.Separator(content).grid(sticky='ew', pady=(10, 8))
        ttk.Label(content, textvariable=self.footer, style='Muted.TLabel', wraplength=510).grid(sticky='w')
        self.bind('<<ActionDone>>', self._action_done)
        self.bind('<<SensorsDone>>', self._sensors_done)
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.update_idletasks()
        self.minsize(max(640, self.winfo_reqwidth()), self.winfo_reqheight())
        if not hide:
            self._dark_titlebar()
        if self._live:
            from gd_lite_keys import Hotkeys

            self._hotkeys = Hotkeys(self, {'brightness': self._toggle_brightness, 'power': self._toggle_cpu})
            errors = self._hotkeys.start()
            if errors:
                valid = [text for key, text in (('brightness', 'Яркость Ctrl+Alt+B'), ('power', 'Питание Ctrl+Alt+P'))
                         if key not in errors]
                self.footer.set('   ·   '.join([*valid, *errors.values()]))
            self.tabs.bind('<<NotebookTabChanged>>', self._view_changed)
            self.bind('<Map>', self._visibility, add='+')
            self.bind('<Unmap>', self._visibility, add='+')
            self._view_changed()

    def _dark_titlebar(self):
        get_parent = ctypes.windll.user32.GetParent
        get_parent.argtypes = [w.HWND]
        get_parent.restype = w.HWND
        set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attr.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
        dark = w.BOOL(1)
        hwnd = get_parent(self.winfo_id()) or self.winfo_id()
        for attribute in (20, 19):
            if set_attr(hwnd, attribute, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
                break

    def _button(self, parent, text, action, *, primary=False, tracked=True):
        button = ttk.Button(parent, text=text, command=action, style='Primary.TButton' if primary else 'TButton')
        if tracked:
            self._controls.append(button)
        return button

    def _row(self, index, name, note):
        ttk.Separator(self.home).grid(row=2 * index - 1, sticky='ew')
        row = ttk.Frame(self.home, padding=(0, 7))
        row.grid(row=2 * index, sticky='ew')
        row.columnconfigure(1, weight=1)
        left = ttk.Frame(row)
        left.grid(sticky='w', padx=(0, 14))
        ttk.Label(left, text=name, style='Section.TLabel').pack(anchor='w')
        ttk.Label(left, textvariable=note, style='Muted.TLabel', wraplength=248).pack(anchor='w')
        actions = ttk.Frame(row)
        actions.grid(row=0, column=1, sticky='e')
        return actions

    def _home(self):
        self.home.columnconfigure(0, weight=1)
        sensors = ttk.Frame(self.home, style='Sensor.TFrame', padding=(14, 8))
        sensors.grid(row=0, sticky='ew', pady=(0, 12))
        sensors.columnconfigure((0, 2), weight=1)
        for i, name, value in ((0, 'CPU', self.cpu), (2, 'GPU', self.gpu)):
            ttk.Label(sensors, text=name, style='Sensor.TLabel').grid(row=0, column=i, sticky='w')
            ttk.Label(sensors, textvariable=value, style='Reading.TLabel').grid(row=1, column=i, sticky='w')
        ttk.Label(sensors, textvariable=self.cpu_note, style='Sensor.TLabel', wraplength=178).grid(row=2, sticky='w')
        ttk.Label(sensors, text='NVIDIA · датчик', style='Sensor.TLabel').grid(row=2, column=2, sticky='w')
        self._button(sensors, 'Замер CPU', self._measure_cpu).grid(row=1, column=1, padx=(0, 22))
        row = self._row(1, 'Яркость', self.bright_note)
        ttk.Label(row, textvariable=self.bright_readout, width=6).pack(side='left')
        bar = ttk.Scale(row, from_=MINIMUM, to=MAXIMUM, variable=self._percent, takefocus=True,
                        command=self._slider_moved, length=120)
        bar.pack(side='left', padx=(0, 10))
        bar.bind('<Left>', lambda _: self._bright_step(-5))
        bar.bind('<Right>', lambda _: self._bright_step(5))
        self._brightness_switch = self._button(row, 'Приглушить', self._toggle_brightness, tracked=False)
        self._brightness_switch.pack(side='left')
        for i, (section, title) in enumerate((('cpu', 'Питание CPU'), ('gpu', 'Мощность GPU'), ('dns', 'DNS')), 2):
            row = self._row(i, title, self.rows[section])
            options = ACTIONS[section]
            select = ttk.Combobox(row, width=14, state='readonly', values=tuple(text for _, text in options))
            select.current(0)
            select.pack(side='left', padx=(0, 6))
            self._controls.append(select)
            self._button(row, 'Применить', lambda s=section, o=options, c=select: self._start(s, o[c.current()][0])).pack(
                side='left', padx=(0, 6))
            fn = self._toggle_cpu if section == 'cpu' else lambda s=section: self._start(s)
            self._button(row, 'Эко / Буст' if section == 'cpu' else 'Проверить', fn).pack(side='left')
        row = self._row(5, 'zapret', self.rows['zapret'])
        self._zap_switches(row)
        self._button(row, 'Настроить', lambda: self.tabs.select(self.zap), tracked=False).pack(side='left')

    def _zap_switches(self, row):
        for key, text in (('on', 'Вкл.'), ('off', 'Выкл.')):
            self._button(row, text, lambda k=key: self._start('zapret', k)).pack(side='left', padx=(0, 6))

    def _zapret(self):
        p = self.zap
        p.columnconfigure(1, weight=1)
        ttk.Label(p, text='Обход DPI', style='Section.TLabel').grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(p, textvariable=self.rows['zapret'], style='Muted.TLabel', wraplength=305).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(4, 10))
        actions = ttk.Frame(p)
        actions.grid(row=1, column=2, sticky='e', pady=(0, 10))
        self._zap_switches(actions)
        ttk.Separator(p).grid(row=2, column=0, columnspan=3, sticky='ew', pady=(0, 12))
        ttk.Label(p, text='Стратегия').grid(row=3, column=0, sticky='w', padx=(0, 16))
        self.strategy = ttk.Combobox(p, width=24, state='readonly')
        self.strategy.grid(row=3, column=1, sticky='ew', padx=(0, 6))
        self._button(p, 'Обновить', self._inspect_zapret).grid(row=3, column=2, sticky='e')
        self.strategy_status = tk.StringVar(value='Из папки установленной службы')
        ttk.Label(p, textvariable=self.strategy_status, style='Muted.TLabel', wraplength=360).grid(
            row=4, column=1, columnspan=2, sticky='w', pady=(4, 12))
        ttk.Label(p, text='GameFilter').grid(row=5, column=0, sticky='nw', pady=(6, 0))
        grid = ttk.Frame(p)
        grid.grid(row=5, column=1, columnspan=2, sticky='ew', pady=(0, 9))
        grid.columnconfigure(1, weight=1)
        self.game = {key: (tk.BooleanVar(), tk.StringVar(value='1024-65535')) for key in ('tcp', 'udp')}
        self._zap_fields: list[ttk.Widget] = [self.strategy]
        self._port_entries = {}
        for i, (key, (flag, value)) in enumerate(self.game.items()):
            check = ttk.Checkbutton(grid, text=key.upper(), variable=flag, command=self._update_controls)
            check.grid(row=i, column=0, sticky='w', padx=(0, 12), pady=(0, 5))
            entry = ttk.Entry(grid, textvariable=value, width=16)
            entry.grid(row=i, column=1, sticky='ew', pady=(0, 5))
            self._port_entries[key] = entry
            self._zap_fields.append(check)
        ttk.Label(p, text='IPSet').grid(row=6, column=0, sticky='w')
        self.ipset = ttk.Combobox(p, state='readonly', width=15, values=tuple(IPSETS.values()))
        self.ipset.current(0)
        self.ipset.grid(row=6, column=1, sticky='w')
        self.ipset_status = tk.StringVar()
        ttk.Label(p, textvariable=self.ipset_status, style='Muted.TLabel').grid(row=6, column=2, sticky='e')
        ttk.Label(p, text='Если страдают игры, попробуй отключить GameFilter.', style='Muted.TLabel').grid(
            row=7, column=0, columnspan=3, sticky='w', pady=(10, 12))
        ttk.Separator(p).grid(row=8, column=0, columnspan=3, sticky='ew', pady=(0, 10))
        ttk.Label(p, text='Работающая служба перезапустится.', style='Muted.TLabel').grid(
            row=9, column=0, columnspan=2, sticky='w')
        submit = self._button(p, 'Применить настройки', self._save_zapret, primary=True, tracked=False)
        submit.grid(row=9, column=2, sticky='e')
        self._zap_fields.extend((self.ipset, submit))
        self._update_controls()

    def _update_controls(self):
        for control in self._controls:
            control.state(['disabled' if self._busy else '!disabled'])
        unavailable = self._busy or not self._zap_loaded
        for control in self._zap_fields:
            control.state(['disabled' if unavailable else '!disabled'])
        for key, entry in self._port_entries.items():
            entry.state(['disabled' if unavailable or not self.game[key][0].get() else '!disabled'])

    def _slider_moved(self, _):
        percent = round(self._percent.get())
        self.bright_readout.set(f'{percent} %')
        self._cancel('_bright_timer')
        self._bright_timer = self.after(100, self._bright_button, percent)

    def _cancel(self, attr):
        timer = getattr(self, attr)
        if timer is not None:
            self.after_cancel(timer)
            setattr(self, attr, None)

    def _bright_button(self, percent):
        self._cancel('_bright_timer')
        try:
            self._brightness.apply(percent)
        except (OSError, ValueError) as exc:
            self.bright_note.set(str(exc))
        else:
            self._applied = percent
            self.bright_note.set('Основной экран')
        self._percent.set(self._applied)
        self.bright_readout.set(f'{self._applied} %')
        self._brightness_switch.configure(text='Вернуть 100 %' if self._applied <= DIM else 'Приглушить')

    def _toggle_brightness(self):
        self._bright_button(100 if self._applied <= DIM else DIM)

    def _bright_step(self, step):
        self._percent.set(max(MINIMUM, min(MAXIMUM, round(self._percent.get()) + step)))
        self._slider_moved(None)
        return 'break'

    def _toggle_cpu(self):
        self._start('cpu', 'toggle')

    def _start(self, section, value=None):
        def work():
            if value == 'toggle' and section == 'cpu':
                import gd_power

                active = gd_power.state_snapshot().get('profile')
                elevated_apply('cpu', 'boost' if active == 'eco' else 'eco')
            elif value is not None:
                elevated_apply(section, value)
            return backend(section)[0]()

        self._job(section, work, 'Применяю…' if value is not None else 'Проверяю…')

    def _inspect_zapret(self):
        from gd_lite_zapret import inspect

        self._zap_seen = True
        self._zap_loaded = False
        self._job('zapret', inspect, 'Читаю настройки службы…')

    def _save_zapret(self):
        from gd_lite_zapret import Settings, inspect

        tcp, udp = self.game['tcp'][0].get(), self.game['udp'][0].get()
        game = 'all' if tcp and udp else ('tcp' if tcp else ('udp' if udp else 'off'))
        mode = next(key for key, label in IPSETS.items() if label == self.ipset.get())
        try:
            cfg = Settings(self.strategy.get(), game, self.game['tcp'][1].get(), self.game['udp'][1].get(), mode)
            cfg = cfg.validated()
        except ValueError as exc:
            self.rows['zapret'].set(str(exc))
            return
        if mode == 'any' and not messagebox.askokcancel(TITLE, 'Расширить IPSet до любых IP-адресов?', parent=self):
            return

        def work():
            elevated_zapret(cfg)
            return inspect()

        self._job('zapret', work, 'Применяю настройки…')

    def _show_zapret(self, info):
        self.strategy.configure(values=info['strategies'])
        self.strategy.set(info['current'] if info['current'] in info['strategies'] else '')
        self.strategy_status.set('В службе: ' + (info['current'] or 'не выбрана'))
        for key, (enabled, value) in self.game.items():
            enabled.set(info['game']['mode'] in (key, 'all'))
            value.set(info['game'][key])
        self.ipset.current(0)
        self.ipset_status.set('Сейчас: ' + IPSETS.get(info['ipset'], 'нет списка'))
        self.rows['zapret'].set('Служба работает' if info['running'] else 'Служба остановлена')
        self._zap_loaded = bool(info['strategies'])

    def _job(self, section, work, pending):
        if self._busy:
            return
        self._busy = True
        self._update_controls()
        (self.cpu_note if section == 'sensor' else self.rows[section]).set(pending)

        def run():
            try:
                result = work()
            except Exception as exc:
                result = str(exc)
            self._result = section, result
            self.event_generate('<<ActionDone>>', when='tail')

        Thread(target=run, daemon=True).start()

    def _action_done(self, _):
        if self._result is None:
            return
        section, result = self._result
        self._result = None
        if section == 'zapret' and isinstance(result, dict):
            self._show_zapret(result)
        elif section == 'sensor':
            if isinstance(result, (int, float)):
                self._manual_cpu = result
                self.cpu.set(f'{result:.0f} °C')
                self.cpu_note.set('Последний ручной замер')
            else:
                self.cpu_note.set(str(result) if result else 'Датчик недоступен')
        else:
            self.rows[section].set(str(result))
        self._busy = False
        self._update_controls()
        self._view_changed()

    def _measure_cpu(self):
        from gd_lite_temps import cpu_measure

        self._job('sensor', cpu_measure, 'Замеряю…')

    def _visible_home(self):
        return self.state() in ('normal', 'zoomed') and self.tabs.select() == str(self.home)

    def _visibility(self, event):
        if event.widget is self:
            if event.type == tk.EventType.Unmap:
                self._cancel('_sensor_timer')
            else:
                self._view_changed()

    def _view_changed(self, _=None):
        if not self._live:
            return
        if not self._visible_home():
            self._cancel('_sensor_timer')
            if self.tabs.select() == str(self.zap) and self.state() in ('normal', 'zoomed'):
                if not self._busy and not self._zap_seen:
                    self._inspect_zapret()
        elif self._sensor_timer is None and not self._sampling:
            self._sensor_timer = self.after(350, self._sample_sensors)

    def _sample_sensors(self):
        self._sensor_timer = None
        if not self._visible_home():
            return
        self._sampling = True

        def sample():
            from gd_lite_temps import cpu_temperature, gpu_temperature

            values = []
            for read in (cpu_temperature, gpu_temperature):
                try:
                    values.append(read())
                except (OSError, ValueError, RuntimeError, AttributeError):
                    values.append(None)
            self._sample = values
            try:
                self.event_generate('<<SensorsDone>>', when='tail')
            except (RuntimeError, tk.TclError):
                pass

        Thread(target=sample, daemon=True).start()

    def _sensors_done(self, _):
        self._sampling = False
        if self._sample is None:
            return
        cpu, gpu = self._sample
        shown_cpu = self._manual_cpu if cpu is None else cpu
        self.cpu.set('—' if shown_cpu is None else f'{shown_cpu:.0f} °C')
        if not self._busy:
            if cpu is not None and self._manual_cpu is None:
                self.cpu_note.set('Обновляется в окне')
            elif self.cpu_note.get() == 'Обновляется в окне':
                self.cpu_note.set('Замер по кнопке' if self._manual_cpu is None else 'Последний ручной замер')
        self.gpu.set('—' if gpu is None else f'{gpu:.0f} °C')
        if self._visible_home():
            self._sensor_timer = self.after(5000, self._sample_sensors)

    def _close(self):
        if self._busy:
            return
        try:
            self._brightness.restore()
        except OSError as exc:
            if not messagebox.askokcancel(TITLE, f'Не удалось вернуть яркость: {exc}\nВсё равно закрыть?', parent=self):
                return
        self.destroy()

    def destroy(self):
        self._cancel('_bright_timer')
        self._cancel('_sensor_timer')
        if self._hotkeys is not None:
            self._hotkeys.close()
            self._hotkeys = None
        super().destroy()
