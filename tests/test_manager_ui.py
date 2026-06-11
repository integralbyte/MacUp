import unittest

from macup_tool.manager import manager_html, manager_log_tail


class ManagerUiTests(unittest.TestCase):
    def test_rclone_recommendations_are_rendered_and_targeted(self):
        html = manager_html("test-token")
        self.assertIn("recommended-choice", html)
        self.assertIn("recommended-badge", html)
        self.assertIn("config_type", html)
        self.assertIn("value === 'onedrive'", html)
        self.assertIn("config_driveid", html)
        self.assertIn("combined.includes('onedrive')", html)
        self.assertIn("combined.includes('business')", html)
        self.assertIn("!combined.includes('personalcachelibrary')", html)

    def test_open_snapshots_button_uses_official_onedrive_url_api(self):
        html = manager_html("test-token")
        self.assertIn("/api/repository/web-url?path=snapshots", html)
        self.assertIn("Opened the official OneDrive snapshots folder", html)
        self.assertNotIn("window.open('/repository?token='", html)

    def test_advanced_settings_include_confirmed_reset_and_close_on_stop(self):
        html = manager_html("test-token")
        self.assertIn("Reset MacUp", html)
        self.assertIn("RESET MACUP", html)
        self.assertIn("/api/reset", html)
        self.assertIn("closeManagerPage('Manager stopped')", html)
        self.assertIn("closeManagerPage('MacUp reset complete')", html)

    def test_password_setup_warns_about_reset_reconnect_password(self):
        html = manager_html("test-token")
        self.assertIn("reconnect to existing OneDrive backups", html)
        self.assertIn("original Restic password", html)
        self.assertIn("Reconnect mode", html)

    def test_onboarding_has_new_vs_existing_repository_choice(self):
        html = manager_html("test-token")
        self.assertIn("Start New Backup Set", html)
        self.assertIn("Reconnect Existing Backups", html)
        self.assertIn("/api/repository/mode", html)
        self.assertIn("Initialize New Repository", html)
        self.assertIn("Connect Existing Repository", html)
        self.assertIn("Existing Repositories", html)
        self.assertIn("/api/repositories/discover", html)
        self.assertIn("/api/repository/select", html)
        self.assertIn("Use This Repository", html)
        self.assertIn("Manual OneDrive repository path", html)
        self.assertIn("selectManualRepository", html)
        self.assertIn("Last backup:", html)
        self.assertIn("Verify Existing Repository", html)
        self.assertIn("repositorySetupSection", html)
        self.assertIn("schedulerSetupSection", html)
        self.assertIn("Choose Different Repository", html)
        self.assertIn("!setup.repository_selected", html)

    def test_onboarding_has_always_available_reset_control(self):
        html = manager_html("test-token")
        self.assertIn('id="setupResetSection"', html)
        self.assertIn("Reset Setup", html)
        self.assertIn("setupResetConfirmation", html)
        self.assertIn("setupResetConfirm", html)
        self.assertIn("updateSetupResetConfirmState", html)
        self.assertIn("id === 'setupResetSection'", html)
        self.assertIn("/api/reset", html)

    def test_manager_has_backup_progress_and_stop_button(self):
        html = manager_html("test-token")
        self.assertIn('id="activitySection"', html)
        self.assertIn('id="backupProgress"', html)
        self.assertIn('id="stopBackup"', html)
        self.assertIn("/api/backup-stop", html)
        self.assertIn("renderActivity", html)

    def test_manager_has_skipped_item_retry_and_ignore_controls(self):
        html = manager_html("test-token")
        self.assertIn('id="backupIssuesSection"', html)
        self.assertIn('id="retrySkippedBackup"', html)
        self.assertIn('id="ignoreSkippedItems"', html)
        self.assertIn('id="ignoredPaths"', html)
        self.assertIn("/api/backup-issues/ignore", html)
        self.assertIn("/api/excludes/remove", html)
        self.assertIn("removeIgnoredPath", html)

    def test_manager_log_tail_hides_benign_lock_noise_and_formats_restic_json(self):
        raw = "\n".join(
            [
                "MacUp log started at 2026-06-11T12:00:14Z",
                "$ /opt/homebrew/bin/restic -o rclone.program=/usr/local/bin/rclone backup --json --tag macup /tmp/source",
                "Load(<lock/f21d28abce>, 0, 0) returned error, retrying after 1s: unexpected EOF",
                "Load(<lock/f21d28abce>, 0, 0) failed: <lock/f21d28abce> does not exist",
                '{"message_type":"status","percent_done":0.5}',
                '{"message_type":"error","error":{"message":"operation not permitted"},"during":"scan","item":"/Users/ace/Pictures/Photos Library.photoslibrary"}',
                '{"message_type":"error","error":{"message":"operation not permitted"},"during":"archival","item":"/Users/ace/Pictures/Photos Library.photoslibrary"}',
                '{"message_type":"summary","total_files_processed":42,"data_added_packed":2048,"total_duration":65,"snapshot_id":"abcdef123456"}',
                '{"message_type":"exit_error","code":3,"message":"Warning: at least one source file could not be read"}',
            ]
        )
        output = manager_log_tail(raw)
        self.assertIn("Starting Restic backup.", output)
        self.assertIn("Skipped /Users/ace/Pictures/Photos Library.photoslibrary", output)
        self.assertIn("Snapshot abcdef12 saved. Processed 42 files in 1m 5s; added 2.0 KiB.", output)
        self.assertIn("Restic warning: Warning: at least one source file could not be read", output)
        self.assertNotIn("Load(<lock", output)
        self.assertNotIn("percent_done", output)


if __name__ == "__main__":
    unittest.main()
