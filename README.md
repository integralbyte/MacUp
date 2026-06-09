# MacUp

MacUp is a lightweight macOS backup tool built around Restic, rclone, OneDrive, launchd, and Xbar.

It has two parts:

- `macup manager`: an on-demand local web manager at `127.0.0.1` for setup and edits.
- `macup backup --due`: a short-lived scheduled runner for launchd. It exits when no backup is due.

The manager is not meant to run all day.

## Quick Start

```sh
cd MacUp
./macup doctor
./macup manager
```

In the manager:

1. Add source folders.
2. Set the Restic repository password. Store this password somewhere safe too; without it, Restic backups cannot be recovered.
3. Configure OneDrive through rclone.
4. Initialize the repository.
5. Install the scheduler and Xbar plugin.
6. Run a manual backup.

## Defaults

- Backup interval: `24` hours.
- Retention: last `14` successful backup runs.
- Log retention: `14` days.
- Path mode: preserve the full selected folder path.
- Repository: `rclone:macup-onedrive:MacUp/<hostname>/restic`.

## Useful Commands

```sh
./macup status
./macup status --format xbar
./macup backup --due
./macup backup --manual
./macup backup --manual --detach
./macup install --no-load
./macup restore --list
./macup restore --snapshot latest --target ~/MacUp-Restore
```

## Files

- Config: `~/.config/macup/config.json`
- rclone config: `~/.config/macup/rclone.conf`
- Status: `~/.local/state/macup/status.json`
- Logs: `~/.local/state/macup/logs/`
- LaunchAgent: `~/Library/LaunchAgents/com.macup.backup.plist`
- Xbar plugin: `~/Library/Application Support/xbar/plugins/macup.1m.sh`

Restic and rclone secrets are stored in macOS Keychain.
