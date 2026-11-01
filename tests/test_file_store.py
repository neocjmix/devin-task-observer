import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from observer.file_store import FileStore, MAX_STATE_BYTES, StateError


def validate(state):
    if state.get("schema_version") != 1:
        raise ValueError("Unsupported task schema")


def state(task, revision=1, value="initial"):
    return {"schema_version": 1, "task_id": task, "revision": revision, "value": value}


class FileStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "state"
        self.store = FileStore(self.root, validator=validate)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, *tasks):
        return self.store.transact("create-" + "-".join(tasks), {"create": tasks},
                                   {task: None for task in tasks},
                                   lambda _: {task: state(task) for task in tasks})

    def update(self, task, report="update", revision=1, value="changed"):
        return self.store.transact(report, {"value": value}, {task: revision},
                                   lambda _: {task: state(task, revision + 1, value)})

    def run_child(self, source, *args):
        return subprocess.run([sys.executable, "-c", source, str(self.root), *args],
                              cwd=Path(__file__).resolve().parents[1], text=True,
                              capture_output=True, timeout=10)

    def test_persist_history_and_revision_conflict_contains_current_state(self):
        self.create("a")
        self.update("a")
        with self.assertRaises(StateError) as raised:
            self.update("a", "stale")
        self.assertEqual(raised.exception.code, "revision_conflict")
        self.assertEqual(raised.exception.details["current"]["a"]["state"]["value"], "changed")
        reopened = FileStore(self.root, validate)
        self.assertEqual([s["revision"] for s in reopened.history("a")], [1, 2])
        self.assertEqual(reopened.read("a")["state"], state("a", 2, "changed"))

    def test_idempotent_success_and_changed_request_refusal(self):
        self.create("a")
        first = self.update("a")
        second = self.store.transact("update", {"value": "changed"}, {"a": 1},
                                     lambda _: self.fail("Replay must not invoke transform"))
        self.assertTrue(second["replayed"])
        self.assertEqual(first["tasks"], second["tasks"])
        with self.assertRaises(StateError) as raised:
            self.update("a", value="different")
        self.assertEqual(raised.exception.code, "report_id_reused")
        self.assertEqual(len(self.store.history("a")), 2)

    def test_read_dependencies_compared_without_incrementing(self):
        self.create("a", "b")
        self.store.transact("dependent", {}, {"a": 1, "b": 1},
                            lambda _: {"a": state("a", 2)})
        self.assertEqual(self.store.read("b")["state"]["revision"], 1)
        with self.assertRaises(StateError) as raised:
            self.store.transact("dependent-stale", {}, {"a": 1, "b": 1},
                                lambda _: {"b": state("b", 2)})
        self.assertEqual(raised.exception.code, "revision_conflict")

    def test_invalid_transform_is_atomic_and_does_not_mutate_input(self):
        self.create("a", "b")
        def invalid(states):
            states["a"]["value"] = "mutated"
            return {"a": state("a", 2), "b": {**state("b", 2), "schema_version": 99}}
        with self.assertRaises(StateError):
            self.store.transact("invalid", {}, {"a": 1, "b": 1}, invalid)
        self.assertEqual(self.store.read("a")["state"], state("a"))
        self.assertEqual(self.store.read("b")["state"], state("b"))
        self.assertFalse(list((self.root / ".transactions").glob("*.json")))

    def test_independent_processes_only_one_compare_and_commit_wins(self):
        self.create("a")
        source = """
import json, pathlib, sys, time
from observer.file_store import FileStore, StateError
root, name = sys.argv[1:]
store=FileStore(root)
snapshot=store.read('a')['state']
pathlib.Path(root, name+'.ready').touch()
while not pathlib.Path(root,'go').exists(): time.sleep(.01)
snapshot['revision'] += 1
snapshot['value'] = name
try:
    store.transact(name, {'value':name}, {'a':1}, lambda _: {'a':snapshot})
    print('committed')
except StateError as exc: print(exc.code)
"""
        processes = [subprocess.Popen([sys.executable, "-c", source, str(self.root), name],
                                      cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for name in ("one", "two")]
        import time
        deadline = time.monotonic() + 5
        try:
            while not all((self.root / (name + ".ready")).exists() for name in ("one", "two")):
                if time.monotonic() > deadline:
                    self.fail("Concurrent writers did not become ready")
                time.sleep(.01)
            (self.root / "go").touch()
            outputs = [p.communicate(timeout=5) for p in processes]
            self.assertEqual(sorted(out.strip() for out, _ in outputs), ["committed", "revision_conflict"])
            self.assertTrue(all(p.returncode == 0 for p in processes), outputs)
            self.assertEqual(len(self.store.history("a")), 2)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def crash_multi_task(self):
        source = """
import os, sys
from observer.file_store import FileStore
store=FileStore(sys.argv[1])
original=store._atomic
def crash(path,data):
    original(path,data)
    if path.name=='state.json' and path.parent.name=='a': os._exit(77)
store._atomic=crash
def update(states):
    for value in states.values(): value.update(revision=2,value='both changed')
    return states
store.transact('pair', {'value':'both changed'}, {'a':1,'b':1}, update)
"""
        result = self.run_child(source)
        self.assertEqual(result.returncode, 77, result.stderr)

    def test_process_crash_mid_multi_file_commit_rolls_forward_before_read(self):
        self.create("a", "b")
        self.crash_multi_task()
        self.assertEqual(json.loads((self.root / "tasks/a/state.json").read_text())["revision"], 2)
        self.assertEqual(json.loads((self.root / "tasks/b/state.json").read_text())["revision"], 1)
        snapshots = self.store.list()
        self.assertTrue(all(s["health"] == "ok" and s["state"]["revision"] == 2 for s in snapshots))
        replay = self.store.transact("pair", {"value": "both changed"}, {"a": 1, "b": 1},
                                    lambda _: self.fail("Recovered transaction must replay"))
        self.assertTrue(replay["replayed"])
        self.assertFalse(list((self.root / ".transactions").glob("*.json")))

    def test_success_before_response_crash_replays_without_new_revision(self):
        self.create("a")
        source = """
import os, sys
from observer.file_store import FileStore
store=FileStore(sys.argv[1]); original=store._finish
def crash(*args): original(*args); os._exit(78)
store._finish=crash
def update(states): states['a'].update(revision=2,value='changed'); return states
store.transact('update', {'value':'changed'}, {'a':1}, update)
"""
        result = self.run_child(source)
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertTrue(self.update("a")["replayed"])
        self.assertEqual(len(self.store.history("a")), 2)

    def test_external_edit_during_pending_recovery_is_preserved_and_isolated(self):
        self.create("a", "b", "healthy")
        self.crash_multi_task()
        path = self.root / "tasks/b/state.json"
        path.write_text('{"manual": "unfinished"')
        raw = path.read_bytes()
        for task in ("a", "b"):
            snapshot = self.store.read(task)
            self.assertEqual(snapshot["health"], "unavailable")
            self.assertIsNone(snapshot["state"])
            self.assertEqual(snapshot["last_good"]["revision"], 1)
            self.assertEqual([s["revision"] for s in self.store.history(task)], [1])
        self.assertEqual(self.store.read("healthy")["health"], "ok")
        self.update("healthy", "healthy-change")
        self.assertEqual(path.read_bytes(), raw)
        with self.assertRaises(StateError) as raised:
            self.store.transact("pair", {"different": True}, {"healthy": 2},
                                lambda _: {"healthy": state("healthy", 3)})
        self.assertEqual(raised.exception.code, "report_id_reused")
        self.assertEqual(len(list((self.root / ".transactions").glob("*.json"))), 1)

    def test_raw_edit_and_invalid_schema_transparency_and_explicit_recovery(self):
        self.create("a", "healthy")
        path = self.root / "tasks/a/state.json"
        changed = state("a", value="direct edit")
        path.write_text(json.dumps(changed))
        snapshot = self.store.read("a")
        self.assertEqual(snapshot["health"], "external_edit")
        self.assertEqual(snapshot["state"]["value"], "direct edit")
        self.assertEqual(snapshot["last_good"]["value"], "initial")
        with self.assertRaises(StateError) as raised:
            self.update("a")
        self.assertEqual(raised.exception.code, "state_unavailable")
        path.write_text('{"schema_version":99,"revision":1}')
        snapshot = self.store.read("a")
        self.assertEqual(snapshot["health"], "invalid")
        self.assertEqual(self.store.read("healthy")["health"], "ok")
        digest = snapshot["content_digest"]
        original = path.read_bytes()
        result = self.store.recover("a", digest)
        self.assertTrue(result["restored"])
        self.assertEqual(Path(result["backup"]).read_bytes(), original)
        self.assertEqual(self.store.read("a")["health"], "ok")
        self.assertEqual(len(self.store.history("a")), 1)

    def test_recovery_refuses_changed_raw_digest_and_handles_missing_file(self):
        self.create("a")
        path = self.root / "tasks/a/state.json"
        path.write_text("broken")
        digest = self.store.read("a")["content_digest"]
        path.write_text("changed again")
        with self.assertRaises(StateError) as raised:
            self.store.recover("a", digest)
        self.assertEqual(raised.exception.code, "revision_conflict")
        self.assertEqual(path.read_text(), "changed again")
        path.unlink()
        self.assertEqual(self.store.read("a")["health"], "external_edit")
        self.assertTrue(self.store.recover("a", None)["restored"])

    def test_invalid_json_duplicate_keys_and_limits_preserve_files(self):
        self.create("a")
        path = self.root / "tasks/a/state.json"
        for raw in (b'{"revision":1,"revision":2}', b'{"revision":NaN}', b"{"):
            path.write_bytes(raw)
            snapshot = self.store.read("a")
            self.assertEqual(snapshot["health"], "invalid")
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(snapshot["last_good"]["revision"], 1)
        with self.assertRaises(StateError) as raised:
            self.store.transact("huge", {}, {"b": None},
                                lambda _: {"b": state("b", value="x" * MAX_STATE_BYTES)})
        self.assertEqual(raised.exception.code, "size_limit")
        self.assertIsNone(self.store.read("b")["state"])

    def test_path_symlink_and_hardlink_refusal(self):
        for unsafe in ("../a", "a/b", "", ".", "..", "/tmp/outside"):
            with self.assertRaises(StateError):
                self.store.read(unsafe)
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (self.root / "tasks/linked").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.store.read("linked")["health"], "invalid")
        self.assertFalse(list(outside.iterdir()))
        alias = Path(self.tmp.name) / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(StateError):
            FileStore(alias)
        self.create("a")
        os.link(self.root / "tasks/a/state.json", outside / "state-copy")
        self.assertEqual(self.store.read("a")["health"], "invalid")

    def test_malformed_task_directory_does_not_hide_healthy_tasks(self):
        self.create("healthy")
        (self.root / "tasks/broken").write_text("not a directory")
        rows = {row["task_id"]: row for row in self.store.list()}
        self.assertEqual(rows["broken"]["health"], "invalid")
        self.assertEqual(rows["healthy"]["health"], "ok")

    def test_invalid_history_is_visible_and_never_replaced(self):
        self.create("a", "healthy")
        path = next((self.root / "tasks/a/history").glob("*.json"))
        path.write_text("invalid history")
        before = (self.root / "tasks/a/state.json").read_bytes()
        self.assertEqual(self.store.read("a")["health"], "invalid")
        with self.assertRaises(StateError):
            self.update("a")
        self.assertEqual(path.read_text(), "invalid history")
        self.assertEqual((self.root / "tasks/a/state.json").read_bytes(), before)
        self.assertEqual(self.store.read("healthy")["health"], "ok")

    def test_task_id_cannot_cross_storage_scope(self):
        self.create("a", "b")
        with self.assertRaises(StateError) as raised:
            self.store.transact("cross-scope", {}, {"a": 1}, lambda _: {"a": state("b", 2)})
        self.assertEqual(raised.exception.code, "invalid_state")
        path = self.root / "tasks/a/state.json"
        path.write_bytes((self.root / "tasks/b/state.json").read_bytes())
        snapshot = self.store.read("a")
        self.assertEqual(snapshot["health"], "invalid")
        self.assertEqual(snapshot["last_good"]["task_id"], "a")

    def test_corrupt_journal_is_preserved_and_fails_closed(self):
        self.create("a", "b")
        self.crash_multi_task()
        journal = next((self.root / ".transactions").glob("*.json"))
        journal.write_text("corrupt journal cannot identify affected tasks")
        with self.assertRaises(StateError) as raised:
            self.store.read("a")
        self.assertEqual(raised.exception.code, "invalid_json")
        self.assertEqual(journal.read_text(), "corrupt journal cannot identify affected tasks")

    def test_reads_load_latest_snapshot_but_history_checks_all_snapshots(self):
        self.create("a")
        self.update("a")
        with patch.object(self.store, "_history_state", wraps=self.store._history_state) as loaded:
            self.assertEqual(self.store.read("a")["state"]["revision"], 2)
            self.assertEqual(loaded.call_count, 1)
        with patch.object(self.store, "_history_state", wraps=self.store._history_state) as loaded:
            self.assertEqual(len(self.store.history("a")), 2)
            self.assertEqual(loaded.call_count, 2)

    def test_commit_lock_has_bounded_wait_across_processes(self):
        self.create("a")
        with self.store._locked():
            child = self.run_child("""
import sys
from observer.file_store import FileStore, StateError
try: FileStore(sys.argv[1], lock_timeout=.05).read('a')
except StateError as exc: print(exc.code)
""")
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.strip(), "busy")
        self.assertEqual(self.store.read("a")["health"], "ok")

    def test_io_failure_after_preparation_recovers_without_partial_visible_state(self):
        self.create("a", "b")
        original = self.store._atomic
        def fail_second(path, data):
            if path.name == "state.json" and path.parent.name == "b":
                raise OSError("simulated write interruption")
            original(path, data)
        with patch.object(self.store, "_atomic", fail_second):
            with self.assertRaises(StateError) as raised:
                self.store.transact("pair", {}, {"a": 1, "b": 1},
                                    lambda _: {"a": state("a", 2), "b": state("b", 2)})
            self.assertEqual(raised.exception.code, "storage_io")
        self.assertTrue(all(s["state"]["revision"] == 2 for s in self.store.list()))


if __name__ == "__main__":
    unittest.main()
