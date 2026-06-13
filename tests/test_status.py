import unittest
from datetime import timedelta
from unittest.mock import patch

from macup_tool.config import default_config
from macup_tool.status import GREEN, ORANGE, RED, default_status, is_due, is_stale, summarize, xbar_output
from macup_tool.timeutil import iso, utc_now


class StatusTests(unittest.TestCase):
    def test_missing_success_is_stale_and_due(self):
        cfg = default_config()
        status = default_status()
        self.assertTrue(is_stale(cfg, status))
        self.assertTrue(is_due(cfg, status))
        self.assertEqual(summarize(cfg, status)["color"], RED)

    def test_recent_success_is_green(self):
        cfg = default_config()
        cfg["initialized"] = True
        status = default_status()
        status["last_result"] = "success"
        status["last_success_at"] = iso(utc_now() - timedelta(hours=1))
        self.assertFalse(is_stale(cfg, status))
        self.assertEqual(summarize(cfg, status)["color"], GREEN)

    def test_due_allows_five_minute_early_scheduler_wakeup(self):
        cfg = default_config()
        status = default_status()
        status["last_result"] = "success"
        now = utc_now()

        status["last_success_at"] = iso(now - timedelta(hours=23, minutes=56))
        self.assertFalse(is_stale(cfg, status, now=now))
        self.assertTrue(is_due(cfg, status, now=now))

        status["last_success_at"] = iso(now - timedelta(hours=23, minutes=54))
        self.assertFalse(is_due(cfg, status, now=now))

    def test_running_status_is_orange(self):
        cfg = default_config()
        cfg["initialized"] = True
        status = default_status()
        status["state"] = "running"
        self.assertEqual(summarize(cfg, status)["color"], ORANGE)

    def test_xbar_output_has_actions(self):
        cfg = default_config()
        cfg["initialized"] = True
        status = default_status()
        with patch("macup_tool.status.manager_state.probe", return_value={"running": False}), patch(
            "macup_tool.status.load_restore_status_for_xbar", return_value={"state": "idle"}
        ):
            output = xbar_output(cfg, status, "/tmp/macup")
        self.assertTrue(output.startswith("● |"))
        self.assertIn("Last never │ Next", output)
        self.assertNotIn("Sources:", output)
        self.assertIn("Backup Now", output)
        self.assertIn("Open Manager", output)
        self.assertIn("param1=manager param2=--detach", output)
        self.assertNotIn("Close Manager", output)
        self.assertIn("color=", output)

    def test_xbar_marks_running_manager(self):
        cfg = default_config()
        cfg["initialized"] = True
        status = default_status()
        with patch("macup_tool.status.manager_state.probe", return_value={"running": True}), patch(
            "macup_tool.status.load_restore_status_for_xbar", return_value={"state": "idle"}
        ):
            output = xbar_output(cfg, status, "/tmp/macup")
        self.assertTrue(output.startswith("● ▾ | color=#d1242f"))
        self.assertNotIn("Manager server running", output)
        self.assertIn("Close Manager", output)
        self.assertIn("color=#fb8c00", output)
        self.assertIn("param1=manager param2=--stop", output)

    def test_xbar_shows_backup_progress(self):
        cfg = default_config()
        cfg["initialized"] = True
        status = default_status()
        status["state"] = "running"
        status["active_run_id"] = "run"
        status["progress_percent"] = 42.4
        status["progress_current"] = 1
        status["progress_total"] = 2
        with patch("macup_tool.status.manager_state.probe", return_value={"running": False}), patch(
            "macup_tool.status.load_restore_status_for_xbar", return_value={"state": "idle"}
        ):
            output = xbar_output(cfg, status, "/tmp/macup")
        self.assertTrue(output.startswith("● |"))
        self.assertIn("Backup: 42.4% (1/2)", output)
        self.assertIn("Stop Backup", output)
        self.assertIn("param1=backup param2=--stop", output)

    def test_xbar_shows_backup_warning_without_turning_red(self):
        cfg = default_config()
        cfg["initialized"] = True
        status = default_status()
        status["last_result"] = "success"
        status["last_success_at"] = iso(utc_now() - timedelta(hours=1))
        status["last_warning"] = "Backup completed, but /Users/ace/Pictures/Photos Library.photoslibrary was skipped."
        status["backup_issues"] = [
            {"item": "/Users/ace/Pictures/Photos Library.photoslibrary", "message": "operation not permitted"},
            {"item": "/Users/ace/Music/Music", "message": "operation not permitted"},
            {"item": "/Users/ace/Movies/TV", "message": "operation not permitted"},
        ]
        with patch("macup_tool.status.manager_state.probe", return_value={"running": False}), patch(
            "macup_tool.status.load_restore_status_for_xbar", return_value={"state": "idle"}
        ):
            output = xbar_output(cfg, status, "/tmp/macup")
        self.assertTrue(output.startswith("● | color=#2da44e"))
        self.assertIn("Warning: 3 source items were skipped.", output)
        self.assertNotIn("Photos Library", output)
        self.assertNotIn("Last error:", output)

    def test_xbar_shows_restore_progress_without_changing_green_icon(self):
        cfg = default_config()
        cfg["initialized"] = True
        status = default_status()
        status["last_result"] = "success"
        status["last_success_at"] = iso(utc_now() - timedelta(hours=1))
        restore = {"state": "running", "progress_percent": 36, "latest_log": "/tmp/restore.log"}
        with patch("macup_tool.status.manager_state.probe", return_value={"running": False}), patch(
            "macup_tool.status.load_restore_status_for_xbar", return_value=restore
        ):
            output = xbar_output(cfg, status, "/tmp/macup")
        self.assertTrue(output.startswith("● |"))
        self.assertIn("Restore: 36%", output)
        self.assertIn("Restore Log", output)


if __name__ == "__main__":
    unittest.main()
