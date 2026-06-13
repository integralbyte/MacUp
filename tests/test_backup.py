import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from macup_tool.backup import (
    BackupCommand,
    BackupError,
    BackupLock,
    build_backup_commands,
    ensure_repository,
    run_backup,
    snapshot_ids_to_forget,
    stop_backup,
)
from macup_tool.config import default_config
from macup_tool.logutil import prune_logs
from macup_tool.process import CommandError, CommandResult


class BackupPlanningTests(unittest.TestCase):
    def test_preserve_mode_builds_one_absolute_path_command(self):
        cfg = default_config()
        cfg["repository"] = "/tmp/repo"
        cfg["sources"] = ["/Users/example/Documents", "/Users/example/Desktop"]
        cfg["excludes"] = ["/Users/example/Documents/private"]
        commands = build_backup_commands(cfg, "macup-run-test")
        self.assertEqual(len(commands), 1)
        self.assertIsNone(commands[0].cwd)
        self.assertIn("--json", commands[0].args)
        self.assertIn("--exclude", commands[0].args)
        self.assertIn("/Users/example/Documents/private", commands[0].args)
        self.assertIn("/Users/example/Documents", commands[0].args)
        self.assertIn("/Users/example/Desktop", commands[0].args)

    def test_flat_mode_builds_one_command_per_source_from_parent(self):
        cfg = default_config()
        cfg["repository"] = "/tmp/repo"
        cfg["path_mode"] = "flat"
        cfg["sources"] = ["/Users/example/A", "/Users/example/Other/B"]
        commands = build_backup_commands(cfg, "macup-run-test")
        self.assertEqual(len(commands), 2)
        self.assertIn("--json", commands[0].args)
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

    def test_malformed_lock_does_not_block_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "backup.lock"
            lock_path.write_text("{not json", encoding="utf-8")
            lock = BackupLock(lock_path)
            self.assertTrue(lock.acquire("run"))
            lock.release()

    def test_stop_backup_clears_lock_and_marks_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MACUP_STATE_DIR": tmp}):
                lock_path = Path(tmp) / "backup.lock"
                lock_path.write_text('{"pid": 424242, "run_id": "run"}', encoding="utf-8")
                with patch("macup_tool.backup.process_alive", return_value=False):
                    result = stop_backup()

                self.assertTrue(result["stopped"])
                self.assertFalse(lock_path.exists())
                from macup_tool.status import load_status

                status = load_status()
                self.assertEqual(status["progress_phase"], "cancelled")
                self.assertEqual(status["active_run_id"], "")

    def test_ensure_repository_does_not_init_on_wrong_password(self):
        cfg = default_config()
        cfg["remote_name"] = "remote"
        cfg["repository_path"] = "MacUp/host/restic"
        probe = CommandResult(
            args=["restic", "snapshots"],
            returncode=12,
            output='{"message_type":"exit_error","code":12,"message":"Fatal: wrong password or no key found"}',
        )
        logger = Mock()
        with patch("macup_tool.backup.run_restic", return_value=probe) as run_restic:
            with self.assertRaisesRegex(BackupError, "original Restic password"):
                ensure_repository(cfg, logger)
        run_restic.assert_called_once()
        self.assertFalse(logger.write.called)

    def test_ensure_repository_explains_existing_repo_after_init_conflict(self):
        cfg = default_config()
        probe = CommandResult(args=["restic", "snapshots"], returncode=1, output="repository is not initialized")
        init = CommandResult(args=["restic", "init"], returncode=1, output="Fatal: config file already exists")
        with patch("macup_tool.backup.run_restic", side_effect=[probe, init]):
            with self.assertRaisesRegex(BackupError, "change the repository path"):
                ensure_repository(cfg, Mock())

    def test_ensure_repository_existing_mode_probe_only(self):
        cfg = default_config()
        probe = CommandResult(args=["restic", "snapshots"], returncode=1, output="repository does not exist")
        with patch("macup_tool.backup.run_restic", return_value=probe) as run_restic:
            with self.assertRaisesRegex(BackupError, "Reconnect existing backups"):
                ensure_repository(cfg, Mock(), create=False)
        run_restic.assert_called_once()

    def test_run_backup_treats_saved_snapshot_with_unreadable_files_as_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            cfg = default_config()
            cfg["repository"] = str(root / "repo")
            cfg["sources"] = [str(source)]
            cfg["initialized"] = True
            lines = [
                '{"message_type":"error","error":{"message":"openfile for readdirnames failed: operation not permitted"},"during":"scan","item":"/Users/ace/Pictures/Photos Library.photoslibrary"}',
                '{"message_type":"summary","total_files_processed":1,"total_bytes_processed":10,"snapshot_id":"abc123"}',
                '{"message_type":"exit_error","code":3,"message":"Warning: at least one source file could not be read"}',
            ]

            def fake_run_streamed(args, **kwargs):
                for line in lines:
                    kwargs["on_line"](line)
                raise CommandError(CommandResult(args=args, returncode=3, output="\n".join(lines)))

            env = {"MACUP_STATE_DIR": str(root / "state"), "MACUP_RESTIC_PASSWORD": "test-password"}
            with patch.dict(os.environ, env), patch("macup_tool.backup.ensure_repository"), patch(
                "macup_tool.backup.prune_snapshots"
            ), patch("macup_tool.backup.build_backup_commands", return_value=[BackupCommand(["restic", "backup"])]), patch(
                "macup_tool.backup.run_streamed", side_effect=fake_run_streamed
            ), patch(
                "macup_tool.backup.launchd.reload_later"
            ):
                self.assertEqual(run_backup(cfg, manual=True), 0)
                from macup_tool.status import load_status

                status = load_status()
                self.assertEqual(status["last_result"], "success")
                self.assertIn("Photos Library", status["last_warning"])
                self.assertEqual(status["backup_issues"][0]["during"], "scan")

    def test_run_backup_fails_when_code_3_has_no_saved_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            cfg = default_config()
            cfg["repository"] = str(root / "repo")
            cfg["sources"] = [str(source)]
            cfg["initialized"] = True
            lines = ['{"message_type":"exit_error","code":3,"message":"Warning: at least one source file could not be read"}']

            def fake_run_streamed(args, **kwargs):
                for line in lines:
                    kwargs["on_line"](line)
                raise CommandError(CommandResult(args=args, returncode=3, output="\n".join(lines)))

            env = {"MACUP_STATE_DIR": str(root / "state"), "MACUP_RESTIC_PASSWORD": "test-password"}
            with patch.dict(os.environ, env), patch("macup_tool.backup.ensure_repository"), patch(
                "macup_tool.backup.cleanup_failed_run"
            ), patch("macup_tool.backup.build_backup_commands", return_value=[BackupCommand(["restic", "backup"])]), patch(
                "macup_tool.backup.run_streamed", side_effect=fake_run_streamed
            ):
                with self.assertRaisesRegex(BackupError, "could not be read"):
                    run_backup(cfg, manual=True)
                from macup_tool.status import load_status

                self.assertEqual(load_status()["last_result"], "failed")


if __name__ == "__main__":
    unittest.main()
