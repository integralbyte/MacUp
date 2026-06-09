from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from . import paths

LABEL = "com.macup.backup"


def plist_data(cli: str | None = None) -> dict:
    paths.ensure_base_dirs()
    cli_path = cli or str(paths.cli_path())
    path_value = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    return {
        "Label": LABEL,
        "ProgramArguments": [cli_path, "backup", "--due"],
        "RunAtLoad": True,
        "StartCalendarInterval": {"Minute": 0},
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
