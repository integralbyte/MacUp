from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import keychain, manager_state, paths, rclone_config, snapshots
from .backup import BackupError, detach_backup, ensure_repository, run_backup, run_id
from .config import load_config, normalize_sources, repository, rclone_config_path, save_config, validate_config
from .doctor import checks
from .installer import install_all, is_xbar_running, open_full_disk_access_settings, refresh_xbar
from .logutil import RunLogger
from . import logutil
from .reset import CONFIRMATION_TEXT, reset_local_state
from .restore import detach_restore, load_restore_status
from .status import json_output, load_status, summarize
from .timeutil import iso


def pick_folders() -> list[str]:
    script = """
set chosenFolders to choose folder with prompt "Select folders to back up" with multiple selections allowed
set outputText to ""
repeat with chosenFolder in chosenFolders
    set outputText to outputText & POSIX path of chosenFolder & linefeed
end repeat
return outputText
"""
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Folder picker was cancelled.")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def pick_restore_destination() -> str:
    script = """
set chosenFolder to choose folder with prompt "Choose where MacUp should restore this snapshot"
return POSIX path of chosenFolder
"""
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Restore destination picker was cancelled.")
    return result.stdout.strip()


def _question_payload(question: rclone_config.RcloneQuestion) -> dict[str, Any]:
    return {
        "state": question.state,
        "option": question.option,
        "error": question.error,
        "complete": question.complete,
        "raw": "",
    }


def _install_ready() -> bool:
    return paths.xbar_plugin_path().exists() and paths.launch_agent_path().exists()


def _setup_state(config: dict[str, Any], restic_password_set: bool) -> dict[str, Any]:
    onedrive_ready = bool(config.get("rclone_configured")) or rclone_config.remote_exists(config)
    sources_ready = bool(normalize_sources(config.get("sources", [])))
    repository_ready = bool(config.get("initialized"))
    installed = _install_ready()
    return {
        "restic_password": restic_password_set,
        "onedrive": onedrive_ready,
        "sources": sources_ready,
        "repository": repository_ready,
        "installed": installed,
        "xbar_running": is_xbar_running(),
        "complete": restic_password_set and onedrive_ready and sources_ready and repository_ready and installed,
    }


def _record_repository_change(old_config: dict[str, Any], new_config: dict[str, Any]) -> list[str]:
    old_repo = repository(old_config)
    new_repo = repository(new_config)
    if old_repo == new_repo or not old_config.get("initialized"):
        return []

    history = list(old_config.get("repository_history") or [])
    entry = {
        "repository": old_repo,
        "remote_name": str(old_config.get("remote_name") or ""),
        "repository_path": str(old_config.get("repository_path") or ""),
        "repository_override": str(old_config.get("repository") or ""),
        "saved_at": iso(),
    }
    if not any(item.get("repository") == old_repo for item in history if isinstance(item, dict)):
        history.insert(0, entry)
    new_config["repository_history"] = history[:20]
    new_config["initialized"] = False
    return [
        "Repository location changed. Previous location was saved in Repository History, "
        "and backups are paused until you initialize/probe the new location."
    ]


def _cleanup_rclone_stage(server: "ManagerServer") -> None:
    if server.rclone_stage_path:
        Path(server.rclone_stage_path).unlink(missing_ok=True)
    server.rclone_stage_path = ""
    server.rclone_state = ""


def _begin_rclone_stage(server: "ManagerServer", config: dict[str, Any]) -> dict[str, Any]:
    _cleanup_rclone_stage(server)
    paths.ensure_base_dirs()
    fd, temp_name = tempfile.mkstemp(prefix="rclone-stage-", suffix=".conf", dir=str(paths.state_dir()))
    os.close(fd)
    stage = Path(temp_name)
    stage.unlink(missing_ok=True)
    real = rclone_config_path(config)
    if real.exists():
        shutil.copy2(real, stage)
    else:
        stage.touch(mode=0o600)
    os.chmod(stage, 0o600)
    server.rclone_stage_path = str(stage)
    staged = dict(config)
    staged["rclone_config_path"] = str(stage)
    return staged


def _active_rclone_stage(server: "ManagerServer", config: dict[str, Any]) -> dict[str, Any]:
    if not server.rclone_stage_path or not Path(server.rclone_stage_path).exists():
        raise RuntimeError("OneDrive setup was not started. Click Configure OneDrive again.")
    staged = dict(config)
    staged["rclone_config_path"] = server.rclone_stage_path
    return staged


def _commit_rclone_stage(server: "ManagerServer", config: dict[str, Any]) -> dict[str, Any]:
    if not server.rclone_stage_path:
        raise RuntimeError("OneDrive setup was not started.")
    stage = Path(server.rclone_stage_path)
    if not stage.exists():
        raise RuntimeError("Staged OneDrive configuration is missing.")
    real = rclone_config_path(config)
    real.parent.mkdir(parents=True, exist_ok=True)
    tmp = real.with_name(f".{real.name}.stage")
    shutil.copy2(stage, tmp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, real)
    stage.unlink(missing_ok=True)
    server.rclone_stage_path = ""
    server.rclone_state = ""
    saved = dict(config)
    saved["rclone_configured"] = True
    saved["rclone_config_path"] = str(real)
    return save_config(saved)


