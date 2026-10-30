"""A moved store never silently accepts a stale CLI or in-flight writer."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from observer.cooperative import CooperativeState
from observer.file_store import FileStore, StateError, _encode
from observer.ui_reads import ReadCursors


class StoreRelocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'old-state'
        self.destination = Path(self.temp.name) / '.devin-task-observer/state'
        self.core = CooperativeState(self.root)
        self.root = self.core.store.root
        self.reads = ReadCursors(self.root)
        self.core.create('task', 'Synthetic task', request_id='create')
        self.lock_inode = (self.root / '.lock').stat().st_ino

    def marker(self, **extra):
        return {'schema_version': 1, 'new_root': str(self.destination), **extra}

    def publish(self, marker=None):
        self.core.store._atomic(self.root / '.relocated.json',
                                _encode(self.marker() if marker is None else marker))

    def inventory(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and p.name != '.relocated.json'}

    def assert_relocated(self, action):
        with self.assertRaises(StateError) as caught:
            action()
        self.assertEqual(caught.exception.code, 'relocated_store')
        self.assertEqual(caught.exception.details['new_root'], str(self.destination))
        self.assertIn(str(self.destination), str(caught.exception))

    def test_existing_instances_block_reads_writes_history_receipt_replay_and_cursors(self):
        before = self.inventory()
        with self.core.store._locked():
            self.publish(self.marker(reason='User selected a new local home'))
        operations = (
            lambda: self.core.read('task'),
            self.core.list,
            lambda: self.core.store.history('task'),
            lambda: self.core.store.history_page('task'),
            lambda: self.core.note('task', 'Must not be stored', 1, 'note'),
            lambda: self.core.create('task', 'Synthetic task', request_id='create'),
            self.reads.snapshot,
            lambda: self.reads.mark('task', 1, 1),
            lambda: FileStore(self.root).read('task'),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                self.assert_relocated(operation)
        self.assertEqual(self.inventory(), before)
        self.assertEqual((self.root / '.lock').stat().st_ino, self.lock_inode)
        self.assertFalse(self.destination.exists())

    def test_writer_already_waiting_on_original_lock_observes_marker_before_commit(self):
        before = self.inventory()
        waiting = threading.Event()
        original_flock = fcntl.flock

        def observe_wait(fd, operation):
            try:
                return original_flock(fd, operation)
            except BlockingIOError:
                waiting.set()
                raise

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.core.store._locked():
                with patch('observer.file_store.fcntl.flock', side_effect=observe_wait):
                    writer = pool.submit(self.core.note, 'task', 'Stale writer', 1, 'waiting-note')
                    self.assertTrue(waiting.wait(1), 'Writer did not wait on the existing lock')
                    self.assertFalse(writer.done())
                    self.publish()
            self.assert_relocated(lambda: writer.result(timeout=2))
        self.assertEqual(self.inventory(), before)
        self.assertEqual((self.root / '.lock').stat().st_ino, self.lock_inode)
        self.assertFalse(self.destination.exists())

    def test_malformed_markers_preserve_state_and_block_access(self):
        before = self.inventory()
        bad = [b'{', b'null', b'{}', b'[]',
               b'{"schema_version":1,"schema_version":1,"new_root":"/tmp/destination"}']
        for value in (
            {'schema_version': True, 'new_root': '/tmp/destination'},
            {'schema_version': 2, 'new_root': '/tmp/destination'},
            {'schema_version': 1, 'new_root': 'relative/state'},
            {'schema_version': 1, 'new_root': '~/state'},
            {'schema_version': 1, 'new_root': '/bad\x00path'},
            {'schema_version': 1, 'new_root': None},
            self.marker(reason=[]), self.marker(unrecognized=True),
        ):
            bad.append(json.dumps(value).encode())
        for raw in bad:
            with self.subTest(raw=raw):
                self.core.store._atomic(self.root / '.relocated.json', raw)
                with self.assertRaises(StateError) as caught:
                    self.core.note('task', 'Never stored', 1, 'invalid-marker')
                self.assertEqual(caught.exception.code, 'invalid_relocation_marker')
                self.assertEqual((self.root / '.relocated.json').read_bytes(), raw)
                self.assertEqual(self.inventory(), before)

    def test_symbolic_marker_is_not_followed_or_replaced(self):
        target = Path(self.temp.name) / 'external-marker.json'
        raw = _encode(self.marker())
        target.write_bytes(raw)
        marker = self.root / '.relocated.json'
        marker.symlink_to(target)
        before = self.inventory()
        with self.assertRaises(StateError) as caught:
            self.core.read('task')
        self.assertEqual(caught.exception.code, 'unsafe_path')
        self.assertTrue(marker.is_symlink())
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual(self.inventory(), before)

    def test_oversized_marker_blocks_without_truncating_or_repairing_it(self):
        raw = _encode(self.marker(reason='x' * 8192))
        self.core.store._atomic(self.root / '.relocated.json', raw)
        before = self.inventory()
        with self.assertRaises(StateError) as caught:
            self.core.list()
        self.assertEqual(caught.exception.code, 'size_limit')
        self.assertEqual((self.root / '.relocated.json').read_bytes(), raw)
        self.assertEqual(self.inventory(), before)

    def test_retired_store_guard_keeps_existing_precedence(self):
        self.publish()
        self.core.store._atomic(self.root / '.retired.json', b'{}')
        with self.assertRaises(StateError) as caught:
            self.core.read('task')
        self.assertEqual(caught.exception.code, 'retired_store')


if __name__ == '__main__':
    unittest.main()
