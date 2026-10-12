# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ прибор §7: масштаб 125/150 % и порядок обхода Tab.

Запуск:  .venv\\Scripts\\python.exe _tmp_ui7.py

Родитель поднимает себя же тремя детьми с QT_SCALE_FACTOR = 1.0 / 1.25 / 1.5
(в одном процессе масштаб не поменять), каждый ребёнок строит окно offscreen,
снимает снимок в build/shots/ui-<N>.png и отдаёт JSON с геометрией и цепочкой
фокуса. Родитель читает JSON-ы, печатает вердикт и пишет build/shots/ui7.log.
Ничего не применяет, компаньонов не поднимает.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "build", "shots")
os.makedirs(OUT, exist_ok=True)

SCALES = (1.0, 1.25, 1.5)


def _child(scale: float) -> int:
    """Ребёнок: окно на заданном масштабе → JSON."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = f"{scale}"
    sys.path.insert(0, HERE)
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFontDatabase
    from PyQt5.QtWidgets import QApplication, QLabel, QWidget

    import main as gd_main

    app = QApplication([])
    for _f in ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf"):
        _p = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", _f)
        if os.path.isfile(_p):
            QFontDatabase.addApplicationFont(_p)
    app.setStyleSheet(gd_main.build_app_stylesheet())

    win = gd_main.MainWindow()
    win.resize(win.compact_size)
    win.show()
    for _ in range(30):
        app.processEvents()

    def desc(w) -> str:
        text = ""
        if hasattr(w, "text"):
            try:
                text = str(w.text())[:18]
            except Exception:  # noqa: BLE001
                text = ""
        return f"{w.metaObject().className()}({text})" if text else w.metaObject().className()

    # цепочка фокуса: от окна по nextInFocusChain — как её пройдёт Tab.
    # `keep` держит обёртки PyQt живыми: иначе id() переиспользуется и цепочка
    # обрывается на первом же освобождённом объекте (ложный «уже видели»).
    ring, seen, keep = [], set(), []
    w = win
    for _ in range(4000):
        w = w.nextInFocusChain()
        if w is win or w is None:
            break
        if id(w) in seen:
            break
        seen.add(id(w))
        keep.append(w)
        ring.append(w)

    def stop(x) -> bool:
        return (x.focusPolicy() != Qt.NoFocus and x.isEnabled()
                and x.isVisibleTo(win) and x.isVisible())

    chain = [desc(x) for x in ring if stop(x)]

    # ожидаемый порядок обхода: сначала меню (сайдбар), потом содержимое страницы,
    # в самом конце — кнопки шапки и управления окном
    head_ok = (len(chain) >= 8 and all(c.startswith("NavItem")
                                       for c in chain[:len(win._nav_items)]))
    tail_ok = (chain[-1] == "QPushButton(✕)" and chain[-7].startswith("QPushButton(Режим"))
    order_ok = head_ok and tail_ok

    bad_geom, clipped_other = [], 0
    for w in win.findChildren(QWidget):
        if w.isVisible() and (w.width() < 0 or w.height() < 0):
            bad_geom.append(desc(w))
    for lb in win.findChildren(QLabel):
        if lb.isVisible() and lb.text() and not lb.wordWrap() \
                and lb.width() < lb.sizeHint().width():
            clipped_other += 1

    bar = win.tabs
    data = {
        "scale": scale,
        "dpr": round(win.devicePixelRatioF(), 3),
        "window": [win.width(), win.height()],
        "compact": [win.compact_size.width(), win.compact_size.height()],
        "neg_geom": bad_geom,
        "tabs": bar.count(),
        "nav_total": len(win._nav_items),
        "nav_focusable": sum(1 for it in win._nav_items.values()
                             if it.focusPolicy() != Qt.NoFocus),
        "statusbar": [win._sb_cpu.text(), win._sb_gpu.text(), win._sb_ram.text(), win._sb_dns.text()],
        "status_clipped": [win._sb_cpu.width() < win._sb_cpu.sizeHint().width(),
                           win._sb_gpu.width() < win._sb_gpu.sizeHint().width(),
                           win._sb_ram.width() < win._sb_ram.sizeHint().width(),
                           win._sb_dns.width() < win._sb_dns.sizeHint().width()],
        "foot_clipped": win._foot_label.width() < win._foot_label.sizeHint().width(),
        "clipped_other": clipped_other,
        "focus_chain": chain,
        "focus_head": chain[:6],
        "focus_tail": chain[-7:],
        "order_ok": order_ok,
        "shot": None,
    }
    shot = os.path.join(OUT, f"ui-{int(scale * 100)}.png")
    try:
        win.grab().save(shot)
        data["shot"] = os.path.basename(shot)
    except Exception as exc:  # noqa: BLE001
        data["shot_error"] = str(exc)
    try:
        win.tray.hide()
        import gd_metrics

        gd_metrics.stop()
        import gd_bg

        gd_bg.wait_all(800)
    except Exception:  # noqa: BLE001
        pass
    with open(os.path.join(OUT, f"ui7-{int(scale * 100)}.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return 0



def _parent() -> int:
    log = []

    def line(text: str) -> None:
        print(text, flush=True)
        log.append(text)

    results = []
    for scale in SCALES:
        env = dict(os.environ)
        env["GD_UI7_SCALE"] = f"{scale}"
        env["PYTHONIOENCODING"] = "utf-8"
        subprocess.run([sys.executable, os.path.abspath(__file__)], env=env, check=False,
                       capture_output=True)
        path = os.path.join(OUT, f"ui7-{int(scale * 100)}.json")
        if not os.path.exists(path):
            line(f"ПРОВАЛ  масштаб {scale:.2f}: ребёнок не отдал отчёт")
            continue
        with open(path, encoding="utf-8") as fh:
            results.append(json.load(fh))

    fails = []
    for r in results:
        tag = f"{int(r['scale'] * 100)}%"
        w0, h0 = r["compact"]
        ok_win = r["window"][0] >= w0 and r["window"][1] >= h0
        line(f"ок      {tag}: окно {r['window'][0]}×{r['window'][1]} (компакт {w0}×{h0}), "
             f"DPR {r['dpr']}" + ("" if ok_win else "  ← меньше компактного"))
        if not ok_win:
            fails.append(f"{tag}: окно меньше компактного")
        if r["neg_geom"]:
            fails.append(f"{tag}: отрицательная геометрия у {r['neg_geom']}")
        line(f"ок      {tag}: виджетов с отрицательной геометрией нет, "
             f"страниц {r['tabs']}, пунктов меню {r['nav_total']} "
             f"(достижимо с клавиатуры {r['nav_focusable']})")
        if r["nav_focusable"] != r["nav_total"]:
            fails.append(f"{tag}: сайдбар недостижим с клавиатуры "
                         f"({r['nav_focusable']} из {r['nav_total']})")
        clipped = [n for n, c in zip(("CPU", "GPU", "RAM", "DNS"), r["status_clipped"]) if c]
        if clipped or r["foot_clipped"]:
            fails.append(f"{tag}: обрезаны плашки {clipped}, фут={r['foot_clipped']}")
        line(f"ок      {tag}: статусбар и фут не обрезаны — {' · '.join(r['statusbar'])}")
        chain = r["focus_chain"]
        head, tail = r["focus_head"], r["focus_tail"]
        if not r["order_ok"]:
            fails.append(f"{tag}: порядок обхода Tab — {head} … {tail}")
        line(f"ок      {tag}: обход Tab — {len(chain)} остановок")
        line(f"        начало: {', '.join(head)}")
        line(f"        конец:  {', '.join(tail)}")
        line(f"        снимок: {r.get('shot')}  (прочих обрезанных подписей: {r['clipped_other']})")

    line("")
    line("ВСЁ ОК" if not fails else "ПРОВАЛЫ: " + "; ".join(fails))
    with open(os.path.join(OUT, "ui7.log"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")
    print(f"LOG: {os.path.join(OUT, 'ui7.log')}", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    scale_env = os.environ.get("GD_UI7_SCALE")
    sys.exit(_child(float(scale_env)) if scale_env else _parent())
