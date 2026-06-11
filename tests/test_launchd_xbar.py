import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from macup_tool import installer, launchd, xbar


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
        self.assertIn("MacUp status failed", script)

    def test_xbar_install_writes_executable_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MACUP_XBAR_PLUGIN_DIR": tmp}):
                target = xbar.install("/tmp/macup")
                self.assertEqual(target.resolve(), (Path(tmp) / "macup.5s.sh").resolve())
                self.assertTrue(os.access(target, os.X_OK))
                self.assertIn("/tmp/macup", target.read_text(encoding="utf-8"))

    def test_xbar_install_removes_old_macup_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MACUP_XBAR_PLUGIN_DIR": tmp}):
                old = Path(tmp) / "macup.1m.sh"
                old.write_text("old", encoding="utf-8")
                target = xbar.install("/tmp/macup")
                self.assertTrue(target.exists())
                self.assertFalse(old.exists())

    def test_installer_copies_bundled_xbar_to_user_applications_and_clears_quarantine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "repo" / "xbar (use what you need)" / "xbar.app"
            (source / "Contents").mkdir(parents=True)
            (source / "Contents" / "Info.plist").write_text("xbar", encoding="utf-8")
            target = root / "home" / "Applications" / "xbar.app"
            with patch("macup_tool.installer.paths.repo_root", return_value=root / "repo"), patch(
                "macup_tool.installer.system_xbar_app",
                return_value=root / "Applications" / "xbar.app",
            ), patch(
                "macup_tool.installer.user_xbar_app",
                return_value=target,
            ), patch(
                "macup_tool.installer.remove_quarantine"
            ) as remove_quarantine:
                installed = installer.ensure_xbar_app_installed()

            self.assertEqual(installed, target)
            self.assertTrue((target / "Contents" / "Info.plist").exists())
            remove_quarantine.assert_called()

    def test_installer_verifies_xbar_plugin_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "macup.5s.sh"
            plugin.write_text("#!/bin/sh\necho '● | color=#2da44e'\n", encoding="utf-8")
            plugin.chmod(0o755)

            ok, first_line = installer.verify_xbar_plugin_output(plugin)

            self.assertTrue(ok)
            self.assertEqual(first_line, "● | color=#2da44e")


if __name__ == "__main__":
    unittest.main()
