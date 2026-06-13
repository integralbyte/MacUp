import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from macup_tool.backup import run_backup
from macup_tool.config import default_config, save_config
from macup_tool.restore import restore_snapshot
from macup_tool.status import load_status


@unittest.skipUnless(shutil.which("restic"), "restic is not installed")
class ResticIntegrationTests(unittest.TestCase):
    def test_local_repo_backup_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "hello.txt").write_text("hello macup", encoding="utf-8")
            repo = root / "repo"
            restore_target = root / "restore"
            env = {
                "MACUP_CONFIG_DIR": str(root / "config"),
                "MACUP_STATE_DIR": str(root / "state"),
                "MACUP_RESTIC_PASSWORD": "integration-test-password",
            }
            with patch.dict(os.environ, env), patch("macup_tool.backup.launchd.reload_later"):
                cfg = default_config()
                cfg["repository"] = str(repo)
                cfg["sources"] = [str(source)]
                cfg["retention_count"] = 2
                cfg["initialized"] = True
                save_config(cfg)
                self.assertEqual(run_backup(cfg, manual=True), 0)
                self.assertEqual(load_status()["last_result"], "success")
                restore_snapshot(cfg, snapshot="latest", target=str(restore_target))
                restored = list(restore_target.rglob("hello.txt"))
                self.assertEqual(len(restored), 1)
                self.assertEqual(restored[0].read_text(encoding="utf-8"), "hello macup")


if __name__ == "__main__":
    unittest.main()
