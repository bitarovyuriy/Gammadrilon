# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ пересчёт цветов по готовым PNG (порядок каналов в Format_RGB32 — BGRA)."""
import os
import sys
import collections

os.environ["QT_QPA_PLATFORM"] = "offscreen"
HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import sysconfig as _sc

    _site = _sc.get_paths().get("purelib", "")
    _qt = os.path.join(_site, "PyQt5", "Qt5", "plugins")
    if _site and not _site.isascii() and os.path.isdir(_qt):
        os.environ.setdefault("QT_PLUGIN_PATH", _qt)
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", os.path.join(_qt, "platforms"))
except Exception:  # noqa: BLE001
    pass
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from PyQt5.QtGui import QImage  # noqa: E402

SHOTS = os.path.join(HERE, "build", "shots")
SKIP = ("diag", "report")

rows = []
for fn in sorted(os.listdir(SHOTS)):
    if not fn.endswith(".png") or fn.startswith(SKIP):
        continue
    img = QImage(os.path.join(SHOTS, fn))
    img = img.convertToFormat(QImage.Format_RGB32)
    data = img.constBits().asstring(img.byteCount())
    # в памяти little-endian BGRA: B=data[i], G=data[i+1], R=data[i+2]
    pixels = zip(data[2::4], data[1::4], data[0::4])
    counter = collections.Counter(pixels)
    coloured = collections.Counter(p for p in counter if max(p) - min(p) > 6)
    total = sum(counter[p] for p in coloured)
    uniq = len(counter)
    rows.append((fn[:-4], img.width(), img.height(), uniq, total,
                 [("#%02X%02X%02X" % c, counter[c]) for c in
                  [p for p, _ in coloured.most_common(4)]]))

print("=== ЦВЕТНОСТЬ СНИМКОВ (правильный порядок каналов) ===")
for name, w, h, uniq, total, top in rows:
    mark = "ЦВЕТ" if total else "моно "
    print(f"  {mark} {name}: цветных {total} px, уникальных {uniq}",
          top if top else "")
print("  всего снимков:", len(rows),
      "| с цветом:", sum(1 for r in rows if r[4]))
