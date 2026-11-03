# -*- coding: utf-8 -*-
"""Калибровка теней и середины по шагам.

Задача: подобрать кривую не «на глаз одним слайдером», а по ступеням — от
почти чёрного до светло-серого. На каждом шаге человек видит плитку и фон
рядом: если плитка сливается с фоном, тени провалены; если фон светлее
плитки — пересвет.

Диалог ничего не сохраняет сам: он отдаёт итоговые `gamma` и `shadow`
через `on_apply`, а решение «делать из этого пресет» принимает окно.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                             QSlider, QVBoxLayout, QWidget)

import gd_i18n as i18n
import gd_theme as th

#: уровни плиток (0..255): тёмные важнее — там и прячется «серая пелена»
STEPS = (16, 32, 64, 128, 200)


class StepStrip(QWidget):
    """Пять плиток на фоне-подложке: видно, где кривая проваливает тени."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.shadow = 0.0
        self.gamma = 1.0

    def set_curve(self, gamma: float, shadow: float) -> None:
        self.gamma = float(gamma)
        self.shadow = float(shadow)
        self.update()

    def _level(self, value: int) -> int:
        """Тот же порядок, что в GammaController: гамма, затем подъём теней."""
        x = value / 255.0
        if x > 0:
            x = x ** (1.0 / max(0.1, self.gamma))
        if 0.0 <= x < 1.0:
            x = x + self.shadow * (1.0 - x) ** 3
        return int(max(0.0, min(1.0, x)) * 255)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101010"))

        width = self.width() / len(STEPS)
        for index, value in enumerate(STEPS):
            level = self._level(value)
            rect = self.rect().adjusted(int(index * width) + 8, 16,
                                        -int((len(STEPS) - index - 1) * width) - 8, -16)
            painter.fillRect(rect, QColor(level, level, level))
            # подпись уровня — чтобы человек видел, что именно проверяет
            painter.setPen(QColor(th.MUTED if hasattr(th, "MUTED") else "#808080"))
            painter.drawText(rect.adjusted(0, 0, 0, 0), Qt.AlignBottom | Qt.AlignHCenter,
                             str(value))


class CalibrationDialog(QDialog):
    """Ползунок гаммы и тени с живым предпросмотром на пяти плитках."""

    def __init__(self, on_apply=None, parent=None, gamma: float = 1.0,
                 shadow: float = 0.0) -> None:
        super().__init__(parent)
        self.on_apply = on_apply
        self.setWindowTitle(i18n.t("Калибровка теней"))
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.DIALOG_MARGINS)
        root.setSpacing(th.DIALOG_SPACING)

        title = QLabel(i18n.t("Поднимите тени так, чтобы плитки различались"))
        title.setObjectName("DialogTitle")
        root.addWidget(title)

        hint = QLabel(i18n.t(
            "Слева почти чёрное, справа светло-серое. Если тёмные плитки "
            "сливаются с фоном — добавьте тени; если картинка стала серой — уберите."))
        hint.setWordWrap(True)
        hint.setObjectName("Hint")
        root.addWidget(hint)

        self.strip = StepStrip()
        self.strip.set_curve(gamma, shadow)
        root.addWidget(self.strip)

        self.gamma_slider = self._row(root, i18n.t("Гамма"), 50, 200, int(gamma * 100))
        self.shadow_slider = self._row(root, i18n.t("Тени"), 0, 100,
                                       int(round(shadow / 0.25 * 100)) if shadow else 0)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton(i18n.t("Отмена"))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = QPushButton(i18n.t("Применить"))
        ok.setObjectName("PrimaryButton")
        ok.clicked.connect(self._accept)
        row.addWidget(ok)
        root.addLayout(row)

    def _row(self, root, title: str, low: int, high: int, value: int) -> QSlider:
        holder = QHBoxLayout()
        label = QLabel(title)
        label.setMinimumWidth(90)
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(low)
        slider.setMaximum(high)
        slider.setValue(max(low, min(high, value)))
        slider.valueChanged.connect(self._refresh)
        holder.addWidget(label)
        holder.addWidget(slider, 1)
        root.addLayout(holder)
        return slider

    def gamma(self) -> float:
        return self.gamma_slider.value() / 100.0

    def shadow(self) -> float:
        return self.shadow_slider.value() / 100.0 * 0.25

    def _refresh(self) -> None:
        self.strip.set_curve(self.gamma(), self.shadow())

    def _accept(self) -> None:
        if self.on_apply is not None:
            try:
                self.on_apply(self.gamma(), self.shadow())
            except Exception:  # noqa: BLE001 — отказ применения не должен запирать диалог
                pass
        self.accept()


