import unittest
import os
import tempfile
from pathlib import Path

from unittest.mock import patch

from macup_tool.config import default_config, fresh_repository_path, load_config, repository, validate_config


class ConfigTests(unittest.TestCase):
    def test_default_config_is_valid_without_sources(self):
        cfg = default_config()
        self.assertEqual(validate_config(cfg, require_sources=False), [])
        self.assertFalse(cfg["repository_selected"])

    def test_fresh_repository_path_uses_new_restic_folder(self):
        path = fresh_repository_path()
        self.assertRegex(path, r"^MacUp/[^/]+/restic-\d{8}-\d{6}-[0-9a-f]{6}$")

    def test_repository_mode_validation(self):
        cfg = default_config()
        cfg["repository_mode"] = "sideways"
        errors = validate_config(cfg, require_sources=False)
        self.assertTrue(any("repository mode" in error for error in errors))

    def test_repository_defaults_to_rclone_remote_and_path(self):
        cfg = default_config()
        cfg["remote_name"] = "remote"
        cfg["repository_path"] = "Backups/restic"
        self.assertEqual(repository(cfg), "rclone:remote:Backups/restic")

    def test_repository_override_can_be_local_path(self):
        cfg = default_config()
        cfg["repository"] = "/tmp/macup-repo"
        self.assertEqual(repository(cfg), "/tmp/macup-repo")

    def test_remote_name_rejects_rclone_delimiters(self):
        cfg = default_config()
        cfg["remote_name"] = "bad:remote"
        errors = validate_config(cfg, require_sources=False)
        self.assertTrue(any("remote name" in error for error in errors))

    def test_repository_path_rejects_absolute_or_parent_segments(self):
        cfg = default_config()
        cfg["repository_path"] = "/MacUp/restic"
        errors = validate_config(cfg, require_sources=False)
        self.assertTrue(any("repository path must be relative" in error for error in errors))
        cfg["repository_path"] = "MacUp/../restic"
        errors = validate_config(cfg, require_sources=False)
        self.assertTrue(any("cannot contain . or .." in error for error in errors))

    def test_flat_mode_rejects_duplicate_basenames(self):
        cfg = default_config()
        cfg["path_mode"] = "flat"
        cfg["sources"] = ["/Users/a/Documents", "/Users/b/Documents"]
        errors = validate_config(cfg, require_sources=True)
        self.assertTrue(any("duplicate folder name" in error for error in errors))

    def test_require_sources_rejects_missing_source_folder(self):
        cfg = default_config()
        cfg["sources"] = ["/tmp/macup-missing-source-folder"]
        errors = validate_config(cfg, require_sources=True)
        self.assertTrue(any("does not exist" in error for error in errors))

    def test_require_sources_rejects_file_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_file = Path(tmp) / "not-a-folder"
            source_file.write_text("x", encoding="utf-8")
            cfg = default_config()
            cfg["sources"] = [str(source_file)]
            errors = validate_config(cfg, require_sources=True)
            self.assertTrue(any("must be a folder" in error for error in errors))

    def test_corrupt_config_is_moved_aside(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MACUP_CONFIG_DIR": tmp}):
                config_path = Path(tmp) / "config.json"
                config_path.write_text("{not json", encoding="utf-8")
                cfg = load_config()
                self.assertEqual(cfg["version"], default_config()["version"])
                self.assertFalse(config_path.exists())
                self.assertTrue(list(Path(tmp).glob("config.json.corrupt-*")))

    def test_config_is_written_user_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MACUP_CONFIG_DIR": tmp}):
                from macup_tool.config import save_config

                save_config(default_config())
                mode = os.stat(Path(tmp) / "config.json").st_mode & 0o777
                self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
