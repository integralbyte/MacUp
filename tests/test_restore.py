import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from macup_tool.restore import (
    default_restore_status,
    load_restore_status,
    restore_lock_active,
    save_restore_status,
)


class RestoreStateTests(unittest.TestCase):
    def test_restore_status_is_written_user_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MACUP_STATE_DIR": tmp}):
                save_restore_status({"state": "running", "snapshot": "abc"})
                mode = os.stat(Path(tmp) / "restore-status.json").st_mode & 0o777
                self.assertEqual(mode, 0o600)
                self.assertEqual(load_restore_status()["snapshot"], "abc")

    def test_corrupt_restore_status_is_moved_aside(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MACUP_STATE_DIR": tmp}):
                status_path = Path(tmp) / "restore-status.json"
                status_path.write_text("{not json", encoding="utf-8")
                self.assertEqual(load_restore_status()["state"], default_restore_status()["state"])
                self.assertFalse(status_path.exists())
                self.assertTrue(list(Path(tmp).glob("restore-status.json.corrupt-*")))

    def test_malformed_restore_lock_is_not_active_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MACUP_STATE_DIR": tmp}):
                lock_path = Path(tmp) / "restore.lock"
                lock_path.write_text("{not json", encoding="utf-8")
                self.assertFalse(restore_lock_active())
                self.assertFalse(lock_path.exists())

    def test_restore_lock_with_bad_pid_is_not_active_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MACUP_STATE_DIR": tmp}):
                lock_path = Path(tmp) / "restore.lock"
                lock_path.write_text('{"pid":"not-a-pid"}', encoding="utf-8")
                self.assertFalse(restore_lock_active())
                self.assertFalse(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
