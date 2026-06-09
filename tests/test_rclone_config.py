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


if __name__ == "__main__":
    unittest.main()
