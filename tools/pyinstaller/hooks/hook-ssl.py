import ssl
from pathlib import Path

# Codex Python загружает этот список недоверенных CA рядом с ssl.py.
# У стандартного CPython файла нет.
policy_file = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
datas = [(str(policy_file), ".")] if policy_file.is_file() else []
