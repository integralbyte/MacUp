import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from macup_tool import rclone_config
from macup_tool.config import default_config


class RcloneConfigTests(unittest.TestCase):
    def test_repository_subpath_rejects_parent_segments(self):
        with self.assertRaises(ValueError):
            rclone_config.normalize_repository_subpath("../snapshots")

    def test_repository_remote_path_joins_repo_and_subpath(self):
        cfg = default_config()
        cfg["remote_name"] = "remote"
        cfg["repository_path"] = "MacUp/host/restic"
        self.assertEqual(
            rclone_config.repository_remote_path(cfg, "snapshots"),
            "remote:MacUp/host/restic/snapshots",
        )

    def test_discover_repositories_finds_restic_markers_under_macup(self):
        cfg = default_config()
        cfg["remote_name"] = "remote"

        def list_remote(_config, path):
            return {
                "MacUp": [
                    {"Name": "host-a", "IsDir": True},
                    {"Name": "not-a-repo", "IsDir": True},
                ],
                "MacUp/host-a": [
                    {"Name": "restic", "IsDir": True},
                    {"Name": "notes", "IsDir": True},
                ],
                "MacUp/host-a/restic": [
                    {"Name": "config", "IsDir": False},
                    {"Name": "data", "IsDir": True},
                    {"Name": "index", "IsDir": True},
                    {"Name": "keys", "IsDir": True},
                    {"Name": "snapshots", "IsDir": True},
                ],
                "MacUp/host-a/notes": [
                    {"Name": "config", "IsDir": False},
                ],
                "MacUp/not-a-repo": [
                    {"Name": "documents", "IsDir": True},
                ],
                "MacUp/not-a-repo/documents": [],
            }[path]

        with patch("macup_tool.rclone_config.ensure_encrypted_config"), patch(
            "macup_tool.rclone_config._list_remote", side_effect=list_remote
        ), patch(
            "macup_tool.rclone_config._list_remote_recursive",
            return_value=[
                {"Name": "old", "IsDir": False, "ModTime": "2026-06-01T10:00:00Z"},
                {"Name": "new", "IsDir": False, "ModTime": "2026-06-09T13:52:06Z"},
            ],
        ):
            repos = rclone_config.discover_repositories(cfg)

        self.assertEqual([repo["path"] for repo in repos], ["MacUp/host-a/restic"])
        self.assertEqual(repos[0]["repository"], "rclone:remote:MacUp/host-a/restic")
        self.assertEqual(repos[0]["last_backup_at"], "2026-06-09T13:52:06Z")
        self.assertEqual(repos[0]["snapshot_file_count"], 2)

    def test_is_restic_repository_path_checks_required_markers(self):
        cfg = default_config()
        restic_items = [
            {"Name": "config", "IsDir": False},
            {"Name": "data", "IsDir": True},
            {"Name": "index", "IsDir": True},
            {"Name": "keys", "IsDir": True},
            {"Name": "snapshots", "IsDir": True},
        ]
        partial_items = [{"Name": "config", "IsDir": False}, {"Name": "data", "IsDir": True}]

        with patch("macup_tool.rclone_config.ensure_encrypted_config"), patch(
            "macup_tool.rclone_config._list_remote", return_value=restic_items
        ):
            self.assertTrue(rclone_config.is_restic_repository_path(cfg, "MacUp/host/restic"))

        with patch("macup_tool.rclone_config.ensure_encrypted_config"), patch(
            "macup_tool.rclone_config._list_remote", return_value=partial_items
        ):
            self.assertFalse(rclone_config.is_restic_repository_path(cfg, "MacUp/host/restic"))

    def test_repository_snapshot_info_compares_timezone_offsets(self):
        cfg = default_config()
        with patch(
            "macup_tool.rclone_config._list_remote_recursive",
            return_value=[
                {"Name": "offset", "IsDir": False, "ModTime": "2026-06-09T13:52:06+02:00"},
                {"Name": "utc", "IsDir": False, "ModTime": "2026-06-09T12:52:06Z"},
            ],
        ):
            info = rclone_config.repository_snapshot_info(cfg, "MacUp/host/restic")

        self.assertEqual(info["last_backup_at"], "2026-06-09T12:52:06Z")
        self.assertEqual(info["snapshot_file_count"], 2)

    def test_list_repository_returns_safe_item_shape(self):
        cfg = default_config()
        cfg["remote_name"] = "remote"
        cfg["repository_path"] = "Repo"
        result = Mock(
            returncode=0,
            stdout=json.dumps(
                [
                    {"Name": "snapshots", "Path": "snapshots", "IsDir": True, "Size": -1},
                    {"Name": "config", "Path": "config", "IsDir": False, "Size": 10, "Metadata": {"id": "secret"}},
                ]
            ),
        )
        with patch("macup_tool.rclone_config.ensure_encrypted_config"), patch(
            "macup_tool.rclone_config.subprocess.run", return_value=result
        ):
            remote_path, items = rclone_config.list_repository(cfg, "")
        self.assertEqual(remote_path, "remote:Repo")
        self.assertEqual(items[0]["name"], "snapshots")
        self.assertNotIn("Metadata", items[1])

    def test_repository_web_url_uses_private_graph_url_not_public_link(self):
        cfg = default_config()
        cfg["remote_name"] = "remote"
        cfg["repository_path"] = "MacUp/host name/restic"
        stat = Mock(returncode=0, stdout=json.dumps({"IsDir": True}))
        dump = Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "remote": {
                        "type": "onedrive",
                        "drive_id": "drive id",
                        "token": json.dumps({"access_token": "access-token"}),
                    }
                }
            ),
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({"webUrl": "https://tenant-my.sharepoint.com/personal/me/Documents/MacUp"}).encode()

        with patch("macup_tool.rclone_config.ensure_encrypted_config"), patch(
            "macup_tool.rclone_config.subprocess.run", side_effect=[stat, dump]
        ) as run, patch("macup_tool.rclone_config.urllib.request.urlopen", return_value=Response()) as urlopen:
            url = rclone_config.repository_web_url(cfg, "snapshots")

        self.assertEqual(url, "https://tenant-my.sharepoint.com/personal/me/Documents/MacUp")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn("lsjson", commands[0])
        self.assertIn("config", commands[1])
        self.assertIn("dump", commands[1])
        self.assertFalse(any("link" in command for command in commands for command in command))
        request = urlopen.call_args.args[0]
        self.assertIn("drives/drive%20id/root:/MacUp/host%20name/restic/snapshots", request.full_url)

    def test_repository_web_url_rejects_repository_override(self):
        cfg = default_config()
        cfg["repository"] = "/tmp/local-restic"
        with self.assertRaisesRegex(RuntimeError, "rclone repository locations"):
            rclone_config.repository_web_url(cfg, "snapshots")


if __name__ == "__main__":
    unittest.main()
