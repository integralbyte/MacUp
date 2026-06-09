from __future__ import annotations

import os
import sys
from pathlib import Path


def _expand_env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cli_path() -> Path:
    local = repo_root() / "macup"
    if local.exists():
        return local
    return Path(sys.argv[0]).expanduser().resolve()


def config_dir() -> Path:
    return _expand_env_path("MACUP_CONFIG_DIR", "~/.config/macup")


def state_dir() -> Path:
    return _expand_env_path("MACUP_STATE_DIR", "~/.local/state/macup")


def logs_dir() -> Path:
    return state_dir() / "logs"


def config_path() -> Path:
    return config_dir() / "config.json"


def status_path() -> Path:
    return state_dir() / "status.json"


def restore_status_path() -> Path:
    return state_dir() / "restore-status.json"


def manager_state_path() -> Path:
    return state_dir() / "manager.json"


def snapshot_stats_cache_path() -> Path:
    return state_dir() / "snapshot-stats-cache.json"


def lock_path() -> Path:
    return state_dir() / "backup.lock"


def restore_lock_path() -> Path:
    return state_dir() / "restore.lock"


def default_rclone_config_path() -> Path:
    return config_dir() / "rclone.conf"


def runtime_dir() -> Path:
    return Path(os.environ.get("MACUP_RUNTIME_DIR", "~/.local/share/macup/app")).expanduser().resolve()


def runtime_cli_path() -> Path:
    return runtime_dir() / "macup"


def xbar_plugin_dir() -> Path:
    return Path(
        os.environ.get(
            "MACUP_XBAR_PLUGIN_DIR",
            "~/Library/Application Support/xbar/plugins",
        )
    ).expanduser().resolve()


def xbar_plugin_path() -> Path:
    return xbar_plugin_dir() / "macup.5s.sh"


def launch_agent_path() -> Path:
    return Path("~/Library/LaunchAgents/com.macup.backup.plist").expanduser().resolve()


def ensure_base_dirs() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
