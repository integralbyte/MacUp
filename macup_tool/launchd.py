from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from . import paths
from .timeutil import parse_iso

LABEL = "com.macup.backup"
DEFAULT_CHECK_MINUTE = 0


def check_minute() -> int:
    status_path = paths.status_path()
    if not status_path.exists():
        return DEFAULT_CHECK_MINUTE
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_CHECK_MINUTE
    last_success = parse_iso(str(status.get("last_success_at") or ""))
    if last_success is None:
        return DEFAULT_CHECK_MINUTE
    return last_success.astimezone().minute


def plist_data(cli: str | None = None) -> dict:
    paths.ensure_base_dirs()
    cli_path = cli or str(paths.cli_path())
    minute = check_minute()
    path_value = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    return {
        "Label": LABEL,
        "ProgramArguments": [cli_path, "backup", "--due"],
        "RunAtLoad": True,
        "StartCalendarInterval": {"Minute": minute},
        "StandardOutPath": str(paths.logs_dir() / "launchd.out.log"),
        "StandardErrorPath": str(paths.logs_dir() / "launchd.err.log"),
        "EnvironmentVariables": {
            "PATH": path_value,
            "PYTHONUTF8": "1",
        },
    }


def write_plist(cli: str | None = None) -> Path:
    path = paths.launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(plist_data(cli), handle, sort_keys=False)
    return path


def load_plist() -> None:
    path = paths.launch_agent_path()
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], check=True)
    subprocess.run(["launchctl", "enable", f"gui/{uid}/{LABEL}"], check=False)


def install(cli: str | None = None, load: bool = True) -> Path:
    path = write_plist(cli)
    if load:
        load_plist()
    return path


def reload_later(cli: str | None = None, delay: float = 5.0) -> bool:
    if not paths.launch_agent_path().exists():
        return False
    cli_path = cli or str(paths.cli_path())
    app_root = str(Path(cli_path).resolve().parent)
    code = (
        "import time; "
        f"time.sleep({delay!r}); "
        "from macup_tool import launchd; "
        f"launchd.install({cli_path!r}, load=True)"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = app_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=env,
    )
    return True


def uninstall() -> Path:
    path = paths.launch_agent_path()
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    path.unlink(missing_ok=True)
    return path
