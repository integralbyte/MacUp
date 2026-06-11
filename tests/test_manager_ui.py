import unittest

from macup_tool.manager import manager_html


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


if __name__ == "__main__":
    unittest.main()
