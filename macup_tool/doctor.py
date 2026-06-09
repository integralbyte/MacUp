from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import paths
from .keychain import security_bin


def _command_version(command: str, args: list[str]) -> str:
    path = shutil.which(command)
    if not path:
        return ""
    result = subprocess.run(
        [path] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return result.stdout.splitlines()[0] if result.stdout else path


def checks() -> dict[str, Any]:
    xbar_app_paths = [
        Path("/Applications/xbar.app"),
        Path("~/Applications/xbar.app").expanduser(),
        paths.repo_root() / "xbar (use what you need)" / "xbar.app",
    ]
    return {
        "macos": platform.platform(),
        "python": platform.python_version(),
        "restic": _command_version("restic", ["version"]),
        "rclone": _command_version("rclone", ["version"]),
        "brew": shutil.which("brew") or "",
        "security": security_bin() if Path(security_bin()).exists() else "",
        "osascript": shutil.which("osascript") or "",
        "launchctl": shutil.which("launchctl") or "",
        "xbar_app": next((str(path) for path in xbar_app_paths if path.exists()), ""),
        "xbar_plugin_dir": str(paths.xbar_plugin_dir()),
        "full_disk_access_note": "Grant Terminal or your Python runner Full Disk Access for protected folders.",
    }


def text_report() -> str:
    data = checks()
    lines = ["MacUp doctor"]
    for key, value in data.items():
        lines.append(f"{key}: {value or 'missing'}")
    return "\n".join(lines) + "\n"
