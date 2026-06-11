from __future__ import annotations

import os
import shutil
import subprocess
import time
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


def bundled_xbar_app() -> Path:
    return paths.repo_root() / "xbar (use what you need)" / "xbar.app"


def system_xbar_app() -> Path:
    return Path("/Applications/xbar.app")


def user_xbar_app() -> Path:
    return Path("~/Applications/xbar.app").expanduser()


def remove_quarantine(path: Path) -> None:
    subprocess.run(
        ["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def ensure_xbar_app_installed() -> Path | None:
    existing = system_xbar_app()
    if existing.exists():
        remove_quarantine(existing)
        return existing
    target = user_xbar_app()
    if target.exists():
        remove_quarantine(target)
        return target
    source = bundled_xbar_app()
    if not source.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(source, tmp, symlinks=True)
    remove_quarantine(tmp)
    if target.exists():
        shutil.rmtree(target)
    tmp.replace(target)
    return target


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


def quit_xbar() -> None:
    subprocess.run(
        ["/usr/bin/osascript", "-e", 'tell application id "com.xbarapp.app" to quit'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    subprocess.run(["/usr/bin/pkill", "-x", "xbar"], check=False)
    deadline = time.time() + 5
    while is_xbar_running() and time.time() < deadline:
        time.sleep(0.2)


def launch_xbar() -> tuple[bool, str]:
    app = ensure_xbar_app_installed() or find_xbar_app()
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
    deadline = time.time() + 8
    while not is_xbar_running() and time.time() < deadline:
        time.sleep(0.2)
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


def verify_xbar_plugin_output(plugin_path: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [str(plugin_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=20,
    )
    output = (result.stdout or result.stderr or "").strip()
    first_line = output.splitlines()[0] if output else ""
    if result.returncode != 0:
        return False, first_line or f"plugin exited with {result.returncode}"
    if not first_line or "|" not in first_line:
        return False, first_line or "plugin produced no menu title"
    return True, first_line


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
    quit_xbar()
    launched, xbar_message = launch_xbar()
    refreshed = refresh_xbar()
    time.sleep(0.5)
    refreshed = refresh_xbar() or refreshed
    plugin_ok, plugin_first_line = verify_xbar_plugin_output(xbar_plugin)
    return {
        "runtime_cli": str(runtime_cli),
        "xbar_plugin": str(xbar_plugin),
        "xbar_app": str(ensure_xbar_app_installed() or find_xbar_app() or ""),
        "launch_agent": str(launch_agent),
        "xbar_launched": launched,
        "xbar_message": xbar_message,
        "xbar_plugin_ok": plugin_ok,
        "xbar_plugin_first_line": plugin_first_line,
        "xbar_refreshed": refreshed,
        "xbar_running": is_xbar_running(),
    }
