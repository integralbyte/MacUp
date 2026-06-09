from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import keychain, paths
from .atomic import write_json_atomic
from .backup import process_alive, restic_base_args, restic_env
from .config import MACUP_TAG
from .logutil import RunLogger
from .process import run_streamed
from .timeutil import iso


def default_restore_status() -> dict[str, Any]:
    return {
        "state": "idle",
        "active_pid": 0,
        "snapshot": "",
        "parent": "",
        "target": "",
        "latest_log": "",
        "started_at": "",
        "finished_at": "",
        "last_error": "",
        "progress_phase": "",
        "progress_message": "",
        "progress_percent": None,
        "progress_bytes_done": 0,
        "progress_total_bytes": 0,
        "progress_files_done": 0,
        "progress_total_files": 0,
        "progress_updated_at": "",
    }


def load_restore_status() -> dict[str, Any]:
    path = paths.restore_status_path()
    if not path.exists():
        return default_restore_status()
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except json.JSONDecodeError:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        path.replace(path.with_name(f"{path.name}.corrupt-{stamp}"))
        return default_restore_status()
    status = default_restore_status()
    if isinstance(loaded, dict):
        status.update(loaded)
    if status.get("state") == "running" and not restore_lock_active():
        status["state"] = "failed"
        status["active_pid"] = 0
        status["finished_at"] = iso()
        status["last_error"] = status.get("last_error") or "Restore process ended without updating status."
        save_restore_status(status)
    return status


def save_restore_status(status: dict[str, Any]) -> dict[str, Any]:
    current = default_restore_status()
    current.update(status)
    paths.ensure_base_dirs()
    write_json_atomic(paths.restore_status_path(), current, mode=0o600)
    return current


