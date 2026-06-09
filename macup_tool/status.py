from __future__ import annotations

import json
from datetime import datetime, timezone
import shlex
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import manager_state, paths
from .atomic import write_json_atomic
from .config import normalize_sources
from .timeutil import iso, local_display, parse_iso, relative_display, utc_now

GREEN = "#2da44e"
ORANGE = "#fb8c00"
RED = "#d1242f"


def default_status() -> dict[str, Any]:
    return {
        "state": "unconfigured",
        "last_result": "",
        "last_attempt_at": "",
        "last_success_at": "",
        "last_finished_at": "",
        "last_error": "",
        "active_run_id": "",
        "latest_log": "",
    }


def load_status() -> dict[str, Any]:
    path = paths.status_path()
    if not path.exists():
        return default_status()
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except json.JSONDecodeError:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        path.replace(path.with_name(f"{path.name}.corrupt-{stamp}"))
        return default_status()
    status = default_status()
    status.update(loaded)
    return status


def save_status(status: dict[str, Any]) -> dict[str, Any]:
    current = default_status()
    current.update(status)
    paths.ensure_base_dirs()
    write_json_atomic(paths.status_path(), current, mode=0o600)
    return current


def mark_running(run_id: str, log_path: Path) -> dict[str, Any]:
    status = load_status()
    status.update(
        {
            "state": "running",
            "last_result": "running",
            "last_attempt_at": iso(),
            "active_run_id": run_id,
            "latest_log": str(log_path),
            "last_error": "",
        }
    )
    return save_status(status)


def mark_success(run_id: str, log_path: Path) -> dict[str, Any]:
    status = load_status()
    now = iso()
    status.update(
        {
            "state": "success",
            "last_result": "success",
            "last_success_at": now,
            "last_finished_at": now,
            "active_run_id": "",
            "latest_log": str(log_path),
            "last_error": "",
        }
    )
    return save_status(status)


def mark_failed(run_id: str, log_path: Path, error: str) -> dict[str, Any]:
    status = load_status()
    status.update(
        {
            "state": "failed",
            "last_result": "failed",
            "last_finished_at": iso(),
            "active_run_id": "",
            "latest_log": str(log_path),
            "last_error": error[:1000],
        }
    )
    return save_status(status)


def next_due_at(config: dict[str, Any], status: dict[str, Any]) -> str:
    last_success = parse_iso(status.get("last_success_at"))
    if last_success is None:
        return iso(utc_now())
    hours = float(config.get("backup_interval_hours", 24))
    return iso(last_success + timedelta(hours=hours))


def is_stale(config: dict[str, Any], status: dict[str, Any], now=None) -> bool:
    current = now or utc_now()
    last_success = parse_iso(status.get("last_success_at"))
    if last_success is None:
        return True
    hours = float(config.get("backup_interval_hours", 24))
    return current - last_success > timedelta(hours=hours)


def is_due(config: dict[str, Any], status: dict[str, Any], now=None) -> bool:
    if status.get("last_result") == "failed":
        return True
    return is_stale(config, status, now=now)


def summarize(config: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    running = status.get("state") == "running" or bool(status.get("active_run_id"))
    stale = is_stale(config, status)
    failed = status.get("last_result") == "failed"
    setup_needed = not bool(config.get("initialized"))
    if running:
        color = ORANGE
        label = "Backing up"
    elif setup_needed:
        color = RED
        label = "Repository setup needed"
    elif failed or stale:
        color = RED
        label = "Backup attention needed"
    else:
        color = GREEN
        label = "Backup healthy"
    return {
        "color": color,
        "label": label,
        "running": running,
        "stale": stale,
        "failed": failed,
        "setup_needed": setup_needed,
        "last_backup": local_display(status.get("last_success_at")),
        "last_backup_relative": relative_display(status.get("last_success_at")),
        "next_backup": local_display(next_due_at(config, status)),
        "next_backup_relative": relative_display(next_due_at(config, status)),
        "latest_log": status.get("latest_log") or "",
        "source_count": len(normalize_sources(config.get("sources", []))),
        "last_error": status.get("last_error") or "",
        "last_result": status.get("last_result") or "none",
    }


def _xbar_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def xbar_output(config: dict[str, Any], status: dict[str, Any], cli: str | None = None) -> str:
    summary = summarize(config, status)
    cli_path = cli or str(paths.cli_path())
    log_path = summary["latest_log"]
    icon = "🟠" if summary["running"] else ("🔴" if summary["failed"] or summary["stale"] else "🟢")
    manager = manager_state.probe()
    manager_running = bool(manager.get("running"))
    menu_title = f"{icon} ▼" if manager_running else icon
    menu_color = ORANGE if manager_running else summary["color"]
    lines = [
        f"{menu_title} | color={menu_color}",
        "---",
        f"{summary['label']} | color={summary['color']}",
        f"Last: {summary['last_backup_relative']} │ Next: {summary['next_backup_relative']} | disabled=true",
    ]
    if summary["last_error"]:
        safe_error = summary["last_error"].replace("|", "/").replace("\n", " ")[:90]
        lines.append(f"Last error: {safe_error} | color={RED} disabled=true")
    if log_path:
        lines.append(
            f"Open Latest Log | shell=/usr/bin/open param1={_xbar_quote(log_path)} terminal=false"
        )
    lines.extend(
        [
            "---",
            (
                "Backup Now | "
                f"shell={_xbar_quote(cli_path)} param1=backup param2=--manual "
                "param3=--detach terminal=false refresh=true"
            ),
            (
                "Open Manager | "
                f"shell={_xbar_quote(cli_path)} param1=manager param2=--detach terminal=false refresh=true"
            ),
        ]
    )
    if manager_running:
        lines.append(
            "Close Manager Server | "
            f"shell={_xbar_quote(cli_path)} param1=manager param2=--stop "
            f"terminal=false refresh=true color={ORANGE}"
        )
    lines.append("Refresh | refresh=true")
    return "\n".join(lines) + "\n"


def text_output(config: dict[str, Any], status: dict[str, Any]) -> str:
    summary = summarize(config, status)
    lines = [
        f"Status: {summary['label']}",
        f"Last result: {summary['last_result']}",
        f"Last backup: {summary['last_backup']}",
        f"Next backup: {summary['next_backup']}",
        f"Sources: {summary['source_count']}",
    ]
    if summary["latest_log"]:
        lines.append(f"Latest log: {summary['latest_log']}")
    if summary["last_error"]:
        lines.append(f"Last error: {summary['last_error']}")
    return "\n".join(lines) + "\n"


def json_output(config: dict[str, Any], status: dict[str, Any]) -> str:
    payload = {"status": status, "summary": summarize(config, status)}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
