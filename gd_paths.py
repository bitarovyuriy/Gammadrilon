"""Lite state stays separate from the full application."""
import os
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'GammadrilonLite'
