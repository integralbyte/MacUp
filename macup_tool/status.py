from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
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
        "last_warning": "",
        "backup_issues": [],
        "active_run_id": "",
        "latest_log": "",
        "progress_phase": "",
        "progress_message": "",
        "progress_percent": None,
        "progress_bytes_done": 0,
        "progress_total_bytes": 0,
        "progress_files_done": 0,
        "progress_total_files": 0,
        "progress_current": 0,
        "progress_total": 0,
        "progress_updated_at": "",
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
            "last_warning": "",
            "backup_issues": [],
            "progress_phase": "starting",
            "progress_message": "Starting backup",
            "progress_percent": 0,
            "progress_bytes_done": 0,
            "progress_total_bytes": 0,
            "progress_files_done": 0,
            "progress_total_files": 0,
            "progress_current": 0,
            "progress_total": 0,
            "progress_updated_at": iso(),
        }
    )
    return save_status(status)


def mark_backup_progress(
    run_id: str,
    log_path: Path,
    *,
    phase: str = "running",
    message: str = "",
    percent: float | None = None,
    bytes_done: int | None = None,
    total_bytes: int | None = None,
    files_done: int | None = None,
    total_files: int | None = None,
    current: int | None = None,
    total: int | None = None,
) -> dict[str, Any]:
    status = load_status()
    status.update(
        {
            "state": "running",
            "last_result": "running",
            "active_run_id": run_id,
            "latest_log": str(log_path),
            "progress_phase": phase,
            "progress_message": message,
            "progress_updated_at": iso(),
        }
    )
    if percent is not None:
        status["progress_percent"] = max(0, min(100, round(float(percent), 1)))
    if bytes_done is not None:
        status["progress_bytes_done"] = max(0, int(bytes_done))
    if total_bytes is not None:
        status["progress_total_bytes"] = max(0, int(total_bytes))
    if files_done is not None:
        status["progress_files_done"] = max(0, int(files_done))
    if total_files is not None:
        status["progress_total_files"] = max(0, int(total_files))
    if current is not None:
        status["progress_current"] = max(0, int(current))
    if total is not None:
        status["progress_total"] = max(0, int(total))
    return save_status(status)


def mark_success(
    run_id: str,
    log_path: Path,
    *,
    warning: str = "",
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
            "last_warning": warning[:1000],
            "backup_issues": list(issues or [])[:200],
            "progress_phase": "complete",
            "progress_message": "Backup complete" + (" with warnings" if warning else ""),
            "progress_percent": 100,
            "progress_updated_at": now,
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
            "last_warning": "",
            "progress_phase": "failed",
            "progress_message": "Backup failed",
            "progress_updated_at": iso(),
        }
    )
    return save_status(status)


def mark_stopped(run_id: str = "", log_path: Path | None = None, message: str = "Backup stopped by user.") -> dict[str, Any]:
    status = load_status()
    latest_log = str(log_path) if log_path else str(status.get("latest_log") or "")
    status.update(
        {
            "state": "failed",
            "last_result": "failed",
            "last_finished_at": iso(),
            "active_run_id": "",
            "latest_log": latest_log,
            "last_error": message[:1000],
            "last_warning": "",
            "progress_phase": "cancelled",
            "progress_message": "Backup stopped",
            "progress_updated_at": iso(),
        }
    )
    if run_id:
        status["active_run_id"] = ""
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
        "last_warning": status.get("last_warning") or "",
        "backup_issues": status.get("backup_issues") if isinstance(status.get("backup_issues"), list) else [],
        "last_result": status.get("last_result") or "none",
        "progress_phase": status.get("progress_phase") or "",
        "progress_message": status.get("progress_message") or "",
        "progress_percent": status.get("progress_percent"),
        "progress_bytes_done": int(status.get("progress_bytes_done") or 0),
        "progress_total_bytes": int(status.get("progress_total_bytes") or 0),
        "progress_files_done": int(status.get("progress_files_done") or 0),
        "progress_total_files": int(status.get("progress_total_files") or 0),
        "progress_current": int(status.get("progress_current") or 0),
        "progress_total": int(status.get("progress_total") or 0),
    }


def _xbar_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _compact_relative(value: str) -> str:
    text = str(value or "")
    replacements = (
        (r"\bmonths?\b", "mo"),
        (r"\bweeks?\b", "w"),
        (r"\bdays?\b", "d"),
        (r"\bhours?\b", "h"),
        (r"\bminutes?\b", "m"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"(\d+) (mo|w|d|h|m)", r"\1\2", text)
    return text


def _percent_label(value: Any) -> str:
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return ""
    if percent >= 99.95:
        return "100%"
    if percent == int(percent):
        return f"{int(percent)}%"
    return f"{percent:.1f}%"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _restore_lock_active() -> bool:
    lock = paths.restore_lock_path()
    if not lock.exists():
        return False
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0) or 0)
    except Exception:
        lock.unlink(missing_ok=True)
        return False
    if pid and _pid_alive(pid):
        return True
    lock.unlink(missing_ok=True)
    return False


