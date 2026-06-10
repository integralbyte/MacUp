import os
import stat
import unittest
from pathlib import Path


class BootstrapScriptTests(unittest.TestCase):
    def test_double_click_installer_is_executable_and_bootstraps_manager(self):
        script = Path(__file__).resolve().parents[1] / "Install MacUp.command"
        mode = os.stat(script).st_mode
        content = script.read_text(encoding="utf-8")
        self.assertTrue(mode & stat.S_IXUSR)
        self.assertIn("install_formula_if_needed restic restic", content)
        self.assertIn("install_formula_if_needed rclone rclone", content)
        self.assertIn("brew install --cask xbar", content)
        self.assertIn("./macup manager", content)
        self.assertIn("./macup install", content)


if __name__ == "__main__":
    unittest.main()
