import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from macup_tool.config import default_config
from macup_tool.manager import _begin_rclone_stage, _commit_rclone_stage


class ManagerRcloneStageTests(unittest.TestCase):
    def test_stage_does_not_replace_real_config_until_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            state_dir = Path(tmp) / "state"
            real = config_dir / "rclone.conf"
            real.parent.mkdir(parents=True)
            real.write_text("old config", encoding="utf-8")
            cfg = default_config()
            cfg["rclone_config_path"] = str(real)
            server = SimpleNamespace(rclone_stage_path="", rclone_state="")
            with patch.dict(os.environ, {"MACUP_CONFIG_DIR": str(config_dir), "MACUP_STATE_DIR": str(state_dir)}):
                staged = _begin_rclone_stage(server, cfg)
                stage = Path(staged["rclone_config_path"])
                self.assertNotEqual(stage, real)
                self.assertEqual(real.read_text(encoding="utf-8"), "old config")
                stage.write_text("new config", encoding="utf-8")
                saved = _commit_rclone_stage(server, cfg)
                self.assertEqual(real.read_text(encoding="utf-8"), "new config")
                self.assertFalse(stage.exists())
                self.assertTrue(saved["rclone_configured"])


if __name__ == "__main__":
    unittest.main()
