"""S0 integration evidence using temporary, synthetic stores only.

Acquisition may roll a prepared FileStore journal forward. The projection itself
starts after that existing transaction boundary and must not perform any I/O.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from observer.cooperative import CooperativeState
from observer.file_store import StateError
from observer.state_observations import observations
from observer.store import open_store
from observer.ui_projection import project


class UiProjectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.work = Path(self.temporary.name)
        self.root = self.work / "state"
        self.state = CooperativeState(self.root)
        self.report_counter = 0

    def tearDown(self):
        self.temporary.cleanup()

    def saved(self, task):
        return self.state.read(task)["state"]

    def create(self, task, sessions=("session-a",)):
        self.state.create(task, "Synthetic " + task, sessions[0])
        for session in sessions[1:]:
            self.state.link(task, session, self.saved(task)["revision"])
        for session in sessions:
            self.state.reporting(task, session, True, self.saved(task)["revision"])

    @staticmethod
    def define(item="progress", session="session-a"):
        return {"op": "define", "item_id": item, "title": "Synthetic progress",
                "value": {"message": "합성 보고", "checks": ["synthetic test"]},
                "status": "reported", "evidence": [{"session_id": session,
                    "reference": "synthetic:" + item, "kind": "session_event"}]}

    def request(self, changes, session="session-a"):
        self.report_counter += 1
        return {"schema_version": 1, "report_id": "report-" + str(self.report_counter),
                "session_id": session,
                "expected_revisions": {task: self.saved(task)["revision"] for task in changes},
                "changes": changes}

    def files(self):
        return {str(path.relative_to(self.work)): path.read_bytes()
                for path in self.work.rglob("*") if path.is_file()}

    def pure_project(self, rows, **kwargs):
        # No acquisition/recovery is permitted inside this boundary.
        with ExitStack() as stack:
            for name in ("builtins.open", "io.open", "os.open", "sqlite3.connect", "subprocess.Popen"):
                stack.enter_context(patch(name, side_effect=AssertionError("projection attempted I/O")))
            return project(rows, **kwargs)

    def observation_store(self):
        workspace = self.work / "personal"
        workspace.mkdir()
        source = self.work / "source" / "sessions.db"
        source.parent.mkdir()
        source.write_bytes(b"synthetic Devin source sentinel; never open or modify")
        database = self.work / "observer.sqlite3"
        db = open_store(database, source)
        for sid in ("session-a", "session-b"):
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?)",
                       (sid, "Synthetic " + sid, str(workspace), 1, "current", 100))
            db.execute("INSERT INTO source_nodes VALUES(?,?,?,?,?,?,?)",
                       (sid, 1, None, "m1", "assistant", "current-hash", 1))
            db.execute("INSERT INTO discovered_resources VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (sid, str(workspace), sid, 1, "current-hash", "pr",
                        "https://github.com/example/synthetic/pull/42", "pr:42",
                        "mentioned", "synthetic:" + sid, 100))
            db.execute("INSERT INTO plan_records VALUES(?,?,?,?,?,?,?)",
                       (sid, 1, "current-hash", "m1", "todo-1", "synthetic-plan",
                        json.dumps([{"content": "Plan for " + sid, "source_status": "pending"}])))
        db.execute("INSERT INTO discovered_resources VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ("stale", str(workspace), "session-a", 1, "superseded-hash", "ticket",
                    "LAB-OLD", "ticket:LAB-OLD", "mentioned", "synthetic:old", 90))
        db.execute("INSERT INTO collector_status VALUES(1,100,110,?)", ("synthetic collector failure",))
        db.commit()
        db.close()
        config = self.work / "config.json"
        config.write_text(json.dumps({"version": 1, "workspaces": [str(workspace)],
                                     "source": str(source), "store": str(database), "port": 8765}))
        return config

    def test_real_store_and_observations_are_preserved_without_projection_io(self):
        self.create("task-a", ("session-a", "session-b"))
        self.create("task-b")
        self.state.report(self.request({"task-a": [self.define()]}))
        self.state.report(self.request({"task-a": [self.define("next", "session-b")]}, "session-b"))
        self.state.note("task-a", "Keep human context", self.saved("task-a")["revision"])
        self.state.pin("task-a", "progress", True, self.saved("task-a")["revision"])
        self.state.reporting("task-b", "session-a", False, self.saved("task-b")["revision"])
        config = self.observation_store()
        expected = self.saved("task-a")
        before = self.files()
        observed = observations(config, ["session-a", "session-b"])
        rows = self.state.list()
        projection = self.pure_project(rows, observations=observed)
        self.assertEqual(self.files(), before)
        self.assertEqual(projection["sessions"]["session-a"]["task_ids"], ["task-a", "task-b"])
        self.assertTrue(projection["contributions"]["task-a"]["session-a"]["reporting"])
        self.assertFalse(projection["contributions"]["task-b"]["session-a"]["reporting"])
        self.assertEqual(projection["tasks"]["task-a"]["metadata"]["notes"], expected["notes"])
        self.assertEqual(projection["items"]["task-a"], {i["item_id"]: i for i in expected["items"]})
        self.assertTrue(projection["items"]["task-a"]["progress"]["pinned"])
        candidates = list(projection["resource_candidates"].values())
        self.assertEqual(len(candidates), 1)  # Superseded node is not current evidence.
        self.assertEqual(candidates[0]["association"], "candidate")
        self.assertEqual(candidates[0]["external_status"], "unknown")
        self.assertEqual({s["session_id"] for s in candidates[0]["sources"]}, {"session-a", "session-b"})
        self.assertEqual(set(candidates[0]["related_task_ids"]), {"task-a", "task-b"})
        for sid in ("session-a", "session-b"):
            plans = projection["sessions"][sid]["plan_snapshots"]
            self.assertEqual(plans[0]["items"][0]["content"], "Plan for " + sid)
        self.assertEqual(projection["health"]["observations"]["collector"], observed["collector"])
        restarted = CooperativeState(self.root)
        self.assertEqual(self.pure_project(restarted.list(), observations=observations(config)), projection)
        self.assertEqual(self.files(), before)

    def test_external_edit_and_missing_good_state_do_not_hide_healthy_tasks(self):
        self.create("edited")
        self.create("healthy")
        self.state.report(self.request({"edited": [self.define()]}))
        committed = self.saved("edited")
        edited_path = self.root / "tasks" / "edited" / "state.json"
        outside_edit = {**committed, "title": "Uncommitted external title"}
        edited_path.write_text(json.dumps(outside_edit))
        broken_path = self.root / "tasks" / "broken" / "state.json"
        broken_path.parent.mkdir()
        broken_path.write_bytes(b'{"unfinished":')
        before = self.files()
        rows = self.state.list()
        projection = self.pure_project(rows)
        fallback = projection["tasks"]["edited"]
        self.assertEqual(fallback["health"], "external_edit")
        self.assertEqual(fallback["display_origin"], "last_good")
        self.assertEqual(fallback["displayed_updated_at"], committed["updated_at"])
        self.assertEqual(fallback["metadata"]["title"], committed["title"])
        self.assertTrue(fallback["errors"])
        broken = projection["tasks"]["broken"]
        self.assertEqual(broken["health"], "invalid")
        self.assertEqual(broken["display_origin"], "unavailable")
        self.assertIsNone(broken["metadata"])
        self.assertEqual(projection["tasks"]["healthy"]["display_origin"], "current")
        self.assertEqual(self.files(), before)

    def interrupt_pair(self):
        request = self.request({task: [self.define("pair")] for task in ("task-a", "task-b")})
        original = self.state.store._atomic
        def fail_second(path, data):
            if path.name == "state.json" and path.parent.name == "task-b":
                raise OSError("synthetic interruption after first task write")
            original(path, data)
        with patch.object(self.state.store, "_atomic", fail_second):
            with self.assertRaises(StateError) as caught:
                self.state.report(request)
            self.assertEqual(caught.exception.code, "storage_io")
        return request

    def test_acquisition_recovers_pending_pair_before_projection_preserving_controls(self):
        for task in ("task-a", "task-b"):
            self.create(task)
        self.state.report(self.request({"task-a": [self.define("decision")]}))
        self.state.note("task-a", "Human decision remains", self.saved("task-a")["revision"])
        self.state.pin("task-a", "decision", True, self.saved("task-a")["revision"])
        before_states = {task: self.saved(task) for task in ("task-a", "task-b")}
        request = self.interrupt_pair()
        interrupted_files = self.files()
        for task, delta in (("task-a", 1), ("task-b", 0)):
            raw = json.loads((self.root / "tasks" / task / "state.json").read_text())
            self.assertEqual(raw["revision"], before_states[task]["revision"] + delta)
        restarted = CooperativeState(self.root)
        rows = restarted.list()  # This existing acquisition boundary writes recovery.
        recovered_files = self.files()
        self.assertNotEqual(recovered_files, interrupted_files)
        self.assertFalse(list((self.root / ".transactions").glob("*.json")))
        projection = self.pure_project(rows)
        for task in before_states:
            self.assertEqual(projection["tasks"][task]["display_origin"], "current")
            self.assertEqual(projection["tasks"][task]["displayed_revision"], before_states[task]["revision"] + 1)
            self.assertEqual(projection["items"][task]["pair"]["report_id"], request["report_id"])
        self.assertEqual(projection["tasks"]["task-a"]["metadata"]["notes"], before_states["task-a"]["notes"])
        self.assertEqual(projection["items"]["task-a"]["decision"], before_states["task-a"]["items"][0])
        self.assertTrue(restarted.report(request)["replayed"])
        stale = {**request, "report_id": "stale-retry"}
        with self.assertRaises(StateError) as caught:
            restarted.report(stale)
        self.assertEqual(caught.exception.code, "revision_conflict")
        pinned = self.request({"task-a": [{"op": "update", "item_id": "decision", "value": "overwrite"}]})
        with self.assertRaises(StateError) as caught:
            restarted.report(pinned)
        self.assertEqual(caught.exception.code, "pinned_item")
        self.assertEqual(self.pure_project(restarted.list()), projection)
        self.assertEqual(self.files(), recovered_files)

    def test_conflicting_pending_pair_projects_precommit_states_and_keeps_other_task(self):
        for task in ("task-a", "task-b", "healthy"):
            self.create(task)
        before_states = {task: self.saved(task) for task in ("task-a", "task-b")}
        self.interrupt_pair()
        (self.root / "tasks" / "task-b" / "state.json").write_bytes(b"uncommitted external edit")
        before_files = self.files()
        projection = self.pure_project(CooperativeState(self.root).list())
        for task in before_states:
            row = projection["tasks"][task]
            self.assertEqual(row["health"], "unavailable")
            self.assertEqual(row["display_origin"], "last_good")
            self.assertEqual(row["displayed_revision"], before_states[task]["revision"])
            self.assertEqual(row["errors"][0]["code"], "recovery_conflict")
            self.assertNotIn("pair", projection["items"][task])
        self.assertEqual(projection["tasks"]["healthy"]["display_origin"], "current")
        self.assertEqual(self.files(), before_files)
        self.assertEqual(len(list((self.root / ".transactions").glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
