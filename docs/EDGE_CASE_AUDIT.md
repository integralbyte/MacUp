# MacUp Edge Case Audit

This audit tracks failure modes that matter for a Restic + rclone + OneDrive backup tool on macOS.

## Snapshot Browsing

- Snapshot listing can fail if Restic password, rclone config, OneDrive auth, or network access is unavailable.
  - Current handling: API returns an error and the manager writes it to Output.
  - Gap: show a dedicated inline snapshot error instead of only the Output panel.
- Snapshot IDs, time, paths, host, and tags come from `restic snapshots --json`.
  - Current handling: displayed without restoring file contents.
- Restore size and file count require `restic stats --mode restore-size --json`.
  - Current handling: calculated only after expanding a snapshot and cached per repository/snapshot.
  - Important: this reads Restic metadata/tree data. It does not restore every file, but over OneDrive/rclone it can still be slow while metadata is fetched or cached.
- Multiple expanded snapshots should not overwrite each other.
  - Current handling: each snapshot card owns its own details panel.
- Very long paths/tags should not expand the page.
  - Current handling: wrapping and fixed manager width.

## Snapshot Restore / Download

- Browser downloads are wrong for Restic restores.
  - Current handling: Download uses a backend Restic restore after a native macOS folder picker.
- Restore target may already contain files.
  - Current handling: MacUp creates `MacUp Restore <snapshot>` under the selected folder and adds a numeric suffix if needed.
- Restic password must not be sent through URLs or command-line arguments.
  - Current handling: restore uses the password already stored in macOS Keychain. The browser does not pass a password to the restore process.
- User cancels destination picker.
  - Current handling: API returns an error and no restore starts.
- Restore can take a long time or fail midway.
  - Current handling: restore runs detached and writes a restore log.
  - Gap: no dedicated restore progress/status widget yet.
- Restore destination may be on a disk with insufficient space or a permission-restricted folder.
  - Current handling: Restic failure is logged.
  - Gap: preflight free-space and writable-folder checks.

## Backups

- Scheduled and manual backups can overlap.
  - Current handling: lock file prevents overlap and stale locks are cleared if the process is gone.
- Repository location can change accidentally.
  - Current handling: old repository is saved in history, new location is marked uninitialized, and backups pause until explicitly initialized/probed.
- Failed backups should retry soon.
  - Current handling: `--due` treats failures as due on the next launchd check.
- Source paths may disappear or become unreadable.
  - Current handling: Restic failure is logged and status turns red.
  - Gap: preflight source availability and Full Disk Access hints per source.
- Flat path mode can collide on duplicate basenames.
  - Current handling: config validation rejects duplicates.

## OneDrive / rclone

- rclone can ask advanced/default questions that are not useful for normal users.
  - Current handling: MacUp does not pass `--all`, so rclone asks only required post-config questions.
- OneDrive Personal/Business drive choice can contain multiple drive IDs.
  - Current handling: rclone exposes choices and MacUp labels important questions.
- OAuth can fail, expire, or be revoked.
  - Current handling: remote test and backup fail visibly.
  - Gap: clearer "reconnect OneDrive" action.
- rclone config encryption password must not be exposed.
  - Current handling: encrypted rclone config password is stored in Keychain.

## Web Manager

- Manager should not run all day unnoticed.
  - Current handling: Xbar shows an orange `▼` while manager is running and has an orange close action.
- Multiple manager instances can be opened.
  - Current handling: a running manager is detected and reopened instead of starting a second server.
- Browser local token should not leak into logs.
  - Current handling: manager request logging redacts token query strings.
- First-run setup should not show every section at once.
  - Current handling: setup is step-by-step until complete.
- Advanced settings should not interrupt normal usage.
  - Current handling: Advanced Settings is at the bottom and collapsed unless setup needs it.

## Xbar / launchd

- Xbar plugin should not block on long-running actions.
  - Current handling: Backup Now and Open Manager detach.
- Manager close should only show when useful.
  - Current handling: Close Manager Server appears only while manager is running.
- LaunchAgent should not run a full backup every hour.
  - Current handling: launchd checks hourly and `backup --due` exits quickly unless due or failed.

## Secrets / Logs

- Restic password loss makes backups unrecoverable.
  - Current handling: setup tells user to save it; Keychain stores it.
- Secrets can leak through logs.
  - Current handling: log redaction catches common token/password field names.
- Public repository must not include personal config, rclone config, logs, or local app bundles.
  - Current handling: `.gitignore` excludes local config/state/logs and bundled Xbar artifacts.
