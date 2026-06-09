import unittest

from macup_tool.config import default_config, repository, validate_config


class ConfigTests(unittest.TestCase):
    def test_default_config_is_valid_without_sources(self):
        cfg = default_config()
        self.assertEqual(validate_config(cfg, require_sources=False), [])

    def test_repository_defaults_to_rclone_remote_and_path(self):
        cfg = default_config()
        cfg["remote_name"] = "remote"
        cfg["repository_path"] = "Backups/restic"
        self.assertEqual(repository(cfg), "rclone:remote:Backups/restic")

    def test_repository_override_can_be_local_path(self):
        cfg = default_config()
        cfg["repository"] = "/tmp/macup-repo"
        self.assertEqual(repository(cfg), "/tmp/macup-repo")

    def test_flat_mode_rejects_duplicate_basenames(self):
        cfg = default_config()
        cfg["path_mode"] = "flat"
        cfg["sources"] = ["/Users/a/Documents", "/Users/b/Documents"]
        errors = validate_config(cfg, require_sources=True)
        self.assertTrue(any("duplicate folder name" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