class ManagerHandler(BaseHTTPRequestHandler):
    server: "ManagerServer"

    def log_message(self, fmt: str, *args) -> None:
        message = fmt % args
        message = logutil.redact(message)
        message = message.replace(f"token={self.server.token}", "token=REDACTED")
        print(f"[manager] {self.address_string()} - {message}")

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        body = manager_html(self.server.token).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_repository_html(self, initial_path: str) -> None:
        body = repository_browser_html(self.server.token, initial_path).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        token = self.headers.get("X-MacUp-Token") or (query.get("token") or [""])[0]
        return secrets.compare_digest(token, self.server.token)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            if not self._authorized():
                self._send_json({"ok": False, "error": "Unauthorized"}, 403)
                return
            self._send_html()
            return
        if parsed.path == "/repository":
            if not self._authorized():
                self._send_json({"ok": False, "error": "Unauthorized"}, 403)
                return
            query = urllib.parse.parse_qs(parsed.query)
            initial_path = str((query.get("path") or [""])[0])
            try:
                self._send_repository_html(initial_path)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
            return
        if parsed.path.startswith("/api/") and not self._authorized():
            self._send_json({"ok": False, "error": "Unauthorized"}, 403)
            return
        if parsed.path == "/api/config":
            cfg = load_config()
            status = load_status()
            restic_password_set = (
                keychain.find_password(keychain.RESTIC_SERVICE, keychain.RESTIC_ACCOUNT)
                is not None
            )
            self._send_json(
                {
                    "ok": True,
                    "config": cfg,
                    "status": status,
                    "summary": summarize(cfg, status),
                    "restore": load_restore_status(),
                    "setup": _setup_state(cfg, restic_password_set),
                    "doctor": checks(),
                    "restic_password_set": restic_password_set,
                }
            )
            return
        if parsed.path == "/api/status":
            cfg = load_config()
            self._send_json({"ok": True, "status": json.loads(json_output(cfg, load_status()))})
            return
        if parsed.path == "/api/restore/status":
            self._send_json({"ok": True, "restore": load_restore_status()})
            return
        if parsed.path == "/api/log/latest":
            status = load_status()
            latest = Path(str(status.get("latest_log") or ""))
            logs_root = paths.logs_dir().resolve()
            try:
                latest_resolved = latest.expanduser().resolve()
                latest_resolved.relative_to(logs_root)
                if not latest_resolved.is_file():
                    raise ValueError("Latest log path is not a file.")
                content = latest_resolved.read_text(encoding="utf-8", errors="replace")
                tail = "\n".join(content.splitlines()[-120:])
            except Exception as exc:
                tail = f"Log unavailable: {exc}"
            self._send_json({"ok": True, "log": logutil.redact(tail)})
            return
        if parsed.path == "/api/snapshots":
            try:
                cfg = load_config()
                self._send_json({"ok": True, "snapshots": snapshots.list_snapshots(cfg)})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
            return
        if parsed.path == "/api/snapshot":
            query = urllib.parse.parse_qs(parsed.query)
            snapshot_id = str((query.get("id") or [""])[0])
            if not snapshot_id:
                self._send_json({"ok": False, "error": "Snapshot id is required."}, 400)
                return
            try:
                self._send_json({"ok": True, "detail": snapshots.snapshot_detail(load_config(), snapshot_id)})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
            return
        if parsed.path == "/api/snapshot/stats":
            query = urllib.parse.parse_qs(parsed.query)
            snapshot_id = str((query.get("id") or [""])[0])
            if not snapshot_id:
                self._send_json({"ok": False, "error": "Snapshot id is required."}, 400)
                return
            try:
                self._send_json({"ok": True, "stats": snapshots.snapshot_stats(load_config(), snapshot_id)})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
            return
        if parsed.path == "/api/repository/list":
            query = urllib.parse.parse_qs(parsed.query)
            subpath = str((query.get("path") or [""])[0])
            try:
                remote_path, items = rclone_config.list_repository(load_config(), subpath)
                self._send_json({"ok": True, "remote_path": remote_path, "path": subpath, "items": items})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
            return
        if parsed.path == "/api/repository/web-url":
            query = urllib.parse.parse_qs(parsed.query)
            subpath = str((query.get("path") or ["snapshots"])[0])
            try:
                self._send_json({"ok": True, "url": rclone_config.repository_web_url(load_config(), subpath)})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
            return
        self._send_json({"ok": False, "error": "Not found"}, 404)

    def do_POST(self) -> None:
        if not self._authorized():
            self._send_json({"ok": False, "error": "Unauthorized"}, 403)
            return
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = self._read_json()
            if parsed.path == "/api/config":
                previous = load_config()
                cfg = dict(previous)
                cfg.update(body.get("config") or {})
                warnings = _record_repository_change(previous, cfg)
                saved = save_config(cfg)
                self._send_json(
                    {
                        "ok": True,
                        "config": saved,
                        "errors": validate_config(saved, False),
                        "warnings": warnings,
                    }
                )
                return
            if parsed.path == "/api/secrets/restic":
                password = str(body.get("password") or "")
                if len(password) < 8:
                    raise ValueError("Restic password must be at least 8 characters.")
                keychain.store_password(keychain.RESTIC_SERVICE, keychain.RESTIC_ACCOUNT, password)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/folders/pick":
                self._send_json({"ok": True, "folders": pick_folders()})
                return
            if parsed.path == "/api/restore/pick":
                self._send_json({"ok": True, "folder": pick_restore_destination()})
                return
            if parsed.path == "/api/rclone/start":
                cfg = load_config()
                staged = _begin_rclone_stage(self.server, cfg)
                question = rclone_config.start_onedrive_flow(staged)
                self.server.rclone_state = question.state
                if question.complete:
                    _commit_rclone_stage(self.server, cfg)
                self._send_json({"ok": not bool(question.error), "question": _question_payload(question)})
                return
            if parsed.path == "/api/rclone/answer":
                answer = str(body.get("answer") or "")
                state = str(body.get("state") or self.server.rclone_state or "")
                cfg = load_config()
                staged = _active_rclone_stage(self.server, cfg)
                question = rclone_config.continue_flow(staged, state, answer)
                self.server.rclone_state = question.state
                if question.complete:
                    _commit_rclone_stage(self.server, cfg)
                self._send_json({"ok": not bool(question.error), "question": _question_payload(question)})
                return
            if parsed.path == "/api/rclone/test":
                ok, output = rclone_config.test_remote(load_config())
                if ok:
                    cfg = load_config()
                    cfg["rclone_configured"] = True
                    save_config(cfg)
                self._send_json({"ok": ok, "output": output})
                return
            if parsed.path == "/api/repo/init":
                cfg = load_config()
                with RunLogger(f"init-{run_id()}") as logger:
                    ensure_repository(cfg, logger)
                cfg["initialized"] = True
                save_config(cfg)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/install":
                self._send_json({"ok": True, **install_all(load=bool(body.get("load", True)))})
                return
            if parsed.path == "/api/full-disk-access":
                opened, message = open_full_disk_access_settings()
                self._send_json({"ok": True, "opened": opened, "message": message})
                return
            if parsed.path == "/api/backup-now":
                detach_backup(str(paths.cli_path()))
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/snapshot/restore":
                snapshot_id = str(body.get("snapshot") or "")
                parent = str(body.get("parent") or "")
                if not snapshot_id:
                    raise ValueError("Snapshot id is required.")
                if not parent:
                    raise ValueError("Restore destination is required.")
                available = snapshots.list_snapshots(load_config())
                if not any(
                    snapshot.get("id") == snapshot_id or snapshot.get("short_id") == snapshot_id
                    for snapshot in available
                ):
                    raise ValueError("Snapshot not found.")
                detach_restore(str(paths.cli_path()), snapshot=snapshot_id, parent=parent)
                self._send_json({"ok": True, "message": "Restore started."})
                return
            if parsed.path == "/api/reset":
                result = reset_local_state(str(body.get("confirmation") or ""))
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                self._send_json({"ok": True, "reset": result})
                return
            if parsed.path == "/api/shutdown":
                manager_state.clear(self.server.token)
                refresh_xbar()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                self._send_json({"ok": True})
                return
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
            return
        self._send_json({"ok": False, "error": "Not found"}, 404)


