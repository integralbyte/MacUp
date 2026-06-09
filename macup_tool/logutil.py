from __future__ import annotations

import os
import re
from datetime import timedelta
from pathlib import Path

from . import paths
from .timeutil import iso, utc_now

SECRET_PATTERNS = [
    re.compile(r"(?i)(access_token|refresh_token|client_secret|password|token)([\"'=:\s]+)([^\"'\s,}]+)"),
    re.compile(r"(?i)(RESTIC_PASSWORD|RCLONE_CONFIG_PASS)(=)([^ \n]+)"),
]


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", redacted)
    return redacted


class RunLogger:
    def __init__(self, run_id: str):
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        self.path = paths.logs_dir() / f"{run_id}.log"
        self._handle = self.path.open("a", encoding="utf-8")
        self.write(f"MacUp log started at {iso()}")

    def write(self, text: str = "") -> None:
        self._handle.write(redact(text.rstrip("\n")) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self.write(f"MacUp log finished at {iso()}")
        self._handle.close()

    def __enter__(self) -> "RunLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def prune_logs(days: int) -> list[Path]:
    cutoff = utc_now() - timedelta(days=days)
    removed: list[Path] = []
    for path in paths.logs_dir().glob("*.log"):
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime < cutoff.timestamp():
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed
