import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from macup_tool.backup import build_backup_commands, snapshot_ids_to_forget
from macup_tool.config import default_config
from macup_tool.logutil import prune_logs


class BackupPlanningTests(unittest.TestCase):
    def test_preserve_mode_builds_one_absolute_path_command(self):
        cfg = default_config()
        cfg["repository"] = "/tmp/repo"
        cfg["sources"] = ["/Users/example/Documents", "/Users/example/Desktop"]
        commands = build_backup_commands(cfg, "macup-run-test")
        self.assertEqual(len(commands), 1)
        self.assertIsNone(commands[0].cwd)
        self.assertIn("/Users/example/Documents", commands[0].args)
        self.assertIn("/Users/example/Desktop", commands[0].args)

    def test_flat_mode_builds_one_command_per_source_from_parent(self):
        cfg = default_config()
        cfg["repository"] = "/tmp/repo"
        cfg["path_mode"] = "flat"
        cfg["sources"] = ["/Users/example/A", "/Users/example/Other/B"]
        commands = build_backup_commands(cfg, "macup-run-test")
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0].cwd, "/Users/example")
        self.assertIn("A", commands[0].args)
        self.assertEqual(commands[1].cwd, "/Users/example/Other")
        self.assertIn("B", commands[1].args)

    def test_snapshot_retention_groups_by_run_tag(self):
        snapshots = [
            {"id": "old-a", "time": "2026-01-01T00:00:00Z", "tags": ["macup", "macup-run-old"]},
            {"id": "old-b", "time": "2026-01-01T00:00:01Z", "tags": ["macup", "macup-run-old"]},
            {"id": "mid", "time": "2026-01-02T00:00:00Z", "tags": ["macup", "macup-run-mid"]},
            {"id": "new", "time": "2026-01-03T00:00:00Z", "tags": ["macup", "macup-run-new"]},
        ]
        self.assertEqual(set(snapshot_ids_to_forget(snapshots, keep_runs=2)), {"old-a", "old-b"})

    def test_prune_logs_removes_only_old_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MACUP_STATE_DIR": tmp}):
                logs = Path(tmp) / "logs"
                logs.mkdir()
                old = logs / "old.log"
                new = logs / "new.log"
                old.write_text("old", encoding="utf-8")
                new.write_text("new", encoding="utf-8")
                old_time = 1
                os.utime(old, (old_time, old_time))
                removed = prune_logs(days=14)
                self.assertIn(old.resolve(), [path.resolve() for path in removed])
                self.assertFalse(old.exists())
                self.assertTrue(new.exists())


if __name__ == "__main__":
    unittest.main()
