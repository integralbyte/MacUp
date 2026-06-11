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
        self.assertIn("use the original password", html)
        self.assertIn("only be reopened with the original Restic password", html)


if __name__ == "__main__":
    unittest.main()
