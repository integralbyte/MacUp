from __future__ import annotations

import argparse
import getpass
import json
import sys

from . import __version__, keychain, paths
from .backup import BackupError, detach_backup, run_backup, stop_backup
from .config import load_config, save_config
from .doctor import text_report
from .installer import install_all
from .manager import detach_manager, run_manager, stop_running_manager
from .restore import detach_restore, restore_job, restore_snapshot, snapshot_table
from .status import json_output, load_status, text_output, xbar_output


def _cmd_manager(args) -> int:
    if args.stop:
        return stop_running_manager()
    if args.detach:
        return detach_manager(str(paths.cli_path()))
    return run_manager(port=args.port, open_browser=not args.no_open)


def _cmd_backup(args) -> int:
    if args.stop:
        result = stop_backup()
        print(result["message"])
        return 0 if result["stopped"] else 1
    if args.detach:
        return detach_backup(str(paths.cli_path()))
    try:
        return run_backup(load_config(), due_only=args.due, manual=args.manual)
    except BackupError as exc:
        print(f"Backup error: {exc}", file=sys.stderr)
        return 2


def _cmd_status(args) -> int:
    cfg = load_config()
    status = load_status()
    if args.format == "json":
        sys.stdout.write(json_output(cfg, status))
    elif args.format == "xbar":
        sys.stdout.write(xbar_output(cfg, status, args.xbar_cli))
    else:
        sys.stdout.write(text_output(cfg, status))
    return 0


def _cmd_install(args) -> int:
    if args.xbar and args.launchd:
        installed = install_all(load=not args.no_load)
    else:
        installed = {"error": "partial install is no longer supported; use both launchd and Xbar"}
    print(json.dumps(installed, indent=2, sort_keys=True))
    return 0


def _cmd_doctor(args) -> int:
    sys.stdout.write(text_report())
    return 0


def _cmd_config(args) -> int:
    if args.config_action == "show":
        print(json.dumps(load_config(), indent=2, sort_keys=True))
        return 0
    if args.config_action == "save-default":
        print(json.dumps(save_config(load_config()), indent=2, sort_keys=True))
        return 0
    raise SystemExit("unknown config action")


def _cmd_secrets(args) -> int:
    if args.secret_action == "set-restic":
        password = getpass.getpass("Restic repository password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.", file=sys.stderr)
            return 2
        if len(password) < 8:
            print("Password must be at least 8 characters.", file=sys.stderr)
            return 2
        keychain.store_password(keychain.RESTIC_SERVICE, keychain.RESTIC_ACCOUNT, password)
        print("Restic password stored in macOS Keychain.")
        return 0
    raise SystemExit("unknown secrets action")


def _cmd_restore(args) -> int:
    cfg = load_config()
    if args.list:
        sys.stdout.write(snapshot_table(cfg))
        return 0
    if not args.target:
        print("--target is required unless --list is used", file=sys.stderr)
        return 2
    if args.download:
        restore_job(cfg, snapshot=args.snapshot, parent=args.target)
        return 0
    if args.detach:
        return detach_restore(str(paths.cli_path()), snapshot=args.snapshot, parent=args.target)
    return restore_snapshot(cfg, snapshot=args.snapshot, target=args.target, include_paths=args.path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="macup", description="MacUp Restic+rclone backup tool")
    parser.add_argument("--version", action="version", version=f"macup {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    manager = sub.add_parser("manager", help="start the on-demand local web manager")
    manager.add_argument("--port", type=int, default=0)
    manager.add_argument("--no-open", action="store_true")
    manager.add_argument("--stop", action="store_true", help="stop the running local web manager")
    manager.add_argument("--detach", action="store_true", help="start the manager in the background")
    manager.set_defaults(func=_cmd_manager)

    backup = sub.add_parser("backup", help="run a backup")
    backup.add_argument("--due", action="store_true", help="run only when the schedule is due")
    backup.add_argument("--manual", action="store_true", help="mark this as a manual run")
    backup.add_argument("--detach", action="store_true", help="start backup in the background")
    backup.add_argument("--stop", action="store_true", help="stop the running backup")
    backup.set_defaults(func=_cmd_backup)

    status = sub.add_parser("status", help="show backup status")
    status.add_argument("--format", choices=["text", "json", "xbar"], default="text")
    status.add_argument("--xbar-cli", default="")
    status.set_defaults(func=_cmd_status)

    install = sub.add_parser("install", help="install launchd and Xbar integration")
    install.add_argument("--launchd", action=argparse.BooleanOptionalAction, default=True)
    install.add_argument("--xbar", action=argparse.BooleanOptionalAction, default=True)
    install.add_argument("--no-load", action="store_true", help="write LaunchAgent without loading it")
    install.set_defaults(func=_cmd_install)

    doctor = sub.add_parser("doctor", help="check local dependencies")
    doctor.set_defaults(func=_cmd_doctor)

    config = sub.add_parser("config", help="inspect config")
    config_sub = config.add_subparsers(dest="config_action", required=True)
    config_sub.add_parser("show")
    config_sub.add_parser("save-default")
    config.set_defaults(func=_cmd_config)

    secrets = sub.add_parser("secrets", help="manage Keychain secrets")
    secrets_sub = secrets.add_subparsers(dest="secret_action", required=True)
    secrets_sub.add_parser("set-restic")
    secrets.set_defaults(func=_cmd_secrets)

    restore = sub.add_parser("restore", help="list or restore snapshots")
    restore.add_argument("--list", action="store_true")
    restore.add_argument("--snapshot", default="latest")
    restore.add_argument("--target", default="")
    restore.add_argument("--path", action="append", default=[])
    restore.add_argument("--download", action="store_true", help="restore into a new MacUp Restore folder under --target")
    restore.add_argument("--detach", action="store_true", help="start restore in the background")
    restore.set_defaults(func=_cmd_restore)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
