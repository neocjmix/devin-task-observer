"""Synthetic process acceptance tests for the file-first reporting CLI."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import contextlib
import io
from unittest.mock import patch

from observer.store import open_store


REPOSITORY = Path(__file__).resolve().parents[1]


class CooperativeCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.work = Path(self.temporary.name)
        self.root = self.work / "state"
        self.counter = 0

    def tearDown(self):
        self.temporary.cleanup()

    def cli(self, *arguments, ok=True, stdin=None):
        result = subprocess.run(
            [sys.executable, str(REPOSITORY / "observerctl"), "state", "--root", str(self.root), *map(str, arguments)],
            cwd=self.work, input=stdin, text=True, capture_output=True, timeout=10,
        )
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f"CLI did not return JSON: {result.returncode}: {result.stdout!r} {result.stderr!r}")
        self.assertEqual(value["ok"], ok, value)
        if ok:
            self.assertEqual(result.returncode, 0, value)
        else:
            self.assertNotEqual(result.returncode, 0, value)
        return value

    def read(self, task="task-t"):
        return self.cli("read", "--task", task)["state"]

    def revision(self, task="task-t"):
        return self.read(task)["revision"]

    def setup_task(self, task="task-t", sessions=("session-a", "session-b")):
        self.cli("create", "--task", task, "--title", "Synthetic task", "--session", sessions[0])
        for session in sessions[1:]:
            self.cli("link", "--task", task, "--session", session, "--expected-revision", self.revision(task))
        for session in sessions:
            self.cli("reporting", "--task", task, "--session", session, "--enabled", "on", "--expected-revision", self.revision(task))

    def report(self, changes, session="session-a", task="task-t", report_id=None, expected_revision=None):
        self.counter += 1
        return {
            "schema_version": 1,
            "report_id": report_id or f"report-{self.counter}",
            "session_id": session,
            "expected_revisions": {task: self.revision(task) if expected_revision is None else expected_revision},
            "changes": {task: changes},
        }

    def submit(self, report, ok=True, context=None):
        arguments = ["report", "--file", "-"]
        if context is not None:
            arguments.extend(["--context", context])
        return self.cli(*arguments, ok=ok, stdin=json.dumps(report))

    def test_commands_work_from_another_directory_and_persist_between_processes(self):
        self.setup_task()
        report = self.report([{"op": "define", "item_id": "implementation", "title": "Implementation", "value": "Reported complete", "status": "done", "evidence": []}])
        self.submit(report)
        saved = self.read()
        item = next(item for item in saved["items"] if item["item_id"] == "implementation")
        self.assertEqual(item["value"], "Reported complete")
        self.assertEqual(item["status"], "done")
        self.submit(report)
        self.assertEqual(self.read(), saved)
        self.cli("validate")
        self.assertFalse(list(self.root.rglob("*.sqlite*")))

    def test_two_sessions_report_conflict_reconcile_and_share_another_task(self):
        self.setup_task()
        evidence = [{"session_id": "session-a", "reference": "synthetic:implementation", "kind": "session_event"}]
        self.submit(self.report([{"op": "define", "item_id": "progress", "title": "Progress", "value": "Implementation complete; tests running", "status": "reported", "evidence": evidence}]))
        stale = self.report([{"op": "update", "item_id": "progress", "value": "All done"}])
        self.submit(self.report([
            {"op": "update", "item_id": "progress", "value": "Tests failed after interface change", "status": "blocked", "evidence": [{"session_id": "session-b", "reference": "synthetic:failing-test", "kind": "session_event"}]},
            {"op": "define", "item_id": "next-action", "title": "Next action", "value": "Repair interface and rerun tests", "status": "pending", "evidence": []},
        ], session="session-b"))
        winning = self.read()
        error = self.submit(stale, ok=False)["error"]
        self.assertEqual(error["code"], "revision_conflict")
        self.assertIn("session-b", json.dumps(error["details"]))
        self.assertNotIn("last_good", error["details"]["current"]["task-t"])
        self.assertEqual(error["details"]["current"]["task-t"]["state"], winning)
        self.assertEqual(self.read(), winning)
        self.setup_task("task-u", ("session-a",))
        items = self.cli("list", "--session", "session-a")["tasks"]
        self.assertEqual({item["task_id"] for item in items}, {"task-t", "task-u"})
        self.assertEqual(self.read("task-u")["items"], [])
        self.assertEqual(self.read(), winning)

    def test_registered_context_is_explicit_and_mismatches_are_rejected(self):
        context = self.work / "session.json"
        registered = self.cli("session-register", "--session", "session-a", "--out", context)
        self.assertFalse(registered["identity_verified"])
        self.assertEqual(context.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cli("whoami", "--context", context)["context"]["session_id"], "session-a")
        original = context.read_bytes()
        self.cli("session-register", "--session", "session-b", "--out", context, ok=False)
        self.assertEqual(context.read_bytes(), original)
        self.setup_task()
        changes = [{"op": "define", "item_id": "progress", "title": "Progress", "value": "Synthetic", "status": "reported", "evidence": []}]
        self.assertEqual(self.submit(self.report(changes, session="session-b"), context=context, ok=False)["error"]["code"], "session_mismatch")
        self.assertEqual(self.read()["items"], [])
        self.submit(self.report(changes), context=context)

    def test_work_copy_human_edit_preserves_notes_and_pin_enforces_boundary(self):
        self.setup_task()
        self.submit(self.report([{"op": "define", "item_id": "progress", "title": "Progress", "value": "Reported value", "status": "reported", "evidence": []}]))
        self.cli("note", "--task", "task-t", "--text", "Keep this human decision", "--expected-revision", self.revision())
        notes = self.read()["notes"]
        edit_path = self.work / "edit.json"
        self.cli("edit-export", "--task", "task-t", "--out", edit_path)
        edited = json.loads(edit_path.read_text())
        edited["items"][0]["value"] = "Human correction"
        edited["view"]["markdown"] = "# Persistent human context"
        edit_path.write_text(json.dumps(edited))
        self.cli("validate", "--file", edit_path, "--kind", "edit")
        self.cli("edit-apply", "--file", edit_path)
        self.assertEqual(self.read()["items"][0]["updated_by"]["kind"], "human")
        self.submit(self.report([{"op": "update", "item_id": "progress", "value": "Later valid session report"}]))
        self.assertEqual(self.read()["notes"], notes)
        self.assertEqual(self.read()["view"], edited["view"])
        self.assertIn("Human correction", json.dumps(self.cli("history", "--task", "task-t")["history"]))
        self.cli("pin", "--task", "task-t", "--item", "progress", "--pinned", "on", "--expected-revision", self.revision())
        self.assertEqual(self.submit(self.report([{"op": "update", "item_id": "progress", "value": "Must not overwrite"}]), ok=False)["error"]["code"], "pinned_item")
        self.assertEqual(self.cli("edit-apply", "--file", edit_path, ok=False)["error"]["code"], "revision_conflict")

    def test_report_stop_blocks_new_work_and_accepts_successful_retry(self):
        self.setup_task()
        report = self.report([{"op": "define", "item_id": "progress", "title": "Progress", "value": "Initial", "status": "reported", "evidence": []}])
        self.submit(report)
        self.cli("reporting", "--task", "task-t", "--session", "session-a", "--enabled", "off", "--expected-revision", self.revision())
        saved = self.read()
        self.assertTrue(self.submit(report)["replayed"])
        self.assertEqual(self.read(), saved)
        self.assertEqual(self.submit(self.report([{"op": "update", "item_id": "progress", "value": "Disabled report"}]), ok=False)["error"]["code"], "reporting_disabled")

    def test_schema_contracts_are_available_without_starting_daemon(self):
        for kind in ("task", "report", "edit", "context"):
            result = self.cli("schema", "--kind", kind)
            self.assertEqual(result["schema"]["properties"]["schema_version"]["const"], 1)
            self.assertFalse(result["schema"]["additionalProperties"])
        self.assertFalse(self.root.exists())

    def test_malformed_nonfinite_duplicate_and_oversized_inputs_fail_without_tracebacks(self):
        for raw in ("{", '{"schema_version":1,"schema_version":2}', '{"value":NaN}', "[" * 1500 + "]" * 1500, '"' + "x" * (2 * 1024 * 1024) + '"'):
            with self.subTest(length=len(raw)):
                result = self.cli("report", "--file", "-", stdin=raw, ok=False)
                self.assertEqual(result["error"]["code"], "validation_error")
        missing = self.cli("report", "--file", self.work / "missing.json", ok=False)
        self.assertEqual(missing["error"]["code"], "io_error")

    def test_observations_bridge_scopes_synthetic_facts_and_does_not_modify_databases_or_reports(self):
        self.setup_task(sessions=("session-a", "session-missing"))
        self.submit(self.report([{"op": "define", "item_id": "progress", "title": "Progress", "value": "Independent reported state", "status": "reported", "evidence": []}]))
        workspace = self.work / "personal"
        workspace.mkdir()
        source = self.work / "source" / "sessions.db"
        source.parent.mkdir()
        source.write_bytes(b"synthetic source sentinel: never open or modify")
        database = self.work / "observer.sqlite3"
        db = open_store(database, source)
        db.executemany("INSERT INTO sessions VALUES(?,?,?,?,?,?)", [
            ("session-a", "Synthetic A", str(workspace), 1, "current", 10),
            ("session-other", "Unlinked synthetic", str(workspace), 1, "current", 10),
            ("session-outside", "Other workspace", str(self.work / "outside"), 1, "current", 10),
        ])
        db.execute("INSERT INTO collector_status VALUES(1,10,11,NULL)")
        db.execute("INSERT INTO source_nodes VALUES(?,?,?,?,?,?,?)", ("session-a", 1, None, "m1", "assistant", "current-hash", 1))
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)", (1, "session-a", 1, "added", "m1", "assistant", "current-hash", 10))
        for suffix, digest in (("current", "current-hash"), ("old", "stale-hash")):
            db.execute("INSERT INTO discovered_resources VALUES(?,?,?,?,?,?,?,?,?,?,?)", (suffix, str(workspace), "session-a", 1, digest, "ticket", "LAB-100", "ticket:LAB-100", "mentioned", "synthetic:event", 10))
            db.execute("INSERT INTO plan_records VALUES(?,?,?,?,?,?,?)", ("session-a", 1, digest, "m1", "todo-" + suffix, suffix, json.dumps([{"content": "Synthetic plan", "source_status": "pending"}])))
        db.commit()
        db.close()
        configuration = self.work / "observer-config.json"
        configuration.write_text(json.dumps({"version": 1, "workspaces": [str(workspace)], "source": str(source), "store": str(database), "port": 8765}))
        before_source = source.read_bytes()
        before_database = database.read_bytes()
        before_state = self.read()
        result = self.cli("observations", "--observer-config", configuration, "--task", "task-t")
        self.assertEqual(result["origin"], "deterministic_collector")
        self.assertEqual(result["collector"]["last_success"], 10)
        self.assertEqual([entry["id"] for entry in result["sessions"]], ["session-a"])
        self.assertEqual(result["unobserved_session_ids"], ["session-missing"])
        entry = result["sessions"][0]
        self.assertEqual(entry["observation_count"], 1)
        self.assertEqual(len(entry["resource_mentions"]), 1)
        self.assertEqual(entry["resource_mentions"][0]["verification"], "mentioned")
        self.assertEqual(len(entry["plan_snapshots"]), 1)
        self.assertEqual(entry["plan_snapshots"][0]["verification"], "agent_reported")
        self.assertEqual(entry["plan_snapshots"][0]["ordering"], "not_inferred")
        self.assertEqual(source.read_bytes(), before_source)
        self.assertEqual(database.read_bytes(), before_database)
        self.assertEqual(self.read(), before_state)

    def test_missing_observer_store_reports_warning_without_creating_it(self):
        workspace = self.work / "personal"
        workspace.mkdir()
        database = self.work / "missing.sqlite3"
        configuration = self.work / "observer-config.json"
        configuration.write_text(json.dumps({"version": 1, "workspaces": [str(workspace)], "source": str(self.work / "source" / "sessions.db"), "store": str(database), "port": 8765}))
        result = self.cli("observations", "--observer-config", configuration)
        self.assertEqual(result["sessions"], [])
        self.assertTrue(result["warnings"])
        self.assertFalse(database.exists())

    def test_missing_task_commands_return_structured_errors_without_tracebacks(self):
        for arguments in (
            ("read", "--task", "missing"),
            ("edit-export", "--task", "missing", "--out", self.work / "edit.json"),
            ("observations", "--task", "missing", "--observer-config", self.work / "missing-config.json"),
        ):
            with self.subTest(command=arguments[0]):
                self.assertEqual(self.cli(*arguments, ok=False)["error"]["code"], "task_not_found")

    def test_missing_state_can_be_explicitly_recovered_with_digest_sentinel(self):
        self.setup_task()
        saved = self.read()
        path = self.root / "tasks" / "task-t" / "state.json"
        path.unlink()
        diagnostic = self.cli("read", "--task", "task-t")
        self.assertEqual(diagnostic["health"], "external_edit")
        self.assertIsNone(diagnostic["content_digest"])
        self.assertEqual(diagnostic["last_good"], saved)
        recovered = self.cli("recover", "--task", "task-t", "--expected-digest", "missing")
        self.assertTrue(recovered["restored"])
        self.assertIsNone(recovered["backup"])
        self.assertEqual(self.read(), saved)
        metadata = list((self.root / "recovery" / "task-t").glob("*.json"))
        self.assertEqual(len(metadata), 1)
        self.assertTrue(json.loads(metadata[0].read_text())["raw_missing"])

    def test_recovery_missing_sentinel_rejects_reappearing_raw_file(self):
        self.setup_task()
        path = self.root / "tasks" / "task-t" / "state.json"
        path.unlink()
        diagnostic = self.cli("read", "--task", "task-t")
        self.assertIsNone(diagnostic["content_digest"])
        new_raw = b'{"unfinished":"external edit"}'
        path.write_bytes(new_raw)
        result = self.cli("recover", "--task", "task-t", "--expected-digest", "missing", ok=False)
        self.assertEqual(result["error"]["code"], "revision_conflict")
        self.assertEqual(path.read_bytes(), new_raw)

    def test_legacy_config_prefix_does_not_load_config_or_run_autostart_for_state(self):
        from observer.app import main
        with patch("observer.app.load_config") as load, patch("observer.app.subprocess.run") as command:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as caught:
                    main(["--config", str(self.work / "missing.json"), "state"])
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("command", errors.getvalue())
            load.assert_not_called()
            command.assert_not_called()
        result = subprocess.run(
            [sys.executable, str(REPOSITORY / "observerctl"), "--config", str(self.work / "missing.json"), "state"],
            cwd=self.work, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("command", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.work / "missing.json").exists())

    def test_mutation_receipts_and_listing_do_not_echo_full_view_or_item_values(self):
        self.setup_task()
        body = "synthetic-large-value-" * 400
        report = self.report([{"op": "define", "item_id": "progress", "title": "Progress", "value": body, "status": "reported", "evidence": []}])
        receipt = self.submit(report)
        self.assertEqual(receipt["snapshot"], "committed")
        self.assertEqual(set(receipt["tasks"]["task-t"]), {"revision"})
        self.assertNotIn(body, json.dumps(receipt))
        self.assertLess(len(json.dumps(receipt)), 1000)
        latest = self.cli("read", "--task", "task-t")
        self.assertNotIn("last_good", latest)
        self.assertEqual(latest["state"]["items"][0]["value"], body)
        listing = self.cli("list", "--session", "session-a")
        self.assertNotIn(body, json.dumps(listing))
        self.assertNotIn("state", listing["tasks"][0])
        self.assertEqual(listing["tasks"][0]["item_count"], 1)
        self.cli("note", "--task", "task-t", "--text", "Later revision", "--expected-revision", self.revision())
        replay = self.submit(report)
        self.assertEqual(replay["snapshot"], "original_commit")
        self.assertLess(replay["tasks"]["task-t"]["revision"], self.revision())


if __name__ == "__main__":
    unittest.main()
