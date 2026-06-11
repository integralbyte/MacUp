# MacUp

MacUp is a lightweight macOS backup tool built around Restic, rclone, OneDrive, launchd, and Xbar.

It has two parts:

- `macup manager`: an on-demand local web manager at `127.0.0.1` for setup and edits.
- `macup backup --due`: a short-lived scheduled runner for launchd. It exits when no backup is due.

The manager is not meant to run all day.

## Quick Start

### Double-click setup

From a GitHub download or clone, double-click:

```text
Install MacUp.command
```

It checks/install prerequisites, opens the MacUp manager, and installs the scheduler/Xbar integration after onboarding is complete. You still need to choose the Restic password, sign in to OneDrive, and choose backup folders in the browser because those choices cannot be safely automated.

The MacUp Xbar plugin is included in this repo and is generated/installed by MacUp. The Xbar app itself is a separate app; the double-click installer detects it or installs it with Homebrew when possible.

If macOS refuses to run the command file from a ZIP download, run this once in Terminal:

```sh
chmod +x "Install MacUp.command"
```

### Terminal setup

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

After a reset, run `./macup manager` or double-click `Install MacUp.command` again. MacUp will start from the onboarding flow because local settings and Keychain secrets have been removed.

Important reset detail: reset does not delete backup data from OneDrive. If you reuse the same repository path, enter the original Restic password to reconnect to those backups. If you choose a new password, change the repository path to a new empty folder before initializing, otherwise Restic will reject the password because the existing repository was encrypted with the old one.

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
- Xbar plugin: `~/Library/Application Support/xbar/plugins/macup.5s.sh`

Restic and rclone secrets are stored in macOS Keychain.

## Robustness

See [docs/EDGE_CASE_AUDIT.md](docs/EDGE_CASE_AUDIT.md) for the current edge-case audit and known gaps.
