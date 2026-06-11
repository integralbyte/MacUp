import unittest
from unittest.mock import patch

from macup_tool.config import default_config
from macup_tool.manager import _setup_state


class ManagerSetupTests(unittest.TestCase):
    def test_existing_repository_requires_password_confirmation_not_just_keychain(self):
        cfg = default_config()
        cfg.update(
            {
                "repository_mode": "existing",
                "repository_selected": True,
                "repository_password_confirmed": False,
                "rclone_configured": True,
                "sources": ["/Users/example/Documents"],
            }
        )

        with patch("macup_tool.manager._install_ready", return_value=False), patch(
            "macup_tool.manager.is_xbar_running", return_value=False
        ):
            setup = _setup_state(cfg, restic_password_set=True)

        self.assertFalse(setup["restic_password"])
        self.assertFalse(setup["repository_password_confirmed"])

    def test_existing_repository_password_ready_after_selected_password_confirmation(self):
        cfg = default_config()
        cfg.update(
            {
                "repository_mode": "existing",
                "repository_selected": True,
                "repository_password_confirmed": True,
                "rclone_configured": True,
                "sources": ["/Users/example/Documents"],
            }
        )

        with patch("macup_tool.manager._install_ready", return_value=False), patch(
            "macup_tool.manager.is_xbar_running", return_value=False
        ):
            setup = _setup_state(cfg, restic_password_set=True)

        self.assertTrue(setup["restic_password"])
        self.assertTrue(setup["repository_password_confirmed"])


if __name__ == "__main__":
    unittest.main()