def load_restore_status_for_xbar() -> dict[str, Any]:
    status = {
        "state": "idle",
        "snapshot": "",
        "target": "",
        "latest_log": "",
        "last_error": "",
        "progress_message": "",
        "progress_percent": None,
    }
    path = paths.restore_status_path()
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                status.update(loaded)
        except Exception:
            pass
    if status.get("state") == "running" and not _restore_lock_active():
        status["state"] = "failed"
        status["last_error"] = status.get("last_error") or "Restore process ended without updating status."
    return status


def _backup_progress_line(summary: dict[str, Any]) -> str | None:
    if not summary["running"]:
        return None
    percent = _percent_label(summary.get("progress_percent"))
    message = summary.get("progress_message") or "Working"
    suffix = ""
    current = int(summary.get("progress_current") or 0)
    total = int(summary.get("progress_total") or 0)
    if total > 1 and current:
        suffix = f" ({current}/{total})"
    if percent:
        return f"Backup: {percent}{suffix} | disabled=true"
    return f"Backup: {message}{suffix} | disabled=true"


def _restore_progress_line(restore: dict[str, Any]) -> str | None:
    if restore.get("state") != "running":
        return None
    percent = _percent_label(restore.get("progress_percent"))
    message = str(restore.get("progress_message") or "Restoring")
    if percent:
        return f"Restore: {percent} | disabled=true"
    return f"Restore: {message} | disabled=true"


def _warning_for_xbar(summary: dict[str, Any]) -> str:
    issues = summary.get("backup_issues")
    if isinstance(issues, list) and issues:
        count = len(issues)
        noun = "item" if count == 1 else "items"
        return f"{count} source {noun} were skipped." if count != 1 else "1 source item was skipped."
    warning = str(summary.get("last_warning") or "")
    count_match = re.search(r"\b(\d+)\s+source\s+items?\s+were\s+skipped\b", warning, re.IGNORECASE)
    if count_match:
        count = int(count_match.group(1))
        noun = "item" if count == 1 else "items"
        verb = "was" if count == 1 else "were"
        return f"{count} source {noun} {verb} skipped."
    if "was skipped" in warning or "were skipped" in warning:
        return "Source items were skipped."
    return "Backup completed with warnings."


def xbar_output(config: dict[str, Any], status: dict[str, Any], cli: str | None = None) -> str:
    summary = summarize(config, status)
    cli_path = cli or str(paths.cli_path())
    log_path = summary["latest_log"]
    restore = load_restore_status_for_xbar()
    icon = "●"
    manager = manager_state.probe()
    manager_running = bool(manager.get("running"))
    menu_title = f"{icon} ▾" if manager_running else icon
    menu_color = summary["color"]
    lines = [
        f"{menu_title} | color={menu_color}",
        "---",
        f"{summary['label']} | color={summary['color']}",
        f"Last {_compact_relative(summary['last_backup_relative'])} │ Next {_compact_relative(summary['next_backup_relative'])} | disabled=true",
    ]
    backup_line = _backup_progress_line(summary)
    restore_line = _restore_progress_line(restore)
    if backup_line or restore_line:
        lines.append("---")
    if backup_line:
        lines.append(backup_line)
    if restore_line:
        lines.append(restore_line)
    if summary["last_error"]:
        safe_error = summary["last_error"].replace("|", "/").replace("\n", " ")[:70]
        lines.append(f"Last error: {safe_error} | color={RED} disabled=true")
    if summary["last_warning"]:
        safe_warning = _warning_for_xbar(summary).replace("|", "/").replace("\n", " ")
        lines.append(f"Warning: {safe_warning} | color={ORANGE} disabled=true")
    if log_path:
        lines.append(
            f"Latest Log | shell=/usr/bin/open param1={_xbar_quote(log_path)} terminal=false"
        )
    restore_log = str(restore.get("latest_log") or "")
    if restore_log:
        lines.append(
            f"Restore Log | shell=/usr/bin/open param1={_xbar_quote(restore_log)} terminal=false"
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
    if summary["running"]:
        lines.append(
            "Stop Backup | "
            f"shell={_xbar_quote(cli_path)} param1=backup param2=--stop "
            f"terminal=false refresh=true color={RED}"
        )
    if manager_running:
        lines.append(
            "Close Manager | "
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
    if summary["last_warning"]:
        lines.append(f"Last warning: {summary['last_warning']}")
    return "\n".join(lines) + "\n"


def json_output(config: dict[str, Any], status: dict[str, Any]) -> str:
    payload = {"status": status, "summary": summarize(config, status)}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
