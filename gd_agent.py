# -*- coding: utf-8 -*-
"""Локальный API для инструментов (ТЗ, этап 8) — шлюз действий, а не «ИИ-магия».

Приложение поднимает HTTP-сервер только на 127.0.0.1; порт выбирается свободный
при каждом запуске, фактический адрес пишется в ``agent_api.json``. Доступ — по
токену из ``agent_token.txt``, права на файл ограничиваются текущим пользователем
(icacls; если не вышло — честное предупреждение в UI, а не тишина).

Агенту доступны только именованные действия из белого списка ``ACTIONS``:

  * ``read``   — чтение: статус, сенсоры, журнал, правила, обход DPI;
  * ``mutate`` — изменение: каждый раз только после подтверждения пользователем;
  * destructive-действия («сбросить всё», очистка ОЗУ в цикле) не экспонируются.

Сырой реестр и произвольный shell агенту недоступны: у каждого действия объявлены
параметры с типами, диапазонами и перечнем значений, всё остальное отклоняется.

Каждый вызов — включая отказы и ошибки авторизации — дописывается в
``agent_audit.jsonl``. Журнал только дописывается: приложение его не редактирует.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import gd_i18n as i18n

PROTOCOL = 1
HOST = "127.0.0.1"

TOKEN_NAME = "agent_token.txt"
API_NAME = "agent_api.json"
AUDIT_NAME = "agent_audit.jsonl"
MAX_AUDIT = 5000          # хвост журнала аудита, который держим на диске
MAX_BODY = 64 * 1024      # больше тела запроса агенту и не нужно


def base_dir() -> str:
    """Папка данных Gammadrilon в профиле пользователя."""
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Gammadrilon")


def token_path() -> str:
    return os.path.join(base_dir(), TOKEN_NAME)


def api_path() -> str:
    return os.path.join(base_dir(), API_NAME)


def audit_path() -> str:
    return os.path.join(base_dir(), AUDIT_NAME)


# --- белый список действий ----------------------------------------------------


@dataclass(frozen=True)
class Param:
    """Параметр действия: тип, диапазон или перечень допустимых значений."""

    name: str
    kind: str                       # "int" | "str"
    lo: int = 0
    hi: int = 0
    choices: tuple = ()
    default: object = None


@dataclass(frozen=True)
class Action:
    """Действие API. ``kind``: read | mutate (destructive не экспонируется)."""

    name: str
    kind: str
    title: str                      # русская строка для UI (переводится через DIRECT)
    params: tuple = ()
    timeout: float = 20.0           # сколько ждать итог от приложения, с


ACTIONS: tuple[Action, ...] = (
    Action("status", "read", "Сводка: профили CPU/GPU, обход DPI, версия"),
    Action("sensors", "read", "Живые сенсоры CPU: частота, загрузка, ОЗУ"),
    Action("journal", "read", "Последние записи журнала изменений",
           (Param("n", "int", 1, 200, default=50),)),
    Action("rules", "read", "Правила автоматизации"),
    Action("zapret", "read", "Состояние обхода DPI (winws)"),
    Action("cpu_profile", "mutate", "Профиль питания CPU",
           (Param("profile", "str", choices=("eco", "normal", "boost")),), 60.0),
    Action("gpu_profile", "mutate", "Профиль питания GPU",
           (Param("profile", "str", choices=("quiet", "default", "game", "max")),), 60.0),
    Action("gpu_limit", "mutate", "Лимит мощности GPU, Вт",
           (Param("watts", "int", 50, 600),), 60.0),
    Action("dns_set", "mutate", "Смена DNS-сервера",
           (Param("provider", "str",
                  choices=("cloudflare", "google", "quad9", "adguard", "yandex")),), 90.0),
)


def find(name: str) -> Action | None:
    for a in ACTIONS:
        if a.name == name:
            return a
    return None


def action_timeout(name: str, fallback: float = 20.0) -> float:
    spec = find(name)
    return spec.timeout if spec else fallback


def spec_json(spec: Action) -> dict:
    """Описание действия для GET /v1/actions (JSON-безопасное)."""
    return {
        "name": spec.name,
        "kind": spec.kind,
        "title": i18n.t(spec.title),
        "params": [
            {
                "name": p.name,
                "type": p.kind,
                "range": [p.lo, p.hi] if p.kind == "int" else None,
                "choices": list(p.choices) or None,
                "default": p.default,
            }
            for p in spec.params
        ],
    }


def validate(name: str, params: dict | None) -> tuple[dict | None, str | None]:
    """Проверить имя и параметры по белым спискам. → (очищенные, ошибка)."""
    spec = find(name)
    if spec is None:
        return None, i18n.t("неизвестное действие: {v0}", v0=name)
    given = dict(params or {})
    clean: dict = {}
    for p in spec.params:
        if p.name not in given:
            if p.default is not None:
                clean[p.name] = p.default
                continue
            return None, i18n.t("нет параметра «{v0}»", v0=p.name)
        v = given[p.name]
        if p.kind == "int":
            if isinstance(v, str) and v.strip().lstrip("-").isdigit():
                v = int(v.strip())        # query-строка приходит текстом: «?n=5»
            if isinstance(v, bool) or not isinstance(v, int):
                return None, i18n.t("«{v0}» — целое число", v0=p.name)
            if not (p.lo <= v <= p.hi):
                return None, i18n.t("«{v0}» вне диапазона {v1}..{v2}", v0=p.name, v1=p.lo, v2=p.hi)
        else:
            if not isinstance(v, str):
                return None, i18n.t("«{v0}» — строка", v0=p.name)
            if p.choices and v not in p.choices:
                return None, i18n.t("«{v0}»: допустимо {v1}", v0=p.name, v1=", ".join(p.choices))
        clean[p.name] = v
    extra = sorted(set(given) - {p.name for p in spec.params})
    if extra:
        return None, i18n.t("лишние параметры: {v0}", v0=", ".join(extra))
    return clean, None


def describe(spec: Action, params: dict) -> str:
    """Одна строка для диалога подтверждения: «gpu_limit · watts=200»."""
    if not params:
        return spec.name
    return f"{spec.name} · " + ", ".join(f"{k}={v}" for k, v in params.items())


def finish(done, ok: bool, error=None, note: str = "", data=None) -> None:
    """Позвать хук внешнего вызова ровно один раз (страницы UI отдают им итог).

    Хук может отсутствовать — тогда это обычная работа из вкладки, no-op.
    """
    if done is None:
        return
    try:
        done({"ok": bool(ok), "error": error, "note": note, "data": data})
    except Exception:  # noqa: BLE001
        pass


# --- токен и права ------------------------------------------------------------


def _restrict(path: str) -> str:
    """Ограничить права на файл текущим пользователем (icacls). → "" или текст беды.

    ТЗ: «токен в файле с правами только для пользователя». Если icacls отказал —
    возвращаем честное предупреждение для UI, а не делаем вид, что закрыто.
    """
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    who = f"{domain}\\{user}" if domain and user else user
    if not user:
        return i18n.t("не удалось определить пользователя — права на токен не ограничены")
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", f"{who}:M"],
            capture_output=True, timeout=15, creationflags=flags,
        )
    except Exception as exc:  # noqa: BLE001
        return i18n.t("не удалось ограничить права файла токена: {v0}", v0=exc)
    if proc.returncode != 0:
        out = (proc.stdout or b"") + (proc.stderr or b"")
        lines = out.decode("cp866", "replace").strip().splitlines()
        return i18n.t("icacls отказал: {v0}", v0=(lines[0] if lines else proc.returncode))
    return ""


def _ensure_token() -> tuple[str | None, str]:
    """Прочитать токен из файла или создать новый. → (токен, предупреждение)."""
    path = token_path()
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                tok = fh.read().strip()
            if len(tok) >= 32 and all(c in "0123456789abcdef" for c in tok):
                return tok, ""
        tok = secrets.token_hex(32)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(tok)
    except OSError as exc:
        return None, i18n.t("не удалось записать файл токена: {v0}", v0=exc)
    return tok, _restrict(path)


# --- аудит --------------------------------------------------------------------


def audit(action: str, decision: str, params: dict | None = None, detail: str = "",
          remote: str = "", dur_ms: int | None = None, kind: str = "") -> dict:
    """Дописать запись в журнал аудита: кто/что/когда/какие аргументы.

    ``decision``: start | stop | ok | denied | rejected | error.
    """
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "kind": kind,
        "decision": decision,
        "params": params or {},
        "detail": detail,
        "remote": remote,
        "dur_ms": dur_ms,
    }
    try:
        path = audit_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim_audit(path)
    except OSError:
        pass
    return entry


def _trim_audit(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) > MAX_AUDIT:
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines[-MAX_AUDIT:])
    except OSError:
        pass


def audit_tail(n: int = 120) -> list[dict]:
    """Последние записи аудита (свежие сверху). Битые строки пропускаем."""
    path = audit_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out


# --- сервер -------------------------------------------------------------------


def _write_api_file(port: int, version: str) -> None:
    """Фактический адрес сервера — в конфиг (ТЗ: порт не фиксированный)."""
    data = {
        "protocol": PROTOCOL,
        "host": HOST,
        "port": port,
        "pid": os.getpid(),
        "started": datetime.now().isoformat(timespec="seconds"),
        "version": version,
        "token_file": token_path(),
        "audit_file": audit_path(),
    }
    try:
        os.makedirs(base_dir(), exist_ok=True)
        with open(api_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def read_api_file() -> dict | None:
    """Прочитать agent_api.json (для UI и внешних инструментов)."""
    try:
        with open(api_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class _Handler(BaseHTTPRequestHandler):
    """Обработчик: токен, белый список, аудит. Никаких сырых команд."""

    protocol_version = "HTTP/1.1"
    server_version = "GammadrilonAgent/1"

    def log_message(self, fmt, *args):  # noqa: A003 — молчание в stderr
        pass

    # -- утилиты -----------------------------------------------------------
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _remote(self) -> str:
        try:
            return str(self.client_address[0])
        except Exception:  # noqa: BLE001
            return ""

    def _authed(self) -> bool:
        head = self.headers.get("Authorization", "")
        got = head[7:].strip() if head.lower().startswith("bearer ") else ""
        token = getattr(self.server, "token", "")
        return bool(token) and bool(got) and hmac.compare_digest(got, token)

    def _route(self) -> str:
        return urlparse(self.path).path.rstrip("/") or "/"

    def _deny_auth(self) -> None:
        audit("api", "denied", detail=i18n.t("неверный или отсутствующий токен"),
              remote=self._remote(), kind="auth")
        self._json(401, {"ok": False,
                         "error": i18n.t("нужен токен: Authorization: Bearer <токен из файла>")})

    # -- маршруты ----------------------------------------------------------
    def do_GET(self):
        if not self._authed():
            self._deny_auth()
            return
        route = self._route()
        if route in ("/v1", "/v1/ping"):
            self._json(200, {"ok": True, "data": {
                "name": "Gammadrilon",
                "version": getattr(self.server, "version", ""),
                "protocol": PROTOCOL,
                "endpoint": f"http://{HOST}:{self.server.server_address[1]}",
            }})
            return
        if route == "/v1/actions":
            self._json(200, {"ok": True, "data": [spec_json(a) for a in ACTIONS]})
            return
        if route.startswith("/v1/read/"):
            name = route[len("/v1/read/"):]
            params = {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items()}
            self._call(name, params, force_read=True)
            return
        self._json(404, {"ok": False, "error": i18n.t("неизвестный маршрут: {v0}", v0=route)})


    def do_POST(self):
        if not self._authed():
            self._deny_auth()
            return
        route = self._route()
        if not route.startswith("/v1/action/"):
            self._json(404, {"ok": False, "error": i18n.t("неизвестный маршрут: {v0}", v0=route)})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            # тело не обрабатываем, но сначала сливаем его (в разумном пределе),
            # иначе клиент не успевает прочитать отказ: получает сброс соединения
            self.close_connection = True
            if length <= MAX_BODY * 16:
                try:
                    self.rfile.read(length)
                except OSError:
                    pass
            self._json(413, {"ok": False, "error": i18n.t("тело запроса слишком большое")})
            return
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "error": i18n.t("тело запроса — не JSON")})
            return
        if not isinstance(body, dict):
            self._json(400, {"ok": False, "error": i18n.t("тело запроса — объект JSON")})
            return
        params = body.get("params") if isinstance(body.get("params"), dict) else \
            {k: v for k, v in body.items() if k != "params"}
        self._call(route[len("/v1/action/"):], params, force_read=False)

    # -- исполнение --------------------------------------------------------
    def _call(self, name: str, params: dict, force_read: bool) -> None:
        spec = find(name)
        if spec is None:
            audit(name, "rejected", params, i18n.t("неизвестное действие"),
                  self._remote(), kind="")
            self._json(404, {"ok": False, "error": i18n.t("неизвестное действие: {v0}", v0=name)})
            return
        if force_read and spec.kind != "read":
            self._json(400, {"ok": False, "error": i18n.t(
                "«{v0}» — изменяющее действие: вызывайте POST /v1/action/{v0}", v0=name)})
            return
        clean, err = validate(name, params)
        if err is not None:
            audit(name, "rejected", params, err, self._remote(), kind=spec.kind)
            self._json(400, {"ok": False, "error": err})
            return
        t0 = time.perf_counter()
        try:
            result = self.server.runner(name, clean, spec.kind == "mutate")
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        dur = int((time.perf_counter() - t0) * 1000)
        if not isinstance(result, dict):
            result = {"ok": False, "error": i18n.t("исполнитель вернул не объект")}
        ok = bool(result.get("ok"))
        decision = "ok" if ok else ("denied" if spec.kind == "mutate" else "error")
        audit(name, decision, clean, str(result.get("error") or result.get("note") or ""),
              self._remote(), dur, spec.kind)
        code = 200 if ok else (403 if decision == "denied" else 500)
        payload = {"ok": ok, "data": result.get("data"),
                   "error": result.get("error"), "note": result.get("note")}
        try:
            json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            # результат с несериализуемым полем не должен рвать соединение
            payload = {"ok": False, "error": i18n.t("результат не удалось сериализовать в JSON")}
            code = 500
            audit(name, "error", clean, payload["error"], self._remote(), dur, spec.kind)
        self._json(code, payload)


class AgentServer:
    """HTTP-шлюз на 127.0.0.1.

    ``runner(action, params, mutate)`` даёт приложение: для read-действий он
    вызывается сразу, для mutate — только после подтверждения пользователем.
    Сервер не знает, *как* выполняются действия, и не умеет ничего сверх списка.
    """

    def __init__(self, runner, version: str = "") -> None:
        self._runner = runner
        self._version = version
        self.port = 0
        self.warn = ""
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- состояние ---------------------------------------------------------
    def running(self) -> bool:
        return self._httpd is not None

    def endpoint(self) -> str:
        return f"http://{HOST}:{self.port}" if self.port else ""

    def token(self) -> str:
        try:
            with open(token_path(), encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    # -- запуск/останов ----------------------------------------------------
    def start(self) -> tuple[bool, str]:
        """Поднять сервер. → (ок, ошибка). Порт выбирает ОС, фактический — в конфиг."""
        if self._httpd is not None:
            return True, ""
        tok, warn = _ensure_token()
        if not tok:
            return False, warn
        self.warn = warn
        try:
            httpd = ThreadingHTTPServer((HOST, 0), _Handler)
        except OSError as exc:
            return False, i18n.t("не удалось открыть порт: {v0}", v0=exc)
        httpd.daemon_threads = True
        httpd.token = tok
        httpd.runner = self._runner
        httpd.version = self._version
        self.port = int(httpd.server_address[1])
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="gd-agent-api", daemon=True)
        self._thread.start()
        _write_api_file(self.port, self._version)
        audit("api", "start", detail=i18n.t("сервер поднят на {v0}", v0=self.endpoint()))
        return True, ""

    def stop(self) -> None:
        """Остановить сервер и убрать адрес из конфига (выключен — значит выключен)."""
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            try:
                self._thread.join(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            self._thread = None
        try:
            os.remove(api_path())
        except OSError:
            pass
        if self.port:
            audit("api", "stop", detail=i18n.t("сервер остановлен"))
        self.port = 0



