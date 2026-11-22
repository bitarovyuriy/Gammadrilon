"""A quiet monochrome ttk theme, no external assets."""
from tkinter import ttk

BG, PANEL, LINE = '#101010', '#181818', '#383838'
TEXT, MUTED, WHITE = '#ededed', '#b6b6b6', '#f2f2f2'
FONT = 'Segoe UI'


def configure(window):
    window.configure(background=BG)
    window.option_add('*TCombobox*Listbox.background', PANEL)
    window.option_add('*TCombobox*Listbox.foreground', TEXT)
    window.option_add('*TCombobox*Listbox.selectBackground', WHITE)
    window.option_add('*TCombobox*Listbox.selectForeground', BG)
    window.option_add('*TCombobox*Listbox.font', (FONT, 10))
    s = ttk.Style(window)
    s.theme_use('clam')
    s.configure('.', background=BG, foreground=TEXT, font=(FONT, 10), bordercolor=LINE,
                lightcolor=LINE, darkcolor=LINE, troughcolor=LINE, focuscolor=WHITE)
    s.configure('TLabel', background=BG, foreground=TEXT)
    s.configure('Muted.TLabel', foreground=MUTED)
    s.configure('Title.TLabel', font=(FONT, 20, 'bold'))
    s.configure('Section.TLabel', font=(FONT, 12, 'bold'))
    s.configure('Reading.TLabel', font=('Consolas', 19), background=PANEL)
    s.configure('Sensor.TLabel', background=PANEL, foreground=MUTED)
    s.configure('Sensor.TFrame', background=PANEL)
    s.configure('TSeparator', background=LINE)
    s.configure('TButton', padding=(12, 6), borderwidth=1, background=PANEL, foreground=TEXT)
    s.map('TButton', background=[('disabled', BG), ('pressed', '#424242'), ('active', '#303030')],
          foreground=[('disabled', '#8d8d8d')], bordercolor=[('focus', WHITE), ('active', '#646464')])
    s.configure('Primary.TButton', background=WHITE, foreground=BG)
    s.map('Primary.TButton', background=[('disabled', PANEL), ('pressed', '#bfbfbf'), ('active', '#d6d6d6')],
          foreground=[('disabled', '#a1a1a1')])
    s.configure('TNotebook', background=BG, borderwidth=0, tabmargins=0, lightcolor=BG, darkcolor=BG)
    s.configure('TNotebook.Tab', background=BG, foreground=MUTED, borderwidth=1, padding=(20, 7))
    s.map('TNotebook.Tab', expand=[('selected', (0, 0, 0, 0))])
    s.map('TNotebook.Tab', background=[('selected', WHITE), ('active', PANEL)],
          foreground=[('selected', BG), ('active', TEXT)])
    s.configure('TCombobox', fieldbackground=PANEL, background=PANEL, foreground=TEXT, arrowcolor=TEXT,
                selectforeground=TEXT, selectbackground=PANEL, insertcolor=TEXT, padding=(8, 6))
    s.map('TCombobox', fieldbackground=[('readonly', PANEL)], foreground=[('disabled', '#8d8d8d')],
          bordercolor=[('focus', WHITE)], arrowcolor=[('disabled', '#8d8d8d'), ('active', WHITE)])
    s.configure('TEntry', fieldbackground=PANEL, foreground=TEXT, insertcolor=TEXT, padding=(8, 6))
    s.map('TEntry', bordercolor=[('focus', WHITE)], fieldbackground=[('disabled', BG)])
    s.configure('Horizontal.TScale', background=WHITE, troughcolor=LINE, sliderlength=16,
                sliderthickness=13, borderwidth=0)
    s.map('Horizontal.TScale', background=[('active', '#bcbcbc')], bordercolor=[('focus', WHITE)])
    s.configure('TCheckbutton', indicatorcolor=PANEL, indicatorbackground=PANEL, padding=(0, 4))
    s.map('TCheckbutton', indicatorcolor=[('selected', WHITE)], foreground=[('disabled', '#8d8d8d')])
