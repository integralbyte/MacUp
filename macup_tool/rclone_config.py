from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from . import keychain
from .config import rclone_config_path


@dataclass
class RcloneQuestion:
    state: str
    option: dict[str, Any]
    error: str = ""
    complete: bool = False
    raw: str = ""


def rclone_bin() -> str:
    return os.environ.get("MACUP_RCLONE_BIN") or shutil.which("rclone") or "rclone"


def rclone_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + env.get("PATH", "")
    env["RCLONE_CONFIG"] = str(rclone_config_path(config))
    if os.environ.get("MACUP_RCLONE_CONFIG_PASS"):
        env["RCLONE_CONFIG_PASS"] = os.environ["MACUP_RCLONE_CONFIG_PASS"]
    elif os.environ.get("MACUP_RCLONE_PASSWORD_COMMAND"):
        env["RCLONE_PASSWORD_COMMAND"] = os.environ["MACUP_RCLONE_PASSWORD_COMMAND"]
    else:
        env["RCLONE_PASSWORD_COMMAND"] = keychain.rclone_password_command()
    return env


def ensure_encrypted_config(config: dict[str, Any]) -> None:
    keychain.ensure_rclone_password()
    path = rclone_config_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    env = rclone_env(config)
    check = subprocess.run(
        [rclone_bin(), "--config", str(path), "config", "encryption", "check"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        check=False,
    )
    if check.returncode == 0:
        return
    args = [rclone_bin(), "--config", str(path)]
    if env.get("RCLONE_PASSWORD_COMMAND"):
        args.extend(["--password-command", env["RCLONE_PASSWORD_COMMAND"]])
    args.extend(["config", "encryption", "set"])
    subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        check=True,
    )


def _extract_json(output: str) -> dict[str, Any] | None:
    text = output.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\})", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _run_config(config: dict[str, Any], args: list[str]) -> RcloneQuestion:
    ensure_encrypted_config(config)
    path = rclone_config_path(config)
    env = rclone_env(config)
    result = subprocess.run(
        [rclone_bin(), "--config", str(path)] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        check=False,
    )
    payload = _extract_json(result.stdout)
    if payload:
        state = payload.get("State") or ""
        return RcloneQuestion(
            state=state,
            option=payload.get("Option") or {},
            error=payload.get("Error") or "",
            complete=not bool(state),
            raw=result.stdout,
        )
    if result.returncode != 0:
        return RcloneQuestion(
            state="",
            option={},
            error=result.stdout.strip() or f"rclone exited with {result.returncode}",
            complete=False,
            raw=result.stdout,
        )
    return RcloneQuestion(state="", option={}, complete=True, raw=result.stdout)


def start_onedrive_flow(config: dict[str, Any]) -> RcloneQuestion:
    remote = str(config.get("remote_name") or "macup-onedrive")
    return _run_config(
        config,
        ["config", "create", remote, "onedrive", "--non-interactive"],
    )


def continue_flow(config: dict[str, Any], state: str, answer: str) -> RcloneQuestion:
    remote = str(config.get("remote_name") or "macup-onedrive")
    return _run_config(
        config,
        [
            "config",
            "update",
            remote,
            "--continue",
            "--state",
            state,
            "--result",
            answer,
            "--non-interactive",
        ],
    )


def remote_exists(config: dict[str, Any]) -> bool:
    remote = str(config.get("remote_name") or "macup-onedrive")
    try:
        ensure_encrypted_config(config)
        result = subprocess.run(
            [rclone_bin(), "--config", str(rclone_config_path(config)), "listremotes"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=rclone_env(config),
            timeout=8,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0 and f"{remote}:" in result.stdout.splitlines()


def test_remote(config: dict[str, Any]) -> tuple[bool, str]:
    ensure_encrypted_config(config)
    remote = str(config.get("remote_name") or "macup-onedrive")
    result = subprocess.run(
        [rclone_bin(), "--config", str(rclone_config_path(config)), "lsd", f"{remote}:"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=rclone_env(config),
        check=False,
    )
    return result.returncode == 0, result.stdout.strip()
