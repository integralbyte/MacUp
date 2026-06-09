from __future__ import annotations

import os
import secrets
import shlex
import subprocess

RESTIC_SERVICE = "com.macup.restic-password"
RESTIC_ACCOUNT = "macup"
RCLONE_SERVICE = "com.macup.rclone-config-password"
RCLONE_ACCOUNT = "macup"


def security_bin() -> str:
    return os.environ.get("MACUP_SECURITY_BIN", "/usr/bin/security")


def password_command(service: str, account: str) -> str:
    return " ".join(
        shlex.quote(part)
        for part in (
            security_bin(),
            "find-generic-password",
            "-a",
            account,
            "-s",
            service,
            "-w",
        )
    )


def find_password(service: str, account: str) -> str | None:
    result = subprocess.run(
        [security_bin(), "find-generic-password", "-a", account, "-s", service, "-w"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def store_password(service: str, account: str, password: str) -> None:
    subprocess.run(
        [
            security_bin(),
            "add-generic-password",
            "-a",
            account,
            "-s",
            service,
            "-w",
            password,
            "-U",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )


def delete_password(service: str, account: str) -> bool:
    result = subprocess.run(
        [security_bin(), "delete-generic-password", "-a", account, "-s", service],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return result.returncode == 0


def ensure_password(service: str, account: str) -> bool:
    if find_password(service, account) is not None:
        return False
    store_password(service, account, secrets.token_urlsafe(48))
    return True


def ensure_restic_password() -> bool:
    return ensure_password(RESTIC_SERVICE, RESTIC_ACCOUNT)


def ensure_rclone_password() -> bool:
    return ensure_password(RCLONE_SERVICE, RCLONE_ACCOUNT)


def restic_password_command() -> str:
    return password_command(RESTIC_SERVICE, RESTIC_ACCOUNT)


def rclone_password_command() -> str:
    return password_command(RCLONE_SERVICE, RCLONE_ACCOUNT)
