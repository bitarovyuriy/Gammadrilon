# -*- coding: utf-8 -*-
"""Архив для поддержки: диагностика, журналы и версия одним zip.

Зачем: человек на другом ПК не будет пересказывать «что у меня не так» и
пересылать пять файлов по одному. Один архив — и видно версию, проверки,
журнал изменений и хвост логов.

Внутри нет ничего личного сверх нужного: отчёт обезличивается тем же
`gd_diagnose.anonymize`, а логи обрезаются до последних 200 КБ — полный
файл за месяц только мешает.
"""
from __future__ import annotations

import json
import platform
import time
import zipfile
from pathlib import Path

import gd_diagnose
import gd_paths

#: сколько байт хвоста брать из каждого журнала
TAIL_BYTES = 200 * 1024


def _version() -> str:
    try:
        import main

        return str(getattr(main, "APP_VERSION", ""))
    except Exception:  # noqa: BLE001 — версия не критична для архива
        return ""


def _tail(path: Path, limit: int = TAIL_BYTES) -> bytes:
    try:
        size = path.stat().st_size
        with path.open("rb") as file:
            if size > limit:
                file.seek(size - limit)
            return file.read()
    except OSError:
        return b""


def build(destination: Path | None = None, items: list[dict] | None = None) -> tuple[bool, str]:
    """Собрать архив. Возвращает (ok, путь или причина отказа)."""
    try:
        checks = items if items is not None else gd_diagnose.checks()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = destination or (gd_paths.log_dir() / f"gammadrilon-support-{stamp}.zip")
        gd_paths.ensure(Path(target).parent)

        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("diagnose.txt", gd_diagnose.report_text(checks, ascii_only=True))
            archive.writestr("diagnose.json", json.dumps(
                gd_diagnose.report_json(checks), ensure_ascii=False, indent=2))
            archive.writestr("info.txt", "\n".join([
                f"Gammadrilon {_version()}",
                platform.platform(),
                platform.machine(),
                f"собрано: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"портативно: {gd_paths.portable_enabled()}",
                f"данные: {gd_paths.data_dir()}",
                f"логи: {gd_paths.log_dir()}",
            ]))

            for name in ("gammadrilon.log", "gpu_service.log", "zapret_winws.log"):
                path = gd_paths.log_dir() / name
                if path.is_file():
                    data = _tail(path)
                    if data:
                        archive.writestr(name, data)

            for name in ("journal.jsonl", "rules_log.jsonl"):
                path = gd_paths.data_dir() / name
                if path.is_file():
                    data = _tail(path)
                    if data:
                        archive.writestr(name, data)

        return True, str(target)
    except Exception as exc:  # noqa: BLE001 — архив не должен ронять окно
        return False, f"{type(exc).__name__}: {exc}"
