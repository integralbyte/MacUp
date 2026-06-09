from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .backup import restic_base_args, restic_env
from .config import MACUP_TAG
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
) -> int:
    args = restic_base_args(config) + ["restore", snapshot, "--target", str(Path(target).expanduser())]
    for include in include_paths or []:
        args.extend(["--path", include])
    run_streamed(args, env=restic_env(config), check=True)
    return 0
