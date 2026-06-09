from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import launchd, paths, xbar


def copy_runtime() -> Path:
    source = paths.repo_root().resolve()
    destination = paths.runtime_dir()
    if source == destination:
        return paths.runtime_cli_path()

    tmp = destination.with_name(destination.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source / "macup", tmp / "macup")
    os.chmod(tmp / "macup", 0o755)
    shutil.copytree(
        source / "macup_tool",
        tmp / "macup_tool",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    if (source / "pyproject.toml").exists():
        shutil.copy2(source / "pyproject.toml", tmp / "pyproject.toml")
    if (source / "README.md").exists():
        shutil.copy2(source / "README.md", tmp / "README.md")

    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = destination.with_name(destination.name + ".old")
    shutil.rmtree(backup, ignore_errors=True)
    if destination.exists():
        destination.replace(backup)
    tmp.replace(destination)
    shutil.rmtree(backup, ignore_errors=True)
    return paths.runtime_cli_path()


def find_xbar_app() -> Path | None:
    candidates = [
        Path("/Applications/xbar.app"),
        Path("~/Applications/xbar.app").expanduser(),
        paths.repo_root() / "xbar (use what you need)" / "xbar.app",
    ]
    return next((path for path in candidates if path.exists()), None)


def is_xbar_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-x", "xbar"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    result = subprocess.run(
        ["pgrep", "-if", "xbar.app"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return result.returncode == 0


def launch_xbar() -> tuple[bool, str]:
    app = find_xbar_app()
    if app is None:
        return False, "xbar.app was not found. Install or open Xbar, then refresh plugins."
    result = subprocess.run(
        ["/usr/bin/open", str(app)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False, result.stderr.strip() or "Could not launch Xbar."
    return True, str(app)


def refresh_xbar() -> bool:
    result = subprocess.run(
        ["/usr/bin/open", "xbar://app.xbarapp.com/refreshAllPlugins"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return result.returncode == 0


def open_full_disk_access_settings() -> tuple[bool, str]:
    urls = [
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_AllFiles",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
    ]
    for url in urls:
        result = subprocess.run(
            ["/usr/bin/open", url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return True, "System Settings opened. Add Terminal, Python, restic, and rclone to Full Disk Access if backups hit protected folders."
    subprocess.run(["/usr/bin/open", "-b", "com.apple.systempreferences"], check=False)
    return False, "System Settings opened. Go to Privacy & Security, then Full Disk Access."


def install_all(load: bool = True) -> dict[str, Any]:
    runtime_cli = copy_runtime()
    xbar_plugin = xbar.install(str(runtime_cli))
    launch_agent = launchd.install(str(runtime_cli), load=load)
    launched, xbar_message = launch_xbar()
    refreshed = refresh_xbar()
    return {
        "runtime_cli": str(runtime_cli),
        "xbar_plugin": str(xbar_plugin),
        "launch_agent": str(launch_agent),
        "xbar_launched": launched,
        "xbar_message": xbar_message,
        "xbar_refreshed": refreshed,
        "xbar_running": is_xbar_running(),
    }
