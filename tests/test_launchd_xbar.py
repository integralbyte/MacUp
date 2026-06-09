import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from macup_tool import launchd, xbar


class InstallArtifactTests(unittest.TestCase):
    def test_launchd_plist_uses_hourly_calendar_check(self):
        data = launchd.plist_data("/tmp/macup")
        self.assertEqual(data["ProgramArguments"], ["/tmp/macup", "backup", "--due"])
        self.assertTrue(data["RunAtLoad"])
        self.assertEqual(data["StartCalendarInterval"], {"Minute": 0})

    def test_xbar_plugin_calls_status_renderer(self):
        script = xbar.plugin_script("/tmp/macup")
        self.assertIn("status --format xbar", script)
        self.assertIn("MACUP_CLI='/tmp/macup'", script)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", script)

    def test_xbar_install_writes_executable_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MACUP_XBAR_PLUGIN_DIR": tmp}):
                target = xbar.install("/tmp/macup")
                self.assertEqual(target.resolve(), (Path(tmp) / "macup.1m.sh").resolve())
                self.assertTrue(os.access(target, os.X_OK))
                self.assertIn("/tmp/macup", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