class ManagerServer(ThreadingHTTPServer):
    def __init__(self, address, handler, token: str):
        super().__init__(address, handler)
        self.token = token
        self.rclone_state = ""
        self.rclone_stage_path = ""


def run_manager(port: int = 0, open_browser: bool = True) -> int:
    existing = manager_state.probe()
    if existing.get("running"):
        url = str(existing.get("url") or "")
        print(f"MacUp manager already running at {url}")
        if open_browser and url:
            webbrowser.open(url)
        return 0

    token = secrets.token_urlsafe(24)
    server = ManagerServer(("127.0.0.1", port), ManagerHandler, token)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/?token={urllib.parse.quote(token)}"
    manager_state.write_running(actual_port, token, url)
    print(f"MacUp manager running at {url}")
    print("Press Ctrl-C to stop it.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _cleanup_rclone_stage(server)
        manager_state.clear(token)
    return 0


def stop_running_manager() -> int:
    ok, message = manager_state.stop()
    print(message)
    return 0 if ok else 1


def detach_manager(cli: str | None = None) -> int:
    existing = manager_state.probe()
    if existing.get("running"):
        url = str(existing.get("url") or "")
        if url:
            webbrowser.open(url)
        print("Manager already running.")
        return 0

    cli_path = cli or str(paths.cli_path())
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.Popen(
        [cli_path, "manager"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=env,
    )
    print("Manager started.")
    return 0


def manager_html(token: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MacUp</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #20242a;
      --muted: #667085;
      --line: #d8dee8;
      --accent: #2563eb;
      --good: #2da44e;
      --warn: #fb8c00;
      --bad: #d1242f;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{ --bg: #111418; --panel: #191d23; --text: #f2f4f7; --muted: #a3acba; --line: #303742; }}
    }}
    * {{ box-sizing: border-box; }}
    html {{ overflow-x: hidden; }}
    body {{ margin: 0; max-width: 100%; overflow-x: hidden; font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    header {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 18px 24px; border-bottom: 1px solid var(--line); background: var(--panel); position: sticky; top: 0; z-index: 2; }}
    h1 {{ margin: 0; font-size: 20px; letter-spacing: 0; }}
    main {{ width: min(760px, 100%); min-width: 0; margin: 0 auto; padding: 16px; display: grid; gap: 14px; }}
    section {{ min-width: 0; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    h2 {{ margin: 0 0 14px; font-size: 15px; }}
    label {{ display: grid; gap: 5px; min-width: 0; color: var(--muted); font-size: 12px; }}
    input, select {{ width: 100%; min-width: 0; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; background: transparent; color: var(--text); font: inherit; }}
    button {{ border: 1px solid var(--line); background: var(--panel); color: var(--text); padding: 8px 11px; border-radius: 6px; font: inherit; cursor: pointer; }}
    button:disabled {{ opacity: .62; cursor: wait; }}
    button.primary {{ background: var(--accent); border-color: var(--accent); color: white; }}
    button.danger {{ border-color: var(--bad); color: var(--bad); }}
    .hidden {{ display: none !important; }}
    .warning {{ border: 1px solid var(--warn); color: var(--warn); border-radius: 6px; padding: 10px; margin-top: 12px; }}
    .danger-zone {{ border-top: 1px solid var(--line); margin-top: 14px; padding-top: 14px; display: grid; gap: 10px; }}
    .setup-step {{ display: grid; gap: 8px; }}
    .setup-step strong {{ font-size: 15px; }}
    details {{ border: 1px solid var(--line); border-radius: 6px; padding: 10px; }}
    summary {{ cursor: pointer; font-weight: 650; }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 12px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .status-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    .metric {{ min-width: 0; border: 1px solid var(--line); border-radius: 6px; padding: 10px; min-height: 64px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 14px; overflow-wrap: anywhere; }}
    .sources {{ display: grid; gap: 8px; margin-bottom: 12px; }}
    .source {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: center; padding: 8px; border: 1px solid var(--line); border-radius: 6px; }}
    .muted {{ color: var(--muted); }}
    .status {{ min-width: 0; display: flex; gap: 10px; align-items: center; }}
    #statusText {{ overflow-wrap: anywhere; }}
    .dot {{ width: 12px; height: 12px; border-radius: 50%; background: var(--bad); display: inline-block; }}
    .spinner {{ width: 13px; height: 13px; border-radius: 50%; border: 2px solid var(--line); border-top-color: var(--accent); animation: spin .8s linear infinite; display: none; }}
    .busy .spinner {{ display: inline-block; }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    .snapshot-list {{ display: grid; gap: 10px; }}
    .snapshot-card {{ border: 1px solid var(--line); border-radius: 6px; padding: 10px; display: grid; gap: 8px; }}
    .snapshot-main {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: start; }}
    .snapshot-title {{ display: grid; gap: 3px; min-width: 0; }}
    .snapshot-title strong, .snapshot-detail strong {{ overflow-wrap: anywhere; }}
    .snapshot-actions {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    .choice-grid {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .recommended-choice {{ display: flex; justify-content: center; margin: 12px 0; width: 100%; }}
    .recommended-choice button {{
      width: min(520px, 100%);
      border-color: #06b6d4;
      background: rgba(6, 182, 212, .16);
      color: var(--text);
      font-weight: 700;
      text-align: center;
    }}
    .recommended-badge {{ display: block; color: #06b6d4; font-size: 11px; font-weight: 750; margin-bottom: 2px; text-transform: uppercase; }}
    .choice-label {{ display: block; overflow-wrap: anywhere; }}
    .repo-history {{ display: grid; gap: 8px; margin-top: 12px; }}
    .log {{ min-width: 0; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; border: 1px solid var(--line); border-radius: 6px; padding: 10px; max-height: 280px; overflow: auto; color: var(--muted); }}
    @media (max-width: 720px) {{ .status-grid {{ grid-template-columns: 1fr; }} header {{ align-items: flex-start; gap: 10px; flex-direction: column; }} }}
  </style>
</head>
<body>
  <header>
    <h1>MacUp</h1>
    <div class="status"><span id="dot" class="dot"></span><span id="statusText">Loading</span></div>
  </header>
  <main>
    <section id="setupFlow" class="hidden">
      <h2>Setup</h2>
      <div class="setup-step">
        <strong id="setupTitle">Loading</strong>
        <span id="setupText" class="muted"></span>
      </div>
    </section>

    <section id="statusSection" data-normal>
      <h2>Current Status</h2>
      <div class="status-grid">
        <div class="metric"><span>State</span><strong id="metricState">Loading</strong></div>
        <div class="metric"><span>Last backup</span><strong id="metricLast">Never</strong></div>
        <div class="metric"><span>Next backup</span><strong id="metricNext">Unknown</strong></div>
        <div class="metric"><span>Latest log</span><strong id="metricLog">None</strong></div>
      </div>
    </section>

    <section id="sourceSection" data-normal>
      <h2>Sources</h2>
      <div id="sources" class="sources"></div>
      <div class="actions">
        <button id="pickFolders">Add Folders</button>
        <button id="saveSources" class="primary">Save</button>
      </div>
    </section>

    <section id="backupRulesSection" data-normal>
      <h2>Backup Rules</h2>
      <div class="grid">
        <label>Interval hours<input id="backup_interval_hours" type="number" min="1" step="1"></label>
        <label>Snapshots to keep<input id="retention_count" type="number" min="1" step="1"></label>
        <label>Log retention days<input id="log_retention_days" type="number" min="1" step="1"></label>
        <label>Path mode<select id="path_mode"><option value="preserve">Preserve full path</option><option value="flat">Flat folder names</option></select></label>
        <label>Remote name<input id="remote_name"></label>
        <label>Repository path<input id="repository_path"></label>
        <label>Upload limit<input id="upload_limit" placeholder="Optional, for example 1M"></label>
      </div>
      <div id="repositoryWarning" class="warning hidden"></div>
      <div class="actions" style="margin-top:12px"><button id="saveConfig" class="primary">Save Rules</button></div>
    </section>

    <section id="secretsSection" data-normal>
      <h2>Secrets</h2>
      <p class="muted">If you reset MacUp and want to reconnect to existing OneDrive backups, enter the same Restic password you used before. A new password only works with a new empty repository path.</p>
      <div class="grid">
        <label>Restic password<input id="restic_password" type="password" autocomplete="new-password"></label>
        <label>Confirm password<input id="restic_password_confirm" type="password" autocomplete="new-password"></label>
      </div>
      <div class="actions" style="margin-top:12px"><button id="savePassword" class="primary">Save Password</button><span id="passwordState" class="muted"></span></div>
    </section>

    <section id="onedriveSection" data-normal>
      <h2>OneDrive</h2>
      <div class="actions">
        <button id="rcloneStart">Configure OneDrive</button>
        <button id="rcloneTest">Test Remote</button>
        <button id="openRepository">Open Snapshots Folder</button>
      </div>
      <div id="rcloneQuestion" style="margin-top:12px"></div>
    </section>

    <section id="snapshotsSection" data-normal>
      <h2>Snapshots</h2>
      <p class="muted">Details appear under each snapshot. Restore size is calculated from Restic metadata; on OneDrive it can be slow, but it does not restore the snapshot contents.</p>
      <div class="actions" style="margin-bottom:12px"><button id="refreshSnapshots">Refresh Snapshots</button></div>
      <div id="restoreStatus" class="warning hidden"></div>
      <div id="snapshotList" class="snapshot-list muted">No snapshots loaded.</div>
    </section>

    <section id="outputSection">
      <h2>Output</h2>
      <div id="output" class="log">Ready.</div>
    </section>

    <section id="latestLogSection" data-normal>
      <h2>Latest Backup Log</h2>
      <div id="liveLog" class="log">No log yet.</div>
    </section>

    <section id="advancedSection" data-normal>
      <h2>Advanced Settings</h2>
      <details id="advancedDetails">
        <summary>Repository, scheduler, and permissions</summary>
        <p class="muted">Repository and scheduler actions are normally only needed during setup or after changing the backup location. Full Disk Access is only needed if macOS blocks protected folders such as Documents, Desktop, Downloads, Mail, or Photos.</p>
        <div class="actions">
          <button id="repoInit">Initialize Repository</button>
          <button id="installAll">Install Scheduler and Xbar</button>
          <button id="backupNow" class="primary">Backup Now</button>
          <button id="fullDiskAccess">Open Full Disk Access Settings</button>
          <button id="shutdown">Stop Manager</button>
        </div>
        <div id="repoHistory" class="repo-history"></div>
        <div class="danger-zone">
          <button id="resetShow" class="danger">Reset MacUp</button>
          <div id="resetPanel" class="hidden">
            <div class="warning">This removes local MacUp settings, Keychain secrets, scheduler, and Xbar plugin. It does not delete backups stored in OneDrive.</div>
            <label style="margin-top:10px">Type {CONFIRMATION_TEXT}<input id="resetConfirmation" autocomplete="off"></label>
            <div class="actions" style="margin-top:10px">
              <button id="resetConfirm" class="danger" disabled>Confirm Reset</button>
              <button id="resetCancel">Cancel</button>
            </div>
          </div>
        </div>
      </details>
    </section>
  </main>
  <script>
    const token = {json.dumps(token)};
    let cfg = null;
    let lastStatus = null;
    let lastRestore = null;
    let pollTimer = null;
    let restorePollTimer = null;
    let snapshotsLoaded = false;
    const resetConfirmationText = {json.dumps(CONFIRMATION_TEXT)};
    const out = document.getElementById('output');
    const liveLog = document.getElementById('liveLog');
    function log(text) {{ out.textContent = String(text); }}
    function closeManagerPage(message) {{
      document.body.innerHTML = '<main><section><h2>' + escapeHtml(message) + '</h2><p class="muted">This manager server is closed.</p></section></main>';
      setTimeout(() => window.close(), 120);
    }}
    function setBusy(button, busy) {{
      if (!button) return;
      if (busy) {{
        button.dataset.busy = 'true';
        button.dataset.originalText = button.textContent;
        button.textContent = 'Working...';
        button.disabled = true;
      }} else {{
        button.dataset.busy = 'false';
        button.textContent = button.dataset.originalText || button.textContent;
        button.disabled = false;
      }}
    }}
    async function api(path, opts = {{}}) {{
      const res = await fetch(path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token), {{
        method: opts.method || 'GET',
        headers: {{ 'Content-Type': 'application/json', 'X-MacUp-Token': token }},
        body: opts.body ? JSON.stringify(opts.body) : undefined
      }});
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Request failed');
      return data;
    }}
    async function runAction(action, button) {{
      setBusy(button, true);
      try {{
        await action();
      }} catch (err) {{
        log('Error: ' + (err && err.message ? err.message : String(err)));
      }} finally {{
        setBusy(button, false);
        syncSnapshotDownloadButtons();
        if (button && button.id === 'backupNow' && lastStatus && lastStatus.running) {{
          button.disabled = true;
        }}
      }}
    }}
    window.addEventListener('unhandledrejection', event => {{
      log('Error: ' + (event.reason && event.reason.message ? event.reason.message : String(event.reason)));
      event.preventDefault();
    }});
    function setVisible(id, visible) {{
      document.getElementById(id).classList.toggle('hidden', !visible);
    }}
    function currentSetupStep(data) {{
      const setup = data.setup || {{}};
      if (!setup.restic_password) return {{
        title: 'Step 1 of 5: Save the encryption password',
        text: 'Choose the Restic password first. If reconnecting after reset, use the original password for the existing OneDrive repository.',
        sections: ['secretsSection']
      }};
      if (!setup.onedrive) return {{
        title: 'Step 2 of 5: Connect OneDrive',
        text: 'Configure OneDrive in the browser, then choose the drive rclone should use.',
        sections: ['onedriveSection']
      }};
      if (!setup.sources) return {{
        title: 'Step 3 of 5: Choose folders',
        text: 'Add the folders that should be backed up, then save them.',
        sections: ['sourceSection']
      }};
      if (!setup.repository) return {{
        title: 'Step 4 of 5: Initialize the repository',
        text: 'Confirm the repository location, then initialize or probe it before backups can run.',
        sections: ['backupRulesSection', 'advancedSection']
      }};
      if (!setup.installed) return {{
        title: 'Step 5 of 5: Install scheduling and Xbar',
        text: 'Install the LaunchAgent scheduler and Xbar menu item so backups and status keep working.',
        sections: ['advancedSection']
      }};
      return {{
        title: 'Setup complete',
        text: 'MacUp is ready.',
        sections: []
      }};
    }}
    function renderSetup(data) {{
      const complete = Boolean(data.setup && data.setup.complete);
      const normalSections = [...document.querySelectorAll('[data-normal]')].map(section => section.id);
      setVisible('setupFlow', !complete);
      if (complete) {{
        normalSections.forEach(id => setVisible(id, true));
        setVisible('outputSection', true);
        return;
      }}
      const step = currentSetupStep(data);
      document.getElementById('setupTitle').textContent = step.title;
      document.getElementById('setupText').textContent = step.text;
      normalSections.forEach(id => setVisible(id, step.sections.includes(id)));
      setVisible('outputSection', true);
      document.getElementById('advancedDetails').open = step.sections.includes('advancedSection');
    }}
    function readConfig() {{
      return {{
        sources: [...document.querySelectorAll('.source input')].map(i => i.value).filter(Boolean),
        backup_interval_hours: Number(document.getElementById('backup_interval_hours').value || 24),
        retention_count: Number(document.getElementById('retention_count').value || 14),
        log_retention_days: Number(document.getElementById('log_retention_days').value || 14),
        path_mode: document.getElementById('path_mode').value,
        remote_name: document.getElementById('remote_name').value,
        repository_path: document.getElementById('repository_path').value,
        upload_limit: document.getElementById('upload_limit').value
      }};
    }}
    function renderSources(sources) {{
      const box = document.getElementById('sources');
      box.innerHTML = '';
      sources.forEach(path => {{
        const row = document.createElement('div');
        row.className = 'source';
        const input = document.createElement('input');
        input.value = path;
        const remove = document.createElement('button');
        remove.textContent = 'Remove';
        remove.className = 'danger';
        remove.onclick = () => row.remove();
        row.append(input, remove);
        box.append(row);
      }});
    }}
    function renderRepositoryHistory(history) {{
      const box = document.getElementById('repoHistory');
      box.innerHTML = '';
      const items = Array.isArray(history) ? history : [];
      if (!items.length) return;
      const title = document.createElement('strong');
      title.textContent = 'Repository History';
      box.append(title);
      items.forEach(item => {{
        const row = document.createElement('div');
        row.className = 'source';
        const text = document.createElement('div');
        text.innerHTML = '<strong>' + escapeHtml(item.repository || 'Unknown repository') + '</strong><br><span class="muted">Saved when the repository location changed.</span>';
        const use = document.createElement('button');
        use.textContent = 'Use';
        use.onclick = () => {{
          document.getElementById('remote_name').value = item.remote_name || '';
          document.getElementById('repository_path').value = item.repository_path || '';
          log('Loaded previous repository fields. Click Save Rules, then initialize/probe it.');
        }};
        row.append(text, use);
        box.append(row);
      }});
    }}
    function renderRepositoryWarning(data) {{
      const warning = document.getElementById('repositoryWarning');
      const messages = [];
      if (data.config && !data.config.initialized) {{
        messages.push('Backups are paused until this repository location is initialized or probed.');
        messages.push('If this OneDrive path already contains backups from before a reset, it can only be reopened with the original Restic password.');
      }}
      warning.textContent = messages.join('\\n');
      warning.classList.toggle('hidden', messages.length === 0);
    }}
    function renderRestoreStatus(restore) {{
      const box = document.getElementById('restoreStatus');
      if (!restore || !restore.state || restore.state === 'idle') {{
        box.classList.add('hidden');
        box.textContent = '';
        return;
      }}
      const parts = [];
      if (restore.state === 'running') parts.push('Restore running');
      if (restore.state === 'success') parts.push('Last restore finished');
      if (restore.state === 'failed') parts.push('Last restore failed');
      if (restore.snapshot) parts.push('snapshot ' + String(restore.snapshot).slice(0, 8));
      if (restore.target) parts.push('saved to ' + restore.target);
      if (restore.last_error) parts.push(restore.last_error);
      box.textContent = parts.join(' - ');
      box.classList.toggle('hidden', parts.length === 0);
    }}
    function syncSnapshotDownloadButtons() {{
      const restoreRunning = Boolean(lastRestore && lastRestore.state === 'running');
      document.querySelectorAll('.download-snapshot').forEach(button => {{
        if (button.dataset.busy !== 'true') button.disabled = restoreRunning;
      }});
    }}
    function render(data) {{
      cfg = data.config;
      lastStatus = data.summary;
      lastRestore = data.restore || null;
      renderSources(cfg.sources || []);
      ['backup_interval_hours','retention_count','log_retention_days','path_mode','remote_name','repository_path','upload_limit'].forEach(id => {{
        document.getElementById(id).value = cfg[id] || '';
      }});
      document.getElementById('dot').style.background = data.summary.color;
      document.getElementById('statusText').textContent = data.summary.label + ' - last backup: ' + data.summary.last_backup_relative;
      document.getElementById('passwordState').textContent = data.restic_password_set ? 'Stored in Keychain' : 'Not stored';
      document.getElementById('metricState').textContent = data.summary.label;
      document.getElementById('metricLast').textContent = data.summary.last_backup_relative;
      document.getElementById('metricNext').textContent = data.summary.next_backup_relative;
      document.getElementById('metricLog').textContent = data.summary.latest_log ? data.summary.latest_log.split('/').pop() : 'None';
      document.getElementById('backupNow').disabled = Boolean(data.summary.running);
      document.getElementById('repoInit').textContent = cfg.initialized ? 'Reinitialize / Probe Repository' : 'Initialize Repository';
      document.getElementById('installAll').textContent = data.setup && data.setup.installed ? 'Reinstall Scheduler and Xbar' : 'Install Scheduler and Xbar';
      renderRepositoryHistory(cfg.repository_history || []);
      renderRepositoryWarning(data);
      renderRestoreStatus(data.restore);
      renderSetup(data);
      syncSnapshotDownloadButtons();
      if (data.restore && data.restore.state === 'running') startRestorePolling();
    }}
    async function refreshLog() {{
      const data = await api('/api/log/latest');
      liveLog.textContent = data.log || 'No log yet.';
      liveLog.scrollTop = liveLog.scrollHeight;
    }}
    async function refresh() {{
      const data = await api('/api/config');
      render(data);
      if (data.summary.latest_log) await refreshLog();
      if (data.setup && data.setup.complete && !snapshotsLoaded) loadSnapshots().catch(err => log('Snapshot error: ' + err.message));
      if (data.summary.running) startPolling();
      return data;
    }}
    function startPolling() {{
      if (pollTimer) return;
      pollTimer = setInterval(async () => {{
        try {{
          const data = await refresh();
          if (!data.summary.running) {{
            clearInterval(pollTimer);
            pollTimer = null;
            log(data.summary.failed ? 'Backup failed. Check the latest log.' : 'Backup finished.');
          }}
        }} catch (err) {{
          log('Error: ' + err.message);
        }}
      }}, 2500);
    }}
    function startRestorePolling() {{
      if (restorePollTimer) return;
      restorePollTimer = setInterval(async () => {{
        try {{
          const data = await refresh();
          if (!data.restore || data.restore.state !== 'running') {{
            clearInterval(restorePollTimer);
            restorePollTimer = null;
            if (data.restore && data.restore.state === 'success') log('Restore finished: ' + (data.restore.target || 'target folder recorded in restore log.'));
            if (data.restore && data.restore.state === 'failed') log('Restore failed: ' + (data.restore.last_error || 'check restore log.'));
          }}
        }} catch (err) {{
          log('Error: ' + err.message);
        }}
      }}, 2500);
    }}
    async function save() {{
      const data = await api('/api/config', {{method:'POST', body:{{config: readConfig()}}}});
      const full = await api('/api/config');
      render(full);
      const warnings = data.warnings && data.warnings.length ? '\\n' + data.warnings.join('\\n') : '';
      log('Saved.' + warnings);
    }}
    function cleanQuestionName(name) {{
      const labels = {{
        config_is_local: 'Sign in method',
        config_type: 'Type of connection',
        config_driveid: 'Choose OneDrive drive',
        config_drive_type: 'Choose OneDrive type'
      }};
      return labels[name] || String(name || 'Question').replace(/^config_/, '').replaceAll('_', ' ');
    }}
    function optionLabel(ex) {{
      const label = (ex.Value || '') + (ex.Help ? ' - ' + ex.Help : '');
      return label;
    }}
    function isRecommendedOption(optionName, ex) {{
      const name = String(optionName || '').toLowerCase();
      const value = String(ex.Value || '').toLowerCase();
      const help = String(ex.Help || '').toLowerCase();
      const combined = (value + ' ' + help).trim();
      if (name === 'config_type') return value === 'onedrive';
      if (name === 'config_driveid') {{
        return combined.includes('onedrive') && combined.includes('business') && !combined.includes('personalcachelibrary');
      }}
      if (name === 'config_drive_type') return combined.includes('business');
      return false;
    }}
    function makeQuestionButton(q, ex, recommended = false) {{
      const b = document.createElement('button');
      if (recommended) {{
        const badge = document.createElement('span');
        badge.className = 'recommended-badge';
        badge.textContent = 'Recommended';
        const label = document.createElement('span');
        label.className = 'choice-label';
        label.textContent = optionLabel(ex);
        b.append(badge, label);
      }} else {{
        b.textContent = optionLabel(ex);
      }}
      if (recommended) b.className = 'primary';
      b.onclick = () => runAction(async () => {{
        const data = await api('/api/rclone/answer', {{method:'POST', body:{{state:q.state, answer:String(ex.Value)}}}});
        renderQuestion(data.question);
      }}, b);
      return b;
    }}
    function renderQuestion(q) {{
      const box = document.getElementById('rcloneQuestion');
      box.innerHTML = '';
      if (q.complete) {{ box.textContent = 'OneDrive configuration complete.'; refresh(); return; }}
      if (q.error) {{ box.textContent = q.error; return; }}
      const title = document.createElement('div');
      title.textContent = cleanQuestionName(q.option.Name);
      const help = document.createElement('pre');
      help.className = 'log';
      help.textContent = q.option.Name === 'config_is_local'
        ? 'Use browser sign-in on this Mac unless rclone asks you to authenticate from another machine.'
        : (q.option.Help || '');
      box.append(title, help);
      const examples = q.option.Examples || [];
      const recommended = examples.find(ex => isRecommendedOption(q.option.Name, ex));
      if (recommended) {{
        const row = document.createElement('div');
        row.className = 'recommended-choice';
        row.append(makeQuestionButton(q, recommended, true));
        box.append(row);
      }}
      const grid = document.createElement('div');
      grid.className = 'choice-grid';
      examples.filter(ex => ex !== recommended).forEach(ex => grid.append(makeQuestionButton(q, ex, false)));
      if (grid.children.length) box.append(grid);
      if (!q.option.Exclusive) {{
        const input = document.createElement(q.option.IsPassword ? 'input' : 'input');
        input.type = q.option.IsPassword ? 'password' : 'text';
        input.value = q.option.Default || '';
        const b = document.createElement('button');
        b.textContent = 'Submit';
        b.onclick = () => runAction(async () => {{
          const data = await api('/api/rclone/answer', {{method:'POST', body:{{state:q.state, answer:input.value}}}});
          renderQuestion(data.question);
        }}, b);
        box.append(input, b);
      }}
    }}
    function showRcloneStartPrompt(button) {{
      const box = document.getElementById('rcloneQuestion');
      box.innerHTML = '';
      const message = document.createElement('div');
      message.className = 'warning';
      message.textContent = 'This starts a new OneDrive setup in a temporary config. Your current OneDrive connection is not replaced unless setup completes.';
      const actions = document.createElement('div');
      actions.className = 'actions';
      actions.style.marginTop = '10px';
      const start = document.createElement('button');
      start.className = 'primary';
      start.textContent = 'Start Setup';
      const cancel = document.createElement('button');
      cancel.textContent = 'Cancel';
      start.onclick = () => runAction(async () => {{
        const data = await api('/api/rclone/start', {{method:'POST', body:{{}}}});
        renderQuestion(data.question);
      }}, start);
      cancel.onclick = () => {{
        box.innerHTML = '';
        log('OneDrive setup was not changed.');
      }};
      actions.append(start, cancel);
      box.append(message, actions);
    }}
    function updateResetConfirmState() {{
      const input = document.getElementById('resetConfirmation');
      document.getElementById('resetConfirm').disabled = input.value !== resetConfirmationText;
    }}
    function escapeHtml(value) {{
      return String(value || '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    }}
    function snapshotSummary(snapshot) {{
      const paths = Array.isArray(snapshot.paths) ? snapshot.paths.join(', ') : '';
      return (snapshot.when || 'unknown time') + (paths ? ' - ' + paths : '');
    }}
    function snapshotBaseDetail(snapshot) {{
      const paths = Array.isArray(snapshot.paths) ? snapshot.paths.join('\\n') : '';
      const tags = Array.isArray(snapshot.tags) ? snapshot.tags.join(', ') : '';
      return [
        'Snapshot: ' + (snapshot.short_id || snapshot.id || ''),
        'Backed up: ' + (snapshot.when || 'unknown'),
        snapshot.hostname ? 'Host: ' + snapshot.hostname : '',
        tags ? 'Tags: ' + tags : '',
        paths ? 'Paths:\\n' + paths : ''
      ].filter(Boolean).join('\\n');
    }}
    function renderSnapshots(items) {{
      const box = document.getElementById('snapshotList');
      box.innerHTML = '';
      box.classList.toggle('muted', !items.length);
      if (!items.length) {{
        box.textContent = 'No MacUp snapshots found.';
        return;
      }}
      items.forEach(snapshot => {{
        const card = document.createElement('div');
        card.className = 'snapshot-card';
        const main = document.createElement('div');
        main.className = 'snapshot-main';
        const title = document.createElement('div');
        title.className = 'snapshot-title';
        const id = snapshot.short_id || snapshot.id || '';
        title.innerHTML = '<strong>' + escapeHtml(id) + '</strong><span class="muted">' + escapeHtml(snapshotSummary(snapshot)) + '</span>';
        const actions = document.createElement('div');
        actions.className = 'snapshot-actions';
        const details = document.createElement('button');
        details.textContent = 'Details';
        const download = document.createElement('button');
        download.textContent = 'Download';
        download.className = 'primary download-snapshot';
        download.disabled = Boolean(lastRestore && lastRestore.state === 'running');
        const detailBox = document.createElement('div');
        detailBox.className = 'snapshot-detail log hidden';
        details.onclick = () => runAction(async () => showSnapshot(snapshot, detailBox), details);
        download.onclick = () => runAction(async () => downloadSnapshot(snapshot), download);
        actions.append(details, download);
        main.append(title, actions);
        card.append(main, detailBox);
        box.append(card);
      }});
    }}
    async function loadSnapshots() {{
      const box = document.getElementById('snapshotList');
      box.classList.add('muted');
      box.textContent = 'Loading snapshots...';
      try {{
        const data = await api('/api/snapshots');
        snapshotsLoaded = true;
        renderSnapshots(data.snapshots || []);
      }} catch (err) {{
        snapshotsLoaded = false;
        box.classList.add('muted');
        box.textContent = 'Snapshot list unavailable: ' + (err && err.message ? err.message : String(err));
        throw err;
      }}
    }}
    async function showSnapshot(snapshot, box) {{
      box.classList.remove('hidden');
      box.textContent = snapshotBaseDetail(snapshot) + '\\n\\nRestore size: calculating metadata...';
      const id = snapshot.id || snapshot.short_id;
      let data;
      try {{
        data = await api('/api/snapshot/stats?id=' + encodeURIComponent(id));
      }} catch (err) {{
        box.textContent = snapshotBaseDetail(snapshot) + '\\n\\nRestore size unavailable: ' + (err && err.message ? err.message : String(err));
        return;
      }}
      const stats = data.stats || {{}};
      const lines = [
        snapshotBaseDetail(snapshot),
        '',
        'Restore size: ' + (stats.restore_size_display || 'unknown') + (stats.cached ? ' (cached)' : ''),
        stats.file_count ? 'Files: ' + stats.file_count : '',
        stats.stats_error ? 'Stats error: ' + stats.stats_error : ''
      ].filter(line => line !== null && line !== undefined);
      box.textContent = lines.join('\\n');
    }}
    async function downloadSnapshot(snapshot) {{
      const id = snapshot.id || snapshot.short_id;
      const picked = await api('/api/restore/pick', {{method:'POST', body:{{}}}});
      if (!picked.folder) throw new Error('No restore destination selected.');
      await api('/api/snapshot/restore', {{method:'POST', body:{{snapshot:id, parent:picked.folder}}}});
      log('Restore started. MacUp will restore into a new folder inside: ' + picked.folder);
      await refresh();
      startRestorePolling();
    }}
    document.getElementById('pickFolders').onclick = event => runAction(async () => {{
      const data = await api('/api/folders/pick', {{method:'POST', body:{{}}}});
      renderSources([...(readConfig().sources || []), ...data.folders]);
    }}, event.currentTarget);
    document.getElementById('saveSources').onclick = event => runAction(save, event.currentTarget);
    document.getElementById('saveConfig').onclick = event => runAction(save, event.currentTarget);
    document.getElementById('savePassword').onclick = event => runAction(async () => {{
      const p = document.getElementById('restic_password').value;
      const c = document.getElementById('restic_password_confirm').value;
      if (p !== c) throw new Error('Passwords do not match');
      await api('/api/secrets/restic', {{method:'POST', body:{{password:p}}}});
      document.getElementById('restic_password').value = '';
      document.getElementById('restic_password_confirm').value = '';
      await refresh();
      log('Password stored in Keychain.');
    }}, event.currentTarget);
    document.getElementById('rcloneStart').onclick = event => showRcloneStartPrompt(event.currentTarget);
    document.getElementById('rcloneTest').onclick = event => runAction(async () => log((await api('/api/rclone/test', {{method:'POST', body:{{}}}})).output || 'Remote test OK.'), event.currentTarget);
    document.getElementById('openRepository').onclick = event => {{
      const opened = window.open('about:blank', '_blank');
      if (opened) opened.opener = null;
      runAction(async () => {{
        try {{
          const data = await api('/api/repository/web-url?path=snapshots');
          if (opened) {{
            opened.location = data.url;
          }} else {{
            window.open(data.url, '_blank', 'noopener');
          }}
          log('Opened the official OneDrive snapshots folder in a new tab.');
        }} catch (err) {{
          if (opened) opened.close();
          throw err;
        }}
      }}, event.currentTarget);
    }};
    document.getElementById('repoInit').onclick = event => runAction(async () => {{ await api('/api/repo/init', {{method:'POST', body:{{}}}}); await refresh(); log('Repository initialized.'); }}, event.currentTarget);
    document.getElementById('installAll').onclick = event => runAction(async () => {{
      const data = await api('/api/install', {{method:'POST', body:{{load:true}}}});
      await refresh();
      log([
        'Installed runtime: ' + data.runtime_cli,
        'Installed Xbar plugin: ' + data.xbar_plugin,
        'Installed LaunchAgent: ' + data.launch_agent,
        data.xbar_launched ? 'Xbar launched: ' + data.xbar_message : 'Xbar launch issue: ' + data.xbar_message
      ].join('\\n'));
    }}, event.currentTarget);
    document.getElementById('backupNow').onclick = event => runAction(async () => {{
      await api('/api/backup-now', {{method:'POST', body:{{}}}});
      log('Backup started. Watching live status and log.');
      startPolling();
      await refresh();
    }}, event.currentTarget);
    document.getElementById('fullDiskAccess').onclick = event => runAction(async () => {{
      const data = await api('/api/full-disk-access', {{method:'POST', body:{{}}}});
      log(data.message);
    }}, event.currentTarget);
    document.getElementById('refreshSnapshots').onclick = event => runAction(async () => {{ snapshotsLoaded = false; await loadSnapshots(); log('Snapshots refreshed.'); }}, event.currentTarget);
    document.getElementById('shutdown').onclick = event => runAction(async () => {{
      await api('/api/shutdown', {{method:'POST', body:{{}}}});
      closeManagerPage('Manager stopped');
    }}, event.currentTarget);
    document.getElementById('resetShow').onclick = () => {{
      document.getElementById('resetPanel').classList.remove('hidden');
      document.getElementById('resetConfirmation').focus();
      updateResetConfirmState();
    }};
    document.getElementById('resetCancel').onclick = () => {{
      document.getElementById('resetConfirmation').value = '';
      document.getElementById('resetPanel').classList.add('hidden');
      updateResetConfirmState();
    }};
    document.getElementById('resetConfirmation').oninput = updateResetConfirmState;
    document.getElementById('resetConfirmation').onkeydown = event => {{
      if (event.key === 'Enter' && event.currentTarget.value === resetConfirmationText) {{
        document.getElementById('resetConfirm').click();
      }}
    }};
    document.getElementById('resetConfirm').onclick = event => runAction(async () => {{
      const confirmation = document.getElementById('resetConfirmation').value;
      await api('/api/reset', {{method:'POST', body:{{confirmation}}}});
      closeManagerPage('MacUp reset complete');
    }}, event.currentTarget);
    refresh().catch(err => log('Error: ' + err.message));
  </script>
</body>
</html>
"""


def repository_browser_html(token: str, initial_path: str) -> str:
    safe_initial = rclone_config.normalize_repository_subpath(initial_path)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MacUp Repository</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #20242a;
      --muted: #667085;
      --line: #d8dee8;
      --accent: #2563eb;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{ --bg: #111418; --panel: #191d23; --text: #f2f4f7; --muted: #a3acba; --line: #303742; }}
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    header {{ padding: 18px 24px; border-bottom: 1px solid var(--line); background: var(--panel); }}
    h1 {{ margin: 0; font-size: 20px; }}
    main {{ width: min(760px, 100%); margin: 0 auto; padding: 16px; display: grid; gap: 14px; }}
    section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; min-width: 0; }}
    button {{ border: 1px solid var(--line); background: var(--panel); color: var(--text); padding: 7px 10px; border-radius: 6px; font: inherit; cursor: pointer; }}
    button.primary {{ background: var(--accent); border-color: var(--accent); color: white; }}
    .muted {{ color: var(--muted); }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .path {{ overflow-wrap: anywhere; color: var(--muted); }}
    .list {{ display: grid; gap: 6px; }}
    .row {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; border: 1px solid var(--line); border-radius: 6px; padding: 9px; }}
    .name {{ overflow-wrap: anywhere; }}
    .meta {{ color: var(--muted); white-space: nowrap; }}
    .log {{ white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid var(--line); border-radius: 6px; padding: 10px; color: var(--muted); }}
  </style>
</head>
<body>
  <header><h1>MacUp Repository</h1></header>
  <main>
    <section>
      <div class="path" id="remotePath">Loading...</div>
      <div class="actions" style="margin-top:12px">
        <button id="up">Up</button>
        <button id="root">Repository Root</button>
        <button id="snapshots" class="primary">Snapshots Folder</button>
      </div>
    </section>
    <section>
      <div id="items" class="list muted">Loading folder...</div>
    </section>
  </main>
  <script>
    const token = {json.dumps(token)};
    let currentPath = {json.dumps(safe_initial)};
    function fmtBytes(value) {{
      const size = Number(value || 0);
      if (size < 0) return '';
      const units = ['B','KiB','MiB','GiB','TiB'];
      let amount = size;
      let unit = 0;
      while (amount >= 1024 && unit < units.length - 1) {{ amount /= 1024; unit++; }}
      return unit === 0 ? Math.round(amount) + ' ' + units[unit] : amount.toFixed(1) + ' ' + units[unit];
    }}
    function joinPath(base, name) {{
      return [base, name].filter(Boolean).join('/').replace(/^\\/+/, '');
    }}
    function parentPath(path) {{
      const parts = String(path || '').split('/').filter(Boolean);
      parts.pop();
      return parts.join('/');
    }}
    async function api(path) {{
      const res = await fetch(path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token), {{
        headers: {{ 'X-MacUp-Token': token }}
      }});
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Request failed');
      return data;
    }}
    async function load(path) {{
      currentPath = String(path || '').replace(/^\\/+|\\/+$/g, '');
      const box = document.getElementById('items');
      box.className = 'list muted';
      box.textContent = 'Loading folder...';
      try {{
        const data = await api('/api/repository/list?path=' + encodeURIComponent(currentPath));
        document.getElementById('remotePath').textContent = data.remote_path || '';
        box.innerHTML = '';
        box.className = 'list';
        const items = data.items || [];
        if (!items.length) {{
          box.className = 'list muted';
          box.textContent = 'Folder is empty.';
          return;
        }}
        items.forEach(item => {{
          const row = document.createElement('div');
          row.className = 'row';
          const name = document.createElement('div');
          name.className = 'name';
          const label = (item.is_dir ? 'Folder: ' : 'File: ') + (item.name || item.path || '');
          if (item.is_dir) {{
            const button = document.createElement('button');
            button.textContent = label;
            button.onclick = () => load(joinPath(currentPath, item.path || item.name));
            name.append(button);
          }} else {{
            name.textContent = label;
          }}
          const meta = document.createElement('div');
          meta.className = 'meta';
          meta.textContent = item.is_dir ? '' : fmtBytes(item.size);
          row.append(name, meta);
          box.append(row);
        }});
      }} catch (err) {{
        box.className = 'log';
        box.textContent = 'Could not open repository folder: ' + (err && err.message ? err.message : String(err));
      }}
    }}
    document.getElementById('up').onclick = () => load(parentPath(currentPath));
    document.getElementById('root').onclick = () => load('');
    document.getElementById('snapshots').onclick = () => load('snapshots');
    load(currentPath);
  </script>
</body>
</html>
"""
