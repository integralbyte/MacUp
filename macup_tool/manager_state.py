from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

from . import paths
from .atomic import write_text_atomic
from .timeutil import iso


def read_state() -> dict[str, Any] | None:
    path = paths.manager_state_path()
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    return state


def write_running(port: int, token: str, url: str) -> dict[str, Any]:
    state = {
        "pid": os.getpid(),
        "port": int(port),
        "token": token,
        "url": url,
        "started_at": iso(),
    }
    content = json.dumps(state, indent=2, sort_keys=True) + "\n"
    paths.ensure_base_dirs()
    write_text_atomic(paths.manager_state_path(), content, mode=0o600)
    return state


def clear(token: str | None = None) -> None:
    path = paths.manager_state_path()
    if token:
        state = read_state()
        if state and state.get("token") != token:
            return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _api_url(state: dict[str, Any], api_path: str) -> str:
    port = int(state.get("port") or 0)
    token = str(state.get("token") or "")
    if port <= 0 or not token:
        return ""
    query = urllib.parse.urlencode({"token": token})
    return f"http://127.0.0.1:{port}{api_path}?{query}"


def _request(state: dict[str, Any], api_path: str, method: str = "GET", timeout: float = 0.4) -> tuple[bool, dict[str, Any], str]:
    url = _api_url(state, api_path)
    token = str(state.get("token") or "")
    if not url:
        return False, {}, "Manager state is incomplete."
    data = b"{}" if method == "POST" else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-MacUp-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return False, {}, str(exc)
    if not isinstance(payload, dict):
        return False, {}, "Manager returned an invalid response."
    return True, payload, ""


def probe(timeout: float = 0.4) -> dict[str, Any]:
    state = read_state()
    if not state:
        return {"running": False}
    ok, payload, error = _request(state, "/api/status", timeout=timeout)
    if ok and payload.get("ok"):
        visible = dict(state)
        visible.pop("token", None)
        visible["running"] = True
        return visible
    clear()
    return {"running": False, "stale": True, "error": error}


def stop(timeout: float = 2.0) -> tuple[bool, str]:
    state = read_state()
    if not state:
        return True, "Manager server is not running."
    ok, payload, error = _request(state, "/api/shutdown", method="POST", timeout=timeout)
    clear(str(state.get("token") or ""))
    if ok and payload.get("ok"):
        return True, "Manager server stopped."
    return True, f"Manager server was not reachable; cleared stale state. {error}".strip()
