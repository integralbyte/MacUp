from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import keychain, launchd, manager_state, paths, xbar
from .backup import process_alive
from .installer import refresh_xbar
from .restore import restore_lock_active

CONFIRMATION_TEXT = "RESET MACUP"


class ResetError(RuntimeError):
    pass


def _lock_active(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0) or 0)
    except Exception:
        path.unlink(missing_ok=True)
        return False
    if pid and process_alive(pid):
        return True
    path.unlink(missing_ok=True)
    return False


def _backup_active() -> bool:
    return _lock_active(paths.lock_path())


def _remove_tree(path: Path) -> bool:
    if not path.exists():
        return False
    shutil.rmtree(path)
    return True


def reset_local_state(confirmation: str) -> dict[str, Any]:
    if confirmation != CONFIRMATION_TEXT:
        raise ResetError(f"Type {CONFIRMATION_TEXT} to confirm reset.")
    if _backup_active():
        raise ResetError("A backup is running. Wait for it to finish before resetting MacUp.")
    if restore_lock_active():
        raise ResetError("A restore is running. Wait for it to finish before resetting MacUp.")

    launch_agent = launchd.uninstall()
    xbar_plugins = xbar.uninstall()
    keychain.delete_password(keychain.RESTIC_SERVICE, keychain.RESTIC_ACCOUNT)
    keychain.delete_password(keychain.RCLONE_SERVICE, keychain.RCLONE_ACCOUNT)
    manager_state.clear()
    removed_config = _remove_tree(paths.config_dir())
    removed_state = _remove_tree(paths.state_dir())
    xbar_refreshed = refresh_xbar()
    return {
        "removed_config": removed_config,
        "removed_state": removed_state,
        "launch_agent": str(launch_agent),
        "xbar_plugins": [str(path) for path in xbar_plugins],
        "xbar_refreshed": xbar_refreshed,
    }
