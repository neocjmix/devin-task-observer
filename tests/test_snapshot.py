import tempfile
import unittest
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from probe import snapshot
from probe.snapshot import scan, compare


class SnapshotTests(unittest.TestCase):
    def test_append_same_length_edit_delete_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / 'a'
            a.write_text('one')
            first, errors = scan(root, b'salt')
            self.assertEqual(errors, 0)
            self.assertEqual(compare(first, scan(root, b'salt')[0])['unchanged'], 1)
            a.write_text('two')
            edited = scan(root, b'salt')[0]
            self.assertEqual(compare(first, edited)['changed'], 1)
            (root / 'b').write_text('new')
            added = scan(root, b'salt')[0]
            self.assertEqual(compare(edited, added)['added'], 1)
            a.unlink()
            self.assertEqual(compare(added, scan(root, b'salt')[0])['removed'], 1)

    def test_symlinks_do_not_read_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'root'
            root.mkdir()
            outside = Path(tmp) / 'outside'
            outside.write_text('private')
            (root / 'link').symlink_to(outside)
            files, _ = scan(root, b'salt')
            self.assertEqual(files, {})

    def test_cli_keeps_previous_state_on_incomplete_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'root'
            root.mkdir()
            (root / 'message.txt').write_text('synthetic private body')
            state = Path(tmp) / 'state.json'
            with patch.object(sys, 'argv', ['snapshot', str(root), str(state)]), contextlib.redirect_stdout(io.StringIO()):
                snapshot.main()
            original = state.read_bytes()
            self.assertNotIn(b'synthetic private body', original)
            self.assertNotIn(b'message.txt', original)
            self.assertEqual(os.stat(state).st_mode & 0o777, 0o600)
            with patch.object(sys, 'argv', ['snapshot', str(root), str(state)]), patch.object(snapshot, 'scan', return_value=({}, 1)), contextlib.redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(SystemExit) as error:
                    snapshot.main()
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(json.loads(out.getvalue())['snapshot_committed'])
            self.assertEqual(state.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
