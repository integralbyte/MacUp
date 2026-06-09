from __future__ import annotations

import json
from typing import Any

from .backup import restic_base_args, restic_env
from .config import MACUP_TAG, RUN_TAG_PREFIX
from .process import run_streamed
from .timeutil import relative_display


def format_bytes(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "unknown"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024
        unit += 1
    if unit == 0:
        return f"{int(size)} {units[unit]}"
    return f"{size:.1f} {units[unit]}"


def _snapshot_id(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("id") or snapshot.get("short_id") or "")


def _run_tag(snapshot: dict[str, Any]) -> str:
    for tag in snapshot.get("tags") or []:
        if isinstance(tag, str) and tag.startswith(RUN_TAG_PREFIX):
            return tag
    return ""


def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot_id = _snapshot_id(snapshot)
    time = str(snapshot.get("time") or "")
    return {
        "id": snapshot_id,
        "short_id": str(snapshot.get("short_id") or snapshot_id[:8]),
        "time": time,
        "when": relative_display(time),
        "hostname": str(snapshot.get("hostname") or ""),
        "paths": snapshot.get("paths") or [],
        "tags": snapshot.get("tags") or [],
        "run_tag": _run_tag(snapshot),
    }


def list_snapshots(config: dict[str, Any]) -> list[dict[str, Any]]:
    result = run_streamed(
        restic_base_args(config) + ["snapshots", "--json", "--tag", MACUP_TAG],
        env=restic_env(config),
        check=True,
    )
    raw = json.loads(result.output or "[]")
    if not isinstance(raw, list):
        return []
    snapshots = [_public_snapshot(snapshot) for snapshot in raw if isinstance(snapshot, dict)]
    return sorted(snapshots, key=lambda item: str(item.get("time") or ""), reverse=True)


def snapshot_detail(config: dict[str, Any], snapshot_id: str) -> dict[str, Any]:
    snapshots = list_snapshots(config)
    selected = next(
        (
            snapshot
            for snapshot in snapshots
            if snapshot.get("id") == snapshot_id or snapshot.get("short_id") == snapshot_id
        ),
        None,
    )
    if selected is None:
        raise ValueError(f"Snapshot not found: {snapshot_id}")

    stats = {}
    result = run_streamed(
        restic_base_args(config) + ["stats", "--json", "--mode", "restore-size", snapshot_id],
        env=restic_env(config),
        check=False,
    )
    if result.returncode == 0:
        try:
            parsed = json.loads(result.output or "{}")
            if isinstance(parsed, dict):
                stats = parsed
        except json.JSONDecodeError:
            stats = {}
    total_size = stats.get("total_size")
    return {
        "snapshot": selected,
        "stats": stats,
        "restore_size": total_size,
        "restore_size_display": format_bytes(total_size),
        "file_count": stats.get("total_file_count"),
        "stats_error": "" if result.returncode == 0 else result.output[-1000:],
    }