def update_restore_progress(
    *,
    phase: str = "restoring",
    message: str = "",
    percent: float | None = None,
    clear_percent: bool = False,
    bytes_done: int | None = None,
    total_bytes: int | None = None,
    files_done: int | None = None,
    total_files: int | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    status = load_restore_status()
    status.update(
        {
            "state": "running",
            "progress_phase": phase,
            "progress_message": message,
            "progress_updated_at": iso(),
        }
    )
    if target is not None:
        status["target"] = target
    if clear_percent:
        status["progress_percent"] = None
    elif percent is not None:
        status["progress_percent"] = max(0, min(100, round(float(percent), 1)))
    if bytes_done is not None:
        status["progress_bytes_done"] = max(0, int(bytes_done))
    if total_bytes is not None:
        status["progress_total_bytes"] = max(0, int(total_bytes))
    if files_done is not None:
        status["progress_files_done"] = max(0, int(files_done))
    if total_files is not None:
        status["progress_total_files"] = max(0, int(total_files))
    return save_restore_status(status)


def _read_restore_lock() -> dict[str, Any]:
    path = paths.restore_lock_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        path.unlink(missing_ok=True)
        return {}
    return data if isinstance(data, dict) else {}


def restore_lock_active() -> bool:
    path = paths.restore_lock_path()
    data = _read_restore_lock()
    try:
        pid = int(data.get("pid", 0) or 0)
    except (TypeError, ValueError):
        path.unlink(missing_ok=True)
        return False
    if pid and process_alive(pid):
        return True
    path.unlink(missing_ok=True)
    return False


def _write_restore_lock(pid: int, *, snapshot: str, parent: str) -> None:
    paths.ensure_base_dirs()
    write_json_atomic(
        paths.restore_lock_path(),
        {"pid": pid, "snapshot": snapshot, "parent": parent, "created_at": iso()},
        mode=0o600,
    )


def _clear_restore_lock(expected_pid: int | None = None) -> None:
    path = paths.restore_lock_path()
    if expected_pid is not None:
        data = _read_restore_lock()
        try:
            pid = int(data.get("pid", 0) or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid and pid != expected_pid:
            return
    path.unlink(missing_ok=True)


def list_snapshots(config: dict[str, Any]) -> list[dict[str, Any]]:
    result = run_streamed(
        restic_base_args(config) + ["snapshots", "--json", "--tag", MACUP_TAG],
        env=restic_env(config),
        check=True,
    )
    return json.loads(result.output or "[]")


def snapshot_table(config: dict[str, Any]) -> str:
    snapshots = list_snapshots(config)
    if not snapshots:
        return "No MacUp snapshots found.\n"
    lines = ["ID        Time                         Tags"]
    for snapshot in snapshots:
        sid = (snapshot.get("short_id") or snapshot.get("id") or "")[:8]
        time = str(snapshot.get("time") or "")
        tags = ", ".join(snapshot.get("tags") or [])
        lines.append(f"{sid:<8}  {time:<27}  {tags}")
    return "\n".join(lines) + "\n"


def restore_snapshot(
    config: dict[str, Any],
    *,
    snapshot: str,
    target: str,
    include_paths: list[str] | None = None,
    logger=None,
    json_output: bool = False,
    on_line=None,
) -> int:
    args = restic_base_args(config) + ["restore"]
    if json_output:
        args.append("--json")
    args.extend([snapshot, "--target", str(Path(target).expanduser())])
    for include in include_paths or []:
        args.extend(["--path", include])
    run_streamed(args, env=restic_env(config), logger=logger, check=True, on_line=on_line)
    return 0


def _next_restore_target(parent: str, snapshot: str) -> Path:
    parent_path = Path(parent).expanduser().resolve()
    if not parent_path.exists() or not parent_path.is_dir():
        raise ValueError("Restore destination must be an existing folder.")
    short = snapshot[:8] if snapshot else "snapshot"
    base = parent_path / f"MacUp Restore {short}"
    target = base
    counter = 2
    while target.exists():
        target = parent_path / f"{base.name} {counter}"
        counter += 1
    return target


def _directory_usage(path: Path) -> tuple[int, int]:
    total_bytes = 0
    total_files = 0
    if not path.exists():
        return 0, 0
    for root, _, files in os.walk(path):
        root_path = Path(root)
        for name in files:
            item = root_path / name
            try:
                stat = item.stat()
            except OSError:
                continue
            total_bytes += stat.st_size
            total_files += 1
    return total_bytes, total_files


def _start_restore_watcher(target: Path, total_bytes: int, total_files: int) -> tuple[threading.Event, threading.Thread] | None:
    if total_bytes <= 0:
        return None
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(2):
            bytes_done, files_done = _directory_usage(target)
            percent = (bytes_done / total_bytes) * 100 if total_bytes else None
            update_restore_progress(
                phase="restoring",
                message="Restoring",
                percent=percent,
                bytes_done=bytes_done,
                total_bytes=total_bytes,
                files_done=files_done,
                total_files=total_files,
                target=str(target),
            )

    thread = threading.Thread(target=watch, name="macup-restore-progress", daemon=True)
    thread.start()
    return stop, thread


def _restore_progress_from_json(line: str) -> dict[str, Any] | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    message_type = data.get("message_type")
    if message_type == "status":
        try:
            percent = None if data.get("percent_done") is None else float(data.get("percent_done")) * 100
        except (TypeError, ValueError):
            percent = None
        return {
            "phase": "restoring",
            "message": "Restoring",
            "percent": percent,
            "bytes_done": data.get("bytes_restored") or data.get("bytes_done"),
            "total_bytes": data.get("total_bytes"),
            "files_done": data.get("files_restored") or data.get("files_done"),
            "total_files": data.get("total_files"),
        }
    if message_type == "summary":
        return {
            "phase": "restoring",
            "message": "Restore data written",
            "percent": 100,
            "bytes_done": data.get("bytes_restored"),
            "total_bytes": data.get("total_bytes"),
            "files_done": data.get("files_restored"),
            "total_files": data.get("total_files"),
        }
    return None


def _restore_expected_size(config: dict[str, Any], snapshot: str, logger=None) -> tuple[int, int]:
    update_restore_progress(phase="sizing", message="Calculating restore size", clear_percent=True)
    try:
        from .snapshots import snapshot_stats

        detail = snapshot_stats(config, snapshot)
    except Exception as exc:
        if logger:
            logger.write(f"Restore size calculation unavailable: {exc}")
        update_restore_progress(phase="restoring", message="Restoring, size unknown", clear_percent=True)
        return 0, 0
    total_bytes = int(detail.get("restore_size") or 0)
    total_files = int(detail.get("file_count") or 0)
    update_restore_progress(
        phase="restoring",
        message="Starting restore",
        percent=0 if total_bytes else None,
        total_bytes=total_bytes,
        total_files=total_files,
    )
    return total_bytes, total_files


def restore_to_new_folder(
    config: dict[str, Any],
    *,
    snapshot: str,
    parent: str,
    logger=None,
) -> Path:
    total_bytes, total_files = _restore_expected_size(config, snapshot, logger=logger)
    target = _next_restore_target(parent, snapshot)
    target.mkdir(parents=True)
    update_restore_progress(
        phase="restoring",
        message="Restoring",
        percent=0 if total_bytes else None,
        total_bytes=total_bytes,
        total_files=total_files,
        target=str(target),
    )
    watcher = _start_restore_watcher(target, total_bytes, total_files)

    def on_line(line: str) -> None:
        progress = _restore_progress_from_json(line)
        if not progress:
            return
        update_restore_progress(target=str(target), **progress)

    try:
        restore_snapshot(config, snapshot=snapshot, target=str(target), logger=logger, json_output=True, on_line=on_line)
    finally:
        if watcher:
            stop, thread = watcher
            stop.set()
            thread.join(timeout=3)
    return target


def detach_restore(cli: str, *, snapshot: str, parent: str) -> int:
    if keychain.find_password(keychain.RESTIC_SERVICE, keychain.RESTIC_ACCOUNT) is None:
        raise RuntimeError("Restic password is not stored in Keychain. Save it before restoring.")
    parent_path = Path(parent).expanduser().resolve()
    if not parent_path.exists() or not parent_path.is_dir():
        raise RuntimeError("Restore destination must be an existing folder.")
    if not os.access(parent_path, os.W_OK):
        raise RuntimeError("Restore destination is not writable.")
    if restore_lock_active():
        raise RuntimeError("A restore is already running. Wait for it to finish before starting another download.")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["MACUP_RESTORE_PRELOCKED"] = "1"
    process = subprocess.Popen(
        [cli, "restore", "--snapshot", snapshot, "--target", parent, "--download"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=env,
    )
    _write_restore_lock(process.pid, snapshot=snapshot, parent=parent)
    status = load_restore_status()
    status.update(
        {
            "state": "running",
            "active_pid": process.pid,
            "snapshot": snapshot,
            "parent": str(parent_path),
            "finished_at": "",
            "last_error": "",
        }
    )
    status["started_at"] = status.get("started_at") or iso()
    status["progress_phase"] = status.get("progress_phase") or "starting"
    status["progress_message"] = status.get("progress_message") or "Starting restore"
    status["progress_percent"] = status.get("progress_percent") if status.get("progress_percent") is not None else 0
    status["progress_updated_at"] = status.get("progress_updated_at") or iso()
    save_restore_status(status)
    return 0


def restore_job(config: dict[str, Any], *, snapshot: str, parent: str) -> Path:
    prelocked = os.environ.get("MACUP_RESTORE_PRELOCKED") == "1"
    if not prelocked:
        if restore_lock_active():
            raise RuntimeError("A restore is already running.")
        _write_restore_lock(os.getpid(), snapshot=snapshot, parent=parent)
    with RunLogger(f"restore-{snapshot[:8]}") as logger:
        save_restore_status(
            {
                "state": "running",
                "active_pid": os.getpid(),
                "snapshot": snapshot,
                "parent": str(Path(parent).expanduser().resolve()),
                "target": "",
                "latest_log": str(logger.path),
                "started_at": iso(),
                "finished_at": "",
                "last_error": "",
                "progress_phase": "starting",
                "progress_message": "Starting restore",
                "progress_percent": 0,
                "progress_bytes_done": 0,
                "progress_total_bytes": 0,
                "progress_files_done": 0,
                "progress_total_files": 0,
                "progress_updated_at": iso(),
            }
        )
        try:
            logger.write(f"Restore for snapshot {snapshot} started.")
            logger.write(f"Selected restore parent: {parent}")
            target = restore_to_new_folder(config, snapshot=snapshot, parent=parent, logger=logger)
            logger.write(f"Restore completed to {target}.")
            save_restore_status(
                {
                    "state": "success",
                    "active_pid": 0,
                    "snapshot": snapshot,
                    "parent": str(Path(parent).expanduser().resolve()),
                    "target": str(target),
                    "latest_log": str(logger.path),
                    "started_at": load_restore_status().get("started_at") or "",
                    "finished_at": iso(),
                    "last_error": "",
                    "progress_phase": "complete",
                    "progress_message": "Restore complete",
                    "progress_percent": 100,
                    "progress_updated_at": iso(),
                }
            )
            return target
        except Exception as exc:
            logger.write(f"Restore failed: {exc}")
            save_restore_status(
                {
                    "state": "failed",
                    "active_pid": 0,
                    "snapshot": snapshot,
                    "parent": str(Path(parent).expanduser().resolve()),
                    "target": "",
                    "latest_log": str(logger.path),
                    "started_at": load_restore_status().get("started_at") or "",
                    "finished_at": iso(),
                    "last_error": str(exc)[:1000],
                    "progress_phase": "failed",
                    "progress_message": "Restore failed",
                    "progress_updated_at": iso(),
                }
            )
            raise
        finally:
            _clear_restore_lock(os.getpid())
