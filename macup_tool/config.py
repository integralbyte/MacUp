from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Any

from . import paths
from .atomic import write_json_atomic

CONFIG_VERSION = 1
MACUP_TAG = "macup"
RUN_TAG_PREFIX = "macup-run-"
DEFAULT_REMOTE_NAME = "macup-onedrive"
DEFAULT_INTERVAL_HOURS = 24
DEFAULT_RETENTION_COUNT = 14
DEFAULT_LOG_RETENTION_DAYS = 14


def host_name() -> str:
    raw = socket.gethostname().split(".")[0] or "mac"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-") or "mac"


def default_config() -> dict[str, Any]:
    hostname = host_name()
    return {
        "version": CONFIG_VERSION,
        "backup_interval_hours": DEFAULT_INTERVAL_HOURS,
        "retention_count": DEFAULT_RETENTION_COUNT,
        "log_retention_days": DEFAULT_LOG_RETENTION_DAYS,
        "path_mode": "preserve",
        "sources": [],
        "remote_name": DEFAULT_REMOTE_NAME,
        "repository_path": f"MacUp/{hostname}/restic",
        "repository": "",
        "repository_history": [],
        "rclone_config_path": str(paths.default_rclone_config_path()),
        "rclone_configured": False,
        "upload_limit": "",
        "initialized": False,
    }


def load_config() -> dict[str, Any]:
    path = paths.config_path()
    if not path.exists():
        return default_config()
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    cfg = default_config()
    cfg.update(loaded)
    cfg["version"] = CONFIG_VERSION
    return cfg


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    cfg = default_config()
    cfg.update(config)
    cfg["sources"] = normalize_sources(cfg.get("sources", []))
    errors = validate_config(cfg, require_sources=False)
    if errors:
        raise ValueError("; ".join(errors))
    paths.ensure_base_dirs()
    write_json_atomic(paths.config_path(), cfg)
    return cfg


def normalize_sources(raw_sources: Any) -> list[str]:
    if not isinstance(raw_sources, list):
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for item in raw_sources:
        if not isinstance(item, str):
            continue
        text = str(Path(item).expanduser())
        if text and text not in seen:
            seen.add(text)
            normalized.append(text)
    return normalized


def repository(config: dict[str, Any]) -> str:
    override = str(config.get("repository") or "").strip()
    if override:
        return str(Path(override).expanduser()) if override.startswith(("~", "/")) else override
    remote = str(config.get("remote_name") or DEFAULT_REMOTE_NAME).strip()
    repo_path = str(config.get("repository_path") or f"MacUp/{host_name()}/restic").strip()
    repo_path = repo_path.lstrip("/")
    return f"rclone:{remote}:{repo_path}"


def rclone_config_path(config: dict[str, Any]) -> Path:
    return Path(str(config.get("rclone_config_path") or paths.default_rclone_config_path())).expanduser()


def upload_limit(config: dict[str, Any]) -> str:
    return str(config.get("upload_limit") or "").strip()


def validate_config(config: dict[str, Any], require_sources: bool = True) -> list[str]:
    errors: list[str] = []
    interval = config.get("backup_interval_hours")
    try:
        if float(interval) <= 0:
            errors.append("backup interval must be greater than 0")
    except (TypeError, ValueError):
        errors.append("backup interval must be a number")

    for key, label in (
        ("retention_count", "retention count"),
        ("log_retention_days", "log retention days"),
    ):
        try:
            if int(config.get(key)) < 1:
                errors.append(f"{label} must be at least 1")
        except (TypeError, ValueError):
            errors.append(f"{label} must be an integer")

    path_mode = config.get("path_mode")
    if path_mode not in {"preserve", "flat"}:
        errors.append("path mode must be preserve or flat")

    sources = normalize_sources(config.get("sources", []))
    if require_sources and not sources:
        errors.append("at least one source folder is required")
    for source in sources:
        path = Path(source).expanduser()
        if not path.is_absolute():
            errors.append(f"source must be an absolute path: {source}")
    if path_mode == "flat":
        basenames: dict[str, str] = {}
        for source in sources:
            name = Path(source).name
            if name in basenames:
                errors.append(
                    f"flat path mode cannot use duplicate folder name '{name}': "
                    f"{basenames[name]} and {source}"
                )
            else:
                basenames[name] = source

    if not repository(config):
        errors.append("repository is required")
    return errors
