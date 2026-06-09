from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
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


def normalize_repository_subpath(subpath: str) -> str:
    text = str(subpath or "").strip().replace("\\", "/").strip("/")
    if "\x00" in text:
        raise ValueError("Repository path contains an invalid character.")
    parts = [part for part in text.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("Repository path cannot contain . or .. segments.")
    return "/".join(parts)


def repository_remote_path(config: dict[str, Any], subpath: str = "") -> str:
    remote = str(config.get("remote_name") or "macup-onedrive").strip()
    repo_path = str(config.get("repository_path") or "").strip().strip("/")
    extra = normalize_repository_subpath(subpath)
    combined = "/".join(part for part in (repo_path, extra) if part)
    return f"{remote}:{combined}" if combined else f"{remote}:"


def _repository_path_parts(config: dict[str, Any], subpath: str = "") -> list[str]:
    repo_path = str(config.get("repository_path") or "").strip().strip("/")
    extra = normalize_repository_subpath(subpath)
    return [part for value in (repo_path, extra) for part in value.split("/") if part]


def _load_remote_config(config: dict[str, Any]) -> dict[str, Any]:
    remote = str(config.get("remote_name") or "macup-onedrive").strip()
    result = subprocess.run(
        [rclone_bin(), "--config", str(rclone_config_path(config)), "config", "dump"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=rclone_env(config),
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not read rclone OneDrive configuration.")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not parse rclone OneDrive configuration.") from exc
    section = payload.get(remote)
    if not isinstance(section, dict):
        raise RuntimeError(f"rclone remote '{remote}' was not found.")
    return section


def _graph_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
        message = body.get("error", {}).get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    except Exception:
        pass
    if exc.code == 401:
        return "OneDrive sign-in has expired. Reconfigure or test the remote, then try again."
    if exc.code == 404:
        return "OneDrive folder was not found. Initialize the repository or run a backup first."
    return f"Microsoft Graph returned HTTP {exc.code}."


def _validate_official_onedrive_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or ("sharepoint" not in host and "onedrive" not in host):
        raise RuntimeError("Microsoft Graph returned an unexpected OneDrive URL.")
    return url


def repository_web_url(config: dict[str, Any], subpath: str = "snapshots") -> str:
    if str(config.get("repository") or "").strip():
        raise RuntimeError("Official OneDrive folder links are only available for rclone repository locations.")
    ensure_encrypted_config(config)
    remote_path = repository_remote_path(config, subpath)
    stat = subprocess.run(
        [
            rclone_bin(),
            "--config",
            str(rclone_config_path(config)),
            "lsjson",
            "--stat",
            "--no-modtime",
            "--no-mimetype",
            remote_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=rclone_env(config),
        check=False,
        timeout=30,
    )
    if stat.returncode != 0:
        raise RuntimeError("Could not find the OneDrive snapshots folder. Initialize the repository or run a backup first.")
    try:
        item = json.loads(stat.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not parse OneDrive folder metadata.") from exc
    if item and item.get("IsDir") is False:
        raise RuntimeError("The configured OneDrive snapshots path is not a folder.")

    section = _load_remote_config(config)
    if str(section.get("type") or "").lower() != "onedrive":
        raise RuntimeError("Official OneDrive folder links require an rclone OneDrive remote.")
    drive_id = str(section.get("drive_id") or "").strip()
    if not drive_id:
        raise RuntimeError("The rclone OneDrive remote is missing its drive id. Reconfigure OneDrive and try again.")
    try:
        token = json.loads(str(section.get("token") or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not parse the rclone OneDrive token.") from exc
    access_token = str(token.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("The rclone OneDrive token is missing. Reconfigure OneDrive and try again.")

    parts = _repository_path_parts(config, subpath)
    drive = urllib.parse.quote(drive_id, safe="")
    if parts:
        encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in parts)
        graph_url = f"https://graph.microsoft.com/v1.0/drives/{drive}/root:/{encoded_path}?$select=webUrl,name,folder"
    else:
        graph_url = f"https://graph.microsoft.com/v1.0/drives/{drive}/root?$select=webUrl,name,folder"
    request = urllib.request.Request(
        graph_url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_graph_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not contact Microsoft Graph: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not parse Microsoft Graph response.") from exc
    web_url = str(data.get("webUrl") or "").strip()
    if not web_url:
        raise RuntimeError("Microsoft Graph did not return a OneDrive web URL.")
    return _validate_official_onedrive_url(web_url)


def list_repository(config: dict[str, Any], subpath: str = "") -> tuple[str, list[dict[str, Any]]]:
    ensure_encrypted_config(config)
    remote_path = repository_remote_path(config, subpath)
    result = subprocess.run(
        [rclone_bin(), "--config", str(rclone_config_path(config)), "lsjson", remote_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=rclone_env(config),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip() or "Could not list OneDrive repository folder.")
    try:
        raw = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not parse rclone folder listing.") from exc
    if not isinstance(raw, list):
        raise RuntimeError("Unexpected rclone folder listing.")
    items: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "name": str(item.get("Name") or item.get("Path") or ""),
                "path": str(item.get("Path") or item.get("Name") or ""),
                "is_dir": bool(item.get("IsDir")),
                "size": int(item.get("Size") or 0),
                "mod_time": str(item.get("ModTime") or ""),
            }
        )
    items.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    return remote_path, items
