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


if __name__ == "__main__":
    unittest.main()