class WhitePatch(QWidget):
    """Серая плитка: на ней видно, уходит ли белый в тепло или в холод."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(140)
        self.temperature = 6500

    def set_temperature(self, value: int) -> None:
        self.temperature = int(value)
        self.update()

    def _tint(self) -> QColor:
        """Тот же сдвиг каналов, что делает кривая: 6500 К — нейтраль."""
        import main as gd_main

        red, green, blue = gd_main.kelvin_to_rgb_multipliers(self.temperature)
        peak = max(red, green, blue) or 1.0
        return QColor(
            int(255 * min(1.0, red / peak)),
            int(255 * min(1.0, green / peak)),
            int(255 * min(1.0, blue / peak)),
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._tint())
        painter.setPen(QColor("#202020"))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class WhitePointDialog(QDialog):
    """Подбор температуры: крутим, пока серая плитка не станет нейтральной."""

    def __init__(self, on_apply=None, parent=None, temperature: int = 6500) -> None:
        super().__init__(parent)
        self.on_apply = on_apply
        self.setWindowTitle(i18n.t("Белая точка"))
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(*th.DIALOG_MARGINS)
        root.setSpacing(th.DIALOG_SPACING)

        title = QLabel(i18n.t("Сделайте плитку нейтрально-серой"))
        title.setObjectName("DialogTitle")
        root.addWidget(title)

        hint = QLabel(i18n.t(
            "Смотрите на плитку, а не на белый лист бумаги рядом: если она "
            "кажется тёплой — увеличьте температуру, если холодной — уменьшите."))
        hint.setWordWrap(True)
        hint.setObjectName("Hint")
        root.addWidget(hint)

        self.patch = WhitePatch()
        self.patch.set_temperature(temperature)
        root.addWidget(self.patch)

        row = QHBoxLayout()
        label = QLabel(i18n.t("Температура"))
        label.setMinimumWidth(90)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(3000)
        self.slider.setMaximum(10000)
        self.slider.setValue(int(max(3000, min(10000, temperature))))
        self.slider.valueChanged.connect(self._refresh)
        self.value = QLabel(f"{self.slider.value()} K")
        self.value.setMinimumWidth(70)
        row.addWidget(label)
        row.addWidget(self.slider, 1)
        row.addWidget(self.value)
        root.addLayout(row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(i18n.t("Отмена"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton(i18n.t("Применить"))
        ok.setObjectName("PrimaryButton")
        ok.clicked.connect(self._accept)
        buttons.addWidget(ok)
        root.addLayout(buttons)

    def temperature(self) -> int:
        return int(self.slider.value())

    def _refresh(self) -> None:
        self.patch.set_temperature(self.slider.value())
        self.value.setText(f"{self.slider.value()} K")

    def _accept(self) -> None:
        if self.on_apply is not None:
            try:
                self.on_apply(self.temperature())
            except Exception:  # noqa: BLE001 — отказ применения не должен запирать диалог
                pass
        self.accept()


def run_white_point(on_apply=None, parent=None, temperature: int = 6500) -> bool:
    """Показать подбор белой точки. True — человек нажал «Применить»."""
    dialog = WhitePointDialog(on_apply=on_apply, parent=parent, temperature=temperature)
    return dialog.exec_() == QDialog.Accepted


def run(on_apply=None, parent=None, gamma: float = 1.0, shadow: float = 0.0) -> bool:
    """Показать калибровку. True — человек нажал «Применить»."""
    dialog = CalibrationDialog(on_apply=on_apply, parent=parent,
                               gamma=gamma, shadow=shadow)
    return dialog.exec_() == QDialog.Accepted
