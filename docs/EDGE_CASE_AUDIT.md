# MacUp Edge Case Audit

This is a practical failure-mode audit for MacUp. It is not a claim that every possible hardware, OS, network, Restic, rclone, OneDrive, or user-behavior failure has already been eliminated. It is a feature-by-feature map of what can fail, what MacUp currently handles, and what remains to harden.

Severity:

- P0: can lose backup access, overwrite data, leak secrets, or silently stop protecting data.
- P1: can block backup/restore or give misleading status.
- P2: confusing UX, missing guidance, or recoverable local failure.

## Setup Flow

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| User opens manager before setup | P1 | Step-by-step setup shows only the active step. | Add "skip for now" where safe. |
| User reloads during setup | P1 | State is loaded from config/Keychain/status. | Rclone mid-flow state only lives in memory, so reloading during rclone questions may require restarting OneDrive setup. |
| User cancels native folder picker | P2 | API returns an error; nothing is saved. | Inline source-picker error could be clearer. |
| User cancels restore destination picker | P2 | API returns an error; restore does not start. | Inline restore-card error could be clearer. |
| User closes manager | P2 | Xbar shows orange `▼` while running and offers Close Manager Server. | No auto-timeout shutdown yet. |
| Multiple manager opens | P1 | Existing manager is detected and reopened instead of starting another server. | None known. |

## Manager Security

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| Browser/API request without token | P0 | API and root HTML require the manager token. | Token is still in the local URL by design; local processes with filesystem access can read manager state. |
| Token appears in manager logs | P0 | Request logging redacts the token query string. | None known. |
| rclone raw output includes sensitive material | P0 | Manager no longer returns raw rclone output in question payloads. | Continue auditing new rclone flows if added. |
| Latest log path is maliciously changed outside log dir | P0 | Manager resolves path and requires it to be inside MacUp logs and a file. | None known. |
| Web UI displays untrusted repository/snapshot text | P1 | Snapshot and repository history text is HTML-escaped. | Continue avoiding `innerHTML` for future UI. |

## Configuration

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| Missing config | P1 | Defaults are used. | None known. |
| Corrupt `config.json` | P1 | Corrupt file is moved to `config.json.corrupt-*`; defaults are used. | Surface the moved-aside path in the UI. |
| Corrupt `status.json` | P1 | Corrupt file is moved to `status.json.corrupt-*`; default status is used. | Surface the moved-aside path in the UI. |
| Invalid interval/retention/log retention | P1 | Validation rejects non-positive/invalid values. | Add inline field-level validation. |
| Invalid remote name or repository path | P1 | Validation rejects blank remote/path, rclone delimiter characters, absolute repository paths, and `.`/`..` path segments. | Add inline field-level validation before save. |
| Source path is relative | P1 | Validation rejects it. | None known. |
| Source path disappears before backup | P1 | Backup validation rejects missing source folders before Restic starts. | Add inline per-source warning in manager. |
| Source path is a file | P1 | Backup validation rejects non-folder source paths. | UI picker already chooses folders. |
| Flat mode duplicate basenames | P1 | Validation rejects duplicates. | None known. |
| Remote name/repository path changed after setup | P0 | Old repository is saved in history, new location is marked uninitialized, backups pause. | Add stronger confirmation modal before saving repo changes. |
| User wants to restore an old repository history entry | P1 | Repository history can repopulate remote/path fields. | Repository override/local path history needs a fuller UI. |

## Secrets and Keychain

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| Restic password missing | P0 | Backup/restore refuse to run and ask user to save it. | Add password health check in setup summary. |
| Restic password lost by user | P0 | Setup warns user to store it elsewhere. | Cannot be recovered by design. |
| rclone config encryption password missing | P1 | MacUp creates/stores a generated password in Keychain. | Add a repair/recreate path if Keychain item is deleted. |
| Keychain command fails | P1 | Command failure bubbles to UI/CLI. | Friendlier explanation and retry action. |
| Secrets in logs | P0 | Common token/password patterns are redacted. | Redaction is best-effort; keep adding patterns as new tools are used. |

## OneDrive / rclone

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| Personal vs Business account | P1 | rclone flow exposes the drive choice. | Improve drive labels if rclone output is cryptic. |
| rclone advanced/default questions | P2 | MacUp does not pass `--all`, so default/blank advanced questions are skipped. | None known. |
| OAuth denied/cancelled | P1 | rclone returns an error surfaced by UI. | Add a "restart OneDrive setup" button/state. |
| Token expires/revoked later | P1 | Remote test/backup fail visibly. | Add explicit reconnect action. |
| Network down during setup/test/backup | P1 | rclone/restic error is surfaced/logged. | Better wording for transient network errors. |
| rclone binary missing | P1 | Doctor reports missing dependency. | Install guidance could be more automated. |
| Encrypted rclone config unreadable/corrupt | P1 | rclone commands fail visibly. | Add repair flow with backup of old config. |

## Repository Initialization

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| Repository already exists | P1 | `restic snapshots --json` succeeds; init is skipped. | None known. |
| Repository does not exist | P1 | Restic init is attempted. | Add clearer success state in UI. |
| Wrong password for existing repo | P0 | Probe fails; init also fails; error is shown/logged. | Error wording should say wrong password is a likely cause. |
| Wrong remote/path accidentally creates a new repo | P0 | Changing repo location marks it uninitialized; scheduled backups pause until explicit init/probe. | Add confirmation modal before init of empty repo. |
| Repo path contains invalid rclone characters | P1 | Remote name and repository path are prevalidated before saving. | Continue expanding validation if rclone documents more reserved characters. |

