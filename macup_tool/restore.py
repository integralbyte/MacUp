from __future__ import annotations

import json
import os
import subprocess
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
) -> int:
    args = restic_base_args(config) + ["restore", snapshot, "--target", str(Path(target).expanduser())]
    for include in include_paths or []:
        args.extend(["--path", include])
    run_streamed(args, env=restic_env(config), logger=logger, check=True)
    return 0


def restore_to_new_folder(
    config: dict[str, Any],
    *,
    snapshot: str,
    parent: str,
    logger=None,
) -> Path:
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
    target.mkdir(parents=True)
    restore_snapshot(config, snapshot=snapshot, target=str(target), logger=logger)
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
    save_restore_status(
        {
            "state": "running",
            "active_pid": process.pid,
            "snapshot": snapshot,
            "parent": str(parent_path),
            "target": "",
            "latest_log": "",
            "started_at": iso(),
            "finished_at": "",
            "last_error": "",
        }
    )
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
                }
            )
            raise
        finally:
            _clear_restore_lock(os.getpid())
