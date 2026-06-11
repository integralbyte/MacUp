from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import keychain, paths
from .config import MACUP_TAG, RUN_TAG_PREFIX, repository, rclone_config_path, upload_limit, validate_config
from .logutil import RunLogger, prune_logs
from .process import CommandError, CommandResult, run_streamed
from .rclone_config import rclone_bin
from .status import is_due, load_status, mark_backup_progress, mark_failed, mark_running, mark_stopped, mark_success
from .timeutil import iso


class BackupError(RuntimeError):
    pass


@dataclass
class BackupCommand:
    args: list[str]
    cwd: str | None = None


def restic_bin() -> str:
    return os.environ.get("MACUP_RESTIC_BIN") or shutil.which("restic") or "restic"


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _descendant_pids(root_pid: int) -> list[int]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid="],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    children: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
    found: list[int] = []
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        found.append(pid)
        stack.extend(children.get(pid, []))
    return found


def _signal_pid(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass


def _signal_process_tree(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass
    except OSError:
        pass
    for child in reversed(_descendant_pids(pid)):
        _signal_pid(child, sig)
    _signal_pid(pid, sig)


def stop_backup(timeout: float = 5.0) -> dict[str, Any]:
    lock_path = paths.lock_path()
    if not lock_path.exists():
        status = load_status()
        if status.get("state") == "running" or status.get("active_run_id"):
            mark_stopped(str(status.get("active_run_id") or ""), message="Backup status was stale and has been cleared.")
            return {"stopped": True, "message": "Cleared stale running backup status."}
        return {"stopped": False, "message": "No backup is running."}
    try:
        lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(lock_data.get("pid", 0) or 0)
        run_id_value = str(lock_data.get("run_id") or "")
    except Exception:
        pid = 0
        run_id_value = ""
    if pid and process_alive(pid):
        _signal_process_tree(pid, signal.SIGTERM)
        deadline = datetime.now(timezone.utc).timestamp() + timeout
        while process_alive(pid) and datetime.now(timezone.utc).timestamp() < deadline:
            time.sleep(0.2)
        if process_alive(pid):
            _signal_process_tree(pid, signal.SIGKILL)
    lock_path.unlink(missing_ok=True)
    status = load_status()
    latest_log = Path(str(status.get("latest_log") or "")) if status.get("latest_log") else None
    mark_stopped(run_id_value, latest_log, "Backup was stopped by user.")
    return {"stopped": True, "message": "Backup stopped."}


class BackupLock:
    def __init__(self, path: Path):
        self.path = path
        self.acquired = False

    def acquire(self, run_id_value: str) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                pid = int(data.get("pid", 0))
            except Exception:
                pid = 0
            if pid and process_alive(pid):
                return False
            self.path.unlink(missing_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.path, flags, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "run_id": run_id_value, "created_at": iso()}, handle)
        self.acquired = True
        return True

    def release(self) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False

    def __enter__(self) -> "BackupLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def restic_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + env.get("PATH", "")
    env["RESTIC_REPOSITORY"] = repository(config)
    if str(env["RESTIC_REPOSITORY"]).startswith("rclone:"):
        env["RCLONE_CONFIG"] = str(rclone_config_path(config))
        if os.environ.get("MACUP_RCLONE_CONFIG_PASS"):
            env["RCLONE_CONFIG_PASS"] = os.environ["MACUP_RCLONE_CONFIG_PASS"]
        elif os.environ.get("MACUP_RCLONE_PASSWORD_COMMAND"):
            env["RCLONE_PASSWORD_COMMAND"] = os.environ["MACUP_RCLONE_PASSWORD_COMMAND"]
        else:
            env["RCLONE_PASSWORD_COMMAND"] = keychain.rclone_password_command()
    limit = upload_limit(config)
    if limit:
        env["RCLONE_BWLIMIT"] = limit

    if os.environ.get("MACUP_RESTIC_PASSWORD"):
        env["RESTIC_PASSWORD"] = os.environ["MACUP_RESTIC_PASSWORD"]
    elif os.environ.get("MACUP_RESTIC_PASSWORD_COMMAND"):
        env["RESTIC_PASSWORD_COMMAND"] = os.environ["MACUP_RESTIC_PASSWORD_COMMAND"]
    else:
        if keychain.find_password(keychain.RESTIC_SERVICE, keychain.RESTIC_ACCOUNT) is None:
            raise BackupError("Restic password is not stored in Keychain. Open the manager and save it first.")
        env["RESTIC_PASSWORD_COMMAND"] = keychain.restic_password_command()
    return env


def restic_base_args(config: dict[str, Any]) -> list[str]:
    args = [restic_bin()]
    if repository(config).startswith("rclone:"):
        args.extend(["-o", f"rclone.program={rclone_bin()}"])
    return args


def build_backup_commands(config: dict[str, Any], run_tag: str) -> list[BackupCommand]:
    sources = [str(Path(source).expanduser()) for source in config.get("sources", [])]
    mode = config.get("path_mode", "preserve")
    base = restic_base_args(config)
    if mode == "preserve":
        return [
            BackupCommand(
                args=base + ["backup", "--json", "--tag", MACUP_TAG, "--tag", run_tag] + sources,
                cwd=None,
            )
        ]
    commands: list[BackupCommand] = []
    for source in sources:
        path = Path(source)
        commands.append(
            BackupCommand(
                args=base + ["backup", "--json", "--tag", MACUP_TAG, "--tag", run_tag, path.name],
                cwd=str(path.parent),
            )
        )
    return commands


def run_restic(config: dict[str, Any], args: list[str], logger=None, check: bool = True) -> CommandResult:
    return run_streamed(restic_base_args(config) + args, env=restic_env(config), logger=logger, check=check)


def _repository_password_mismatch(output: str) -> bool:
    text = output.lower()
    return "wrong password" in text or "no key found" in text


def _repository_password_mismatch_error(config: dict[str, Any]) -> BackupError:
    return BackupError(
        "An existing Restic repository was found at "
        f"{repository(config)}, but the saved password cannot unlock it. "
        "This commonly happens after resetting MacUp and entering a new password while reusing the same OneDrive repository path. "
        "To reconnect to existing backups, save the original Restic password. "
        "To start a brand new backup set, change the repository path to a new empty folder, then initialize again. "
        "MacUp did not delete or overwrite the existing OneDrive repository."
    )


def ensure_repository(config: dict[str, Any], logger: RunLogger, *, create: bool = True) -> None:
    probe = run_restic(config, ["snapshots", "--json"], logger=logger, check=False)
    if probe.returncode == 0:
        return
    if _repository_password_mismatch(probe.output):
        raise _repository_password_mismatch_error(config)
    if not create:
        raise BackupError(
            "Reconnect existing backups was selected, but MacUp could not open a Restic repository at "
            f"{repository(config)}. Confirm the OneDrive account, repository path, and original Restic password. "
            "To create a brand new backup set instead, choose Start new backup set. "
            f"Probe output: {probe.output[-1000:]}"
        )
    logger.write("Repository probe failed; trying restic init.")
    init = run_restic(config, ["init"], logger=logger, check=False)
    if init.returncode != 0:
        if _repository_password_mismatch(probe.output) or "config file already exists" in init.output.lower():
            raise _repository_password_mismatch_error(config)
        raise BackupError(
            "Unable to open or initialize Restic repository. "
            f"Probe output: {probe.output[-1000:]} Init output: {init.output[-1000:]}"
        )


def _run_tags(snapshot: dict[str, Any]) -> list[str]:
    tags = snapshot.get("tags") or []
    if not isinstance(tags, list):
        return []
    return [tag for tag in tags if isinstance(tag, str) and tag.startswith(RUN_TAG_PREFIX)]


def snapshot_ids_to_forget(snapshots: list[dict[str, Any]], keep_runs: int) -> list[str]:
    grouped: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        if MACUP_TAG not in (snapshot.get("tags") or []):
            continue
        run_tags = _run_tags(snapshot)
        if not run_tags:
            continue
        run_tag = run_tags[0]
        group = grouped.setdefault(run_tag, {"time": "", "ids": []})
        group["ids"].append(snapshot.get("id") or snapshot.get("short_id"))
        if str(snapshot.get("time") or "") > group["time"]:
            group["time"] = str(snapshot.get("time") or "")
    ordered = sorted(grouped.items(), key=lambda item: item[1]["time"], reverse=True)
    old = ordered[max(keep_runs, 0) :]
    ids: list[str] = []
    for _, group in old:
        ids.extend([snapshot_id for snapshot_id in group["ids"] if snapshot_id])
    return ids


def prune_snapshots(config: dict[str, Any], logger: RunLogger) -> None:
    keep_runs = int(config.get("retention_count", 14))
    result = run_restic(config, ["snapshots", "--json", "--tag", MACUP_TAG], logger=logger, check=False)
    if result.returncode != 0:
        logger.write("Skipping snapshot pruning because snapshot listing failed.")
        return
    try:
        snapshots = json.loads(result.output or "[]")
    except json.JSONDecodeError:
        logger.write("Skipping snapshot pruning because Restic JSON could not be parsed.")
        return
    ids = snapshot_ids_to_forget(snapshots, keep_runs)
    if not ids:
        logger.write("No old MacUp snapshots to prune.")
        return
    run_restic(config, ["forget"] + ids + ["--prune"], logger=logger, check=True)


def cleanup_failed_run(config: dict[str, Any], run_tag: str, logger: RunLogger) -> None:
    result = run_restic(config, ["forget", "--tag", run_tag, "--prune"], logger=logger, check=False)
    if result.returncode != 0:
        logger.write("Failed-run snapshot cleanup did not complete.")


def _backup_progress_from_json(line: str) -> dict[str, Any] | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    message_type = data.get("message_type")
    if message_type == "status":
        try:
            percent_value = None if data.get("percent_done") is None else float(data.get("percent_done")) * 100
        except (TypeError, ValueError):
            percent_value = None
        return {
            "phase": "backing_up",
            "message": "Backing up",
            "percent": percent_value,
            "bytes_done": data.get("bytes_done"),
            "total_bytes": data.get("total_bytes"),
            "files_done": data.get("files_done"),
            "total_files": data.get("total_files"),
        }
    if message_type == "summary":
        return {
            "phase": "backing_up",
            "message": "Backup data written",
            "percent": 100,
            "bytes_done": data.get("total_bytes_processed"),
            "total_bytes": data.get("total_bytes_processed"),
            "files_done": data.get("total_files_processed"),
            "total_files": data.get("total_files_processed"),
        }
    return None


def run_backup(config: dict[str, Any], *, due_only: bool = False, manual: bool = False) -> int:
    errors = validate_config(config, require_sources=True)
    if errors:
        raise BackupError("; ".join(errors))
    current_status = load_status()
    if due_only and not is_due(config, current_status):
        print("Backup is not due.")
        return 0
    if not config.get("initialized"):
        raise BackupError(
            "Restic repository is not initialized for the configured location. "
            "Open the manager and initialize/probe the repository before running backups."
        )

    run_id_value = run_id()
    run_tag = f"{RUN_TAG_PREFIX}{run_id_value}"
    lock = BackupLock(paths.lock_path())
    if not lock.acquire(run_id_value):
        print("A backup is already running.")
        return 0

    with lock:
        with RunLogger(run_id_value) as logger:
            mark_running(run_id_value, logger.path)
            try:
                logger.write(f"Backup run {run_id_value} started. manual={manual} due_only={due_only}")
                prune_logs(int(config.get("log_retention_days", 14)))
                mark_backup_progress(run_id_value, logger.path, phase="preparing", message="Preparing backup")
                ensure_repository(config, logger)
                commands = build_backup_commands(config, run_tag)
                command_count = len(commands)
                for index, command in enumerate(commands, start=1):
                    mark_backup_progress(
                        run_id_value,
                        logger.path,
                        phase="backing_up",
                        message="Backing up",
                        current=index,
                        total=command_count,
                    )

                    def on_line(line: str, current=index) -> None:
                        progress = _backup_progress_from_json(line)
                        if not progress:
                            return
                        mark_backup_progress(
                            run_id_value,
                            logger.path,
                            current=current,
                            total=command_count,
                            **progress,
                        )

                    run_streamed(
                        command.args,
                        cwd=command.cwd,
                        env=restic_env(config),
                        logger=logger,
                        check=True,
                        on_line=on_line,
                    )
                mark_backup_progress(run_id_value, logger.path, phase="pruning", message="Pruning old snapshots")
                prune_snapshots(config, logger)
            except Exception as exc:
                logger.write(f"Backup failed: {exc}")
                try:
                    cleanup_failed_run(config, run_tag, logger)
                except Exception as cleanup_exc:
                    logger.write(f"Failed-run cleanup error: {cleanup_exc}")
                mark_failed(run_id_value, logger.path, str(exc))
                raise
            mark_success(run_id_value, logger.path)
            logger.write(f"Backup run {run_id_value} completed successfully.")
    return 0


def detach_backup(cli: str | None = None) -> int:
    cli_path = cli or str(paths.cli_path())
    subprocess.Popen(
        [cli_path, "backup", "--manual"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    print("Backup started.")
    return 0
