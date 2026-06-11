import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from macup_tool import reset


class ResetTests(unittest.TestCase):
    def test_reset_removes_local_state_and_installer_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            state_dir = Path(tmp) / "state"
            config_dir.mkdir()
            state_dir.mkdir()
            (state_dir / "logs").mkdir()
            (config_dir / "config.json").write_text("{}", encoding="utf-8")
            (config_dir / "rclone.conf").write_text("remote", encoding="utf-8")
            (state_dir / "status.json").write_text("{}", encoding="utf-8")
            (state_dir / "logs" / "backup.log").write_text("log", encoding="utf-8")

            with patch.dict(os.environ, {"MACUP_CONFIG_DIR": str(config_dir), "MACUP_STATE_DIR": str(state_dir)}), patch(
                "macup_tool.reset.launchd.uninstall", return_value=Path("/tmp/com.macup.backup.plist")
            ) as launchd_uninstall, patch(
                "macup_tool.reset.xbar.uninstall", return_value=[Path("/tmp/macup.10s.sh")]
            ) as xbar_uninstall, patch(
                "macup_tool.reset.keychain.delete_password", return_value=True
            ) as delete_password, patch(
                "macup_tool.reset.refresh_xbar", return_value=True
            ):
                result = reset.reset_local_state(reset.CONFIRMATION_TEXT)

            self.assertFalse(config_dir.exists())
            self.assertFalse(state_dir.exists())
            self.assertTrue(result["removed_config"])
            self.assertTrue(result["removed_state"])
            launchd_uninstall.assert_called_once()
            xbar_uninstall.assert_called_once()
            self.assertEqual(delete_password.call_count, 2)

    def test_reset_requires_exact_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            marker = config_dir / "config.json"
            marker.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"MACUP_CONFIG_DIR": str(config_dir), "MACUP_STATE_DIR": str(Path(tmp) / "state")}):
                with self.assertRaisesRegex(reset.ResetError, reset.CONFIRMATION_TEXT):
                    reset.reset_local_state("reset")
            self.assertTrue(marker.exists())

    def test_reset_refuses_active_backup_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            (state_dir / "backup.lock").write_text(f'{{"pid": {os.getpid()}}}', encoding="utf-8")
            with patch.dict(os.environ, {"MACUP_CONFIG_DIR": str(Path(tmp) / "config"), "MACUP_STATE_DIR": str(state_dir)}):
                with self.assertRaisesRegex(reset.ResetError, "backup is running"):
                    reset.reset_local_state(reset.CONFIRMATION_TEXT)

    def test_reset_refuses_active_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MACUP_CONFIG_DIR": str(Path(tmp) / "config"), "MACUP_STATE_DIR": str(Path(tmp) / "state")}), patch(
                "macup_tool.reset._backup_active", return_value=False
            ), patch("macup_tool.reset.restore_lock_active", return_value=True):
                with self.assertRaisesRegex(reset.ResetError, "restore is running"):
                    reset.reset_local_state(reset.CONFIRMATION_TEXT)


if __name__ == "__main__":
    unittest.main()
