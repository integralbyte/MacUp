from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from . import keychain
from .backup import restic_base_args, restic_env
from .config import MACUP_TAG
from .logutil import RunLogger
from .process import run_streamed


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
    subprocess.Popen(
        [cli, "restore", "--snapshot", snapshot, "--target", parent, "--download"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    return 0


def restore_job(config: dict[str, Any], *, snapshot: str, parent: str) -> Path:
    with RunLogger(f"restore-{snapshot[:8]}") as logger:
        logger.write(f"Restore for snapshot {snapshot} started.")
        logger.write(f"Selected restore parent: {parent}")
        target = restore_to_new_folder(config, snapshot=snapshot, parent=parent, logger=logger)
        logger.write(f"Restore completed to {target}.")
        return target
