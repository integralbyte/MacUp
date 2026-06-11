import unittest
from unittest.mock import patch

from macup_tool.process import CommandResult
from macup_tool.snapshots import SnapshotError, list_snapshots


class SnapshotTests(unittest.TestCase):
    def test_list_snapshots_uses_json_line_when_restic_prints_warning(self):
        output = 'warning: transient remote message\n[{"id":"abc123","time":"2026-06-11T10:00:00Z","tags":["macup"]}]'
        with patch("macup_tool.snapshots.run_streamed", return_value=CommandResult([], 0, output)):
            snapshots = list_snapshots({})

        self.assertEqual(snapshots[0]["id"], "abc123")

    def test_list_snapshots_reports_non_json_without_raw_decode_error(self):
        with patch("macup_tool.snapshots.run_streamed", return_value=CommandResult([], 0, "not json")):
            with self.assertRaises(SnapshotError) as context:
                list_snapshots({})

        self.assertIn("not JSON", str(context.exception))
        self.assertNotIn("Expecting value", str(context.exception))


if __name__ == "__main__":
    unittest.main()