## Backup Execution

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| Manual and scheduled backup overlap | P1 | Lock file prevents overlap. | Xbar/UI could show "already running" more clearly. |
| Stale lock from crashed process | P1 | Lock is removed if PID is gone. | None known. |
| Malformed lock file | P1 | Lock is treated as stale and replaced. | None known. |
| Backup due check after failure | P1 | Failures are treated as due on next launchd check. | None known. |
| Backup not due | P2 | `backup --due` exits quickly. | None known. |
| Source missing/non-folder | P1 | Validation fails before Restic starts. | Inline per-source status. |
| macOS Full Disk Access blocks source | P1 | Restic failure is logged; Full Disk Access button is available in advanced settings. | Detect common protected-folder permission errors and show targeted instructions. |
| OneDrive quota exceeded | P1 | rclone/restic failure is logged and status turns red. | Detect quota wording and show specific message. |
| Machine sleeps mid-backup | P1 | Process may fail; launchd retries if failed. | Consider caffeinate during backup. |
| Restic prune fails after backup succeeds | P1 | Current backup run is marked failed if prune raises. | Decide if backup should be success-with-prune-warning instead. |
| Failed partial snapshot | P1 | MacUp attempts `forget --tag <run> --prune`. | Keep monitoring cleanup failure cases. |

## Scheduling / launchd

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| Install/reinstall LaunchAgent | P1 | Writes plist and bootstraps it. | Better error if launchctl bootstrap fails. |
| Mac asleep at scheduled time | P1 | Hourly checks plus due logic handle wake better than a single 24h trigger. | None known. |
| LaunchAgent points at moved Downloads repo | P1 | Install copies runtime to `~/.local/share/macup/app`. | None known. |
| launchd stdout/stderr grows forever | P2 | Main run logs are pruned; launchd logs are not. | Add pruning/rotation for launchd logs. |

## Xbar

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| Xbar not installed/running | P2 | Installer searches common paths and reports issue. | Add install guidance/link. |
| Xbar action blocks | P1 | Backup and manager actions detach. | Restore is not exposed in Xbar. |
| Manager running forgotten | P2 | Menu-bar title shows orange `▼`; close action is orange. | Optional auto-close. |
| Xbar plugin creates Python bytecode | P2 | Plugin exports `PYTHONDONTWRITEBYTECODE=1`. | None known. |
| Status command crashes | P1 | Plugin renders a red fallback status with the first few error lines. | None known. |

## Snapshot Browsing

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| Snapshot listing fails | P1 | API returns structured JSON errors and the snapshot section shows the error inline. | None known. |
| No snapshots | P2 | UI says no MacUp snapshots found. | None known. |
| Long paths/tags | P2 | Wrapped within card. | None known. |
| Clicking details for multiple snapshots | P2 | Each card has independent details. | None known. |
| Restore size slow | P2 | ID/time/path appear immediately; restore size/file count computed only on demand and cached. | Add cancel button for stats command. |
| User worries stats downloads files | P2 | UI explains stats read metadata and do not restore contents. | None known. |
| Stats fails | P2 | Detail card shows restore size unavailable. | Include raw sanitized reason inline. |

## Restore / Download

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| Browser download would bypass Restic | P0 | Download starts backend `restic restore`, not browser download. | None known. |
| Snapshot id invalid | P1 | API validates selected snapshot exists before detaching restore. | None known. |
| Destination does not exist | P1 | Restore refuses to start. | None known. |
| Destination is not writable | P1 | Restore refuses to start. | None known. |
| Destination has existing restore folder | P1 | Numeric suffix is added. | None known. |
| Restore fails after starting | P1 | Restore log and restore status record failure; manager polls and shows the latest restore state. | Add a direct "open restore log" action. |
| Disk space insufficient | P1 | Restic fails and logs error. | Preflight disk-space estimate using cached stats when available. |
| User starts multiple restores | P1 | App-level restore lock/status blocks overlapping restores and treats stale/malformed locks as recoverable. | None known. |
| Restoring malicious paths | P1 | Restic restores under the chosen target. | Confirm behavior for absolute-path snapshots across Restic versions. |

## Logs and State

| Case | Severity | Current handling | Remaining gap |
| --- | --- | --- | --- |
| Atomic config/status writes | P1 | Atomic writes are used; config/status/cache/restore-status/lock JSON is written user-only where MacUp controls the write. | None known. |
| Log pruning | P2 | Backup logs are pruned by age. | Restore and launchd logs need explicit pruning coverage. |
| Latest log path tampered | P0 | Must resolve inside logs dir and be a file. | None known. |
| Public repo accidentally includes local state | P0 | `.gitignore` excludes config/state/logs/Xbar bundle/dmg. | Keep running public-source scans before push. |

## Test Coverage Added So Far

- Config validation and repository construction.
- Missing/file source validation.
- Corrupt config recovery.
- Repository input validation.
- Backup command generation for preserve/flat modes.
- Malformed lock recovery.
- Restore status and malformed restore-lock recovery.
- Snapshot retention grouping.
- Log pruning.
- Restic local integration backup/restore.
- LaunchAgent and Xbar artifact generation.
- Status/Xbar rendering states.

## Highest Remaining Gaps

1. Preflight disk-space check for restores when stats are available.
2. Better Full Disk Access detection per selected source.
3. Confirmation modals for repository-location changes and initializing empty repos.
4. Friendlier Keychain/rclone repair flows.
5. Restore log shortcut and optional cancel/kill action.
