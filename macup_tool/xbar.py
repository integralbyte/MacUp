from __future__ import annotations

import os
from pathlib import Path

from . import paths
from .atomic import write_text_atomic


def plugin_script(cli: str | None = None) -> str:
    cli_path = cli or str(paths.cli_path())
    return f"""#!/usr/bin/env bash
# <xbar.title>MacUp Backup Status</xbar.title>
# <xbar.version>v0.1.0</xbar.version>
# <xbar.author>MacUp</xbar.author>
# <xbar.desc>Shows MacUp backup status and actions.</xbar.desc>
# <xbar.dependencies>bash,python3,restic,rclone</xbar.dependencies>

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
MACUP_CLI={_bash_quote(cli_path)}
exec "$MACUP_CLI" status --format xbar --xbar-cli "$MACUP_CLI"
"""


def _bash_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def install(cli: str | None = None) -> Path:
    paths.xbar_plugin_dir().mkdir(parents=True, exist_ok=True)
    target = paths.xbar_plugin_path()
    write_text_atomic(target, plugin_script(cli), mode=0o755)
    os.chmod(target, 0o755)
    return target
