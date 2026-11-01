"""Domain acceptance: sessions report; the core validates without interpreting."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from observer.cooperative import CooperativeState
from observer.file_store import StateError
from observer.state_schema import MAX_BYTES, published, validate


def define(item="progress", value="Implementation reported complete", evidence=None):
    return {"op": "define", "item_id": item, "title": item.title(), "value": value,
            "status": "reported", "evidence": evidence or []}


class CooperativeStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "state"
        self.core = CooperativeState(self.root)
        self.counter = 0
        self.create_task("task-t", ("session-a", "session-b"))

    def tearDown(self):
        self.temporary.cleanup()

    def state(self, task="task-t"):
        row = self.core.read(task)
        self.assertEqual(row["health"], "ok", row)
        return row["state"]

    def revision(self, task="task-t"):
        return self.state(task)["revision"]

    def item(self, item="progress", task="task-t"):
        return next(i for i in self.state(task)["items"] if i["item_id"] == item)

    def create_task(self, task, sessions):
        self.core.create(task, "Synthetic task", sessions[0])
        for session in sessions[1:]:
            self.core.link(task, session, self.revision(task))
        for session in sessions:
            self.core.reporting(task, session, True, self.revision(task))

    def request(self, changes, session="session-a", task="task-t", report_id=None, revision=None):
        self.counter += 1
        return {"schema_version": 1, "report_id": report_id or f"report-{self.counter}",
                "session_id": session,
                "expected_revisions": {task: self.revision(task) if revision is None else revision},
                "changes": {task: changes}}

    def assert_rejected(self, request, code=None):
        before = self.state()
        history = self.core.store.history("task-t")
        with self.assertRaises(StateError) as caught:
            self.core.report(request)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.core.store.history("task-t"), history)
        return caught.exception

    def test_two_sessions_report_and_reconcile_with_source_attribution(self):
        a_evidence = {"session_id": "session-a", "reference": "synthetic:event-a", "kind": "session_event"}
        b_evidence = {"session_id": "session-b", "reference": "synthetic:event-b", "kind": "session_event"}
        self.core.note("task-t", "Human decision: preserve the public API", self.revision())
        notes = self.state()["notes"]
        self.core.report(self.request([define(evidence=[a_evidence])]))
        created = self.item()
        self.assertEqual(created["created_by"], {"kind": "session", "session_id": "session-a"})
        self.assertEqual(created["verification"], "reported")
        self.core.report(self.request([
            {"op": "update", "item_id": "progress", "value": "Implementation exists; interface change breaks tests", "status": "blocked", "evidence": [a_evidence, b_evidence]},
            define("next-action", "Repair interface and rerun tests", [b_evidence]),
        ], session="session-b"))
        current = self.item()
        self.assertEqual(current["created_by"], created["created_by"])
        self.assertEqual(current["updated_by"], {"kind": "session", "session_id": "session-b"})
        self.assertEqual(current["item_revision"], created["item_revision"] + 1)
        self.assertEqual(current["commit_sequence"], self.revision())
        self.assertEqual(current["evidence"], [a_evidence, b_evidence])
        self.assertEqual(self.state()["notes"], notes)
        history = json.dumps(self.core.store.history("task-t"))
        self.assertIn("Implementation reported complete", history)
        self.assertIn("Human decision", history)

    def test_new_session_report_replaces_legacy_verification_only_for_updated_item(self):
        self.core.report(self.request([define(), define('other')]))
        value=self.state()
        for item in value['items']:
            item['verification']='legacy_import'
            item['updated_by']={'kind':'legacy','session_id':None}
        expected = value['revision']
        value['revision'] += 1
        self.core.store.transact('synthetic-legacy', {'fixture':True}, {'task-t':expected}, lambda _: {'task-t':value})
        self.core.report(self.request([{'op':'update','item_id':'progress','value':'Fresh result'}]))
        self.assertEqual(self.item()['verification'],'reported')
        self.assertEqual(self.item('other')['verification'],'legacy_import')

    def test_stale_report_returns_latest_session_and_preserves_winning_state(self):
        self.core.report(self.request([define()]))
        stale = self.request([{"op": "update", "item_id": "progress", "value": "Stale A claim"}])
        self.core.report(self.request([{"op": "update", "item_id": "progress", "value": "B found a blocker"}], session="session-b"))
        conflict = self.assert_rejected(stale, "revision_conflict")
        details = json.dumps(conflict.details)
        self.assertIn("session-b", details)
        self.assertIn("B found a blocker", details)
        stale["expected_revisions"]["task-t"] = self.revision()
        stale["changes"]["task-t"][0]["value"] = "A acknowledges B; repair next"
        self.core.report(stale)
        self.assertEqual(self.item()["updated_by"]["session_id"], "session-a")

    def test_repeated_report_is_idempotent_after_newer_state_and_reporting_stop(self):
        request = self.request([define()])
        self.core.report(request)
        self.core.report(self.request([{"op": "update", "item_id": "progress", "value": "New B state"}], session="session-b"))
        self.core.reporting("task-t", "session-a", False, self.revision())
        current = self.state()
        history = self.core.store.history("task-t")
        self.assertTrue(self.core.report(request)["replayed"])
        self.assertEqual(self.state(), current)
        self.assertEqual(self.core.store.history("task-t"), history)
        changed = copy.deepcopy(request)
        changed["changes"]["task-t"][0]["value"] = "Different payload with reused ID"
        self.assert_rejected(changed)

    def test_unlinked_disabled_session_and_unlinked_evidence_rejected(self):
        self.assert_rejected(self.request([define()], session="unknown-session"), "session_not_linked")
        self.core.reporting("task-t", "session-a", False, self.revision())
        self.assert_rejected(self.request([define()]), "reporting_disabled")
        self.core.reporting("task-t", "session-a", True, self.revision())
        evidence = [{"session_id": "unknown-session", "reference": "synthetic:event", "kind": "session_event"}]
        self.assert_rejected(self.request([define(evidence=evidence)]), "validation_error")

    def test_pinned_values_reject_changes_archive_and_redefinition(self):
        self.core.report(self.request([define()]))
        self.core.pin("task-t", "progress", True, self.revision())
        for change in (
            {"op": "update", "item_id": "progress", "value": "overwrite"},
            {"op": "update", "item_id": "progress", "status": "done"},
            {"op": "archive", "item_id": "progress"},
        ):
            self.assert_rejected(self.request([change]), "pinned_item")
        self.assert_rejected(self.request([define()]), "item_exists")
        self.assert_rejected(self.request([{"op": "update", "item_id": "progress", "pinned": False}]), "validation_error")
        self.core.pin("task-t", "progress", False, self.revision())
        self.core.report(self.request([{"op": "update", "item_id": "progress", "value": "Explicit unpin permits new report"}]))
        self.assertFalse(self.item()["pinned"])

    def test_human_unpinned_edit_can_be_updated_and_notes_history_survive(self):
        self.core.report(self.request([define()]))
        self.core.note("task-t", "Human note retained", self.revision())
        notes = self.state()["notes"]
        edit = self.core.export_edit("task-t")
        edit["items"][0]["value"] = "Human corrected value"
        edit["view"]["markdown"] = "# Human persistent view\nKeep this text."
        self.core.apply_edit(edit)
        self.assertEqual(self.item()["updated_by"], {"kind": "human", "session_id": None})
        self.assertFalse(self.item()["pinned"])
        self.core.report(self.request([{"op": "update", "item_id": "progress", "value": "Later session value"}], session="session-b"))
        self.assertEqual(self.item()["updated_by"]["session_id"], "session-b")
        self.assertEqual(self.state()["notes"], notes)
        self.assertEqual(self.state()["view"], edit["view"])
        self.assertIn("Human corrected value", json.dumps(self.core.store.history("task-t")))

    def test_safe_edit_stale_and_item_deletion_are_rejected_without_changes(self):
        self.core.report(self.request([define()]))
        stale = self.core.export_edit("task-t")
        self.core.report(self.request([{"op": "update", "item_id": "progress", "value": "Other session updated"}], session="session-b"))
        current = self.state()
        with self.assertRaises(StateError) as caught:
            self.core.apply_edit(stale)
        self.assertEqual(caught.exception.code, "revision_conflict")
        edit = self.core.export_edit("task-t")
        edit["items"] = []
        with self.assertRaises(StateError):
            self.core.apply_edit(edit)
        self.assertEqual(self.state(), current)

    def test_archiving_retains_item_id_and_history(self):
        self.core.report(self.request([define()]))
        self.core.report(self.request([{"op": "archive", "item_id": "progress"}]))
        self.assertTrue(self.item()["archived"])
        self.assert_rejected(self.request([{"op": "update", "item_id": "progress", "value": "Silent revival"}]), "archived_item")
        self.assert_rejected(self.request([define()]), "item_exists")
        self.assertIn("Implementation reported complete", json.dumps(self.core.store.history("task-t")))

    def test_shared_session_multi_task_report_and_independent_revisions(self):
        self.create_task("task-u", ("session-a",))
        request = self.request([define()])
        request["expected_revisions"]["task-u"] = self.revision("task-u")
        request["changes"]["task-u"] = [define("research", "Analysis hypothesis")]
        self.core.report(request)
        self.assertEqual({x["task_id"] for x in self.core.list("session-a")}, {"task-t", "task-u"})
        self.assertEqual({x["task_id"] for x in self.core.list("session-b")}, {"task-t"})
        other = self.state("task-u")
        self.core.report(self.request([{"op": "update", "item_id": "progress", "value": "Changed T"}]))
        self.assertEqual(self.state("task-u"), other)

    def test_multi_task_validation_failure_and_read_dependency_conflict_write_nothing(self):
        self.create_task("task-u", ("session-a",))
        request = self.request([define()])
        request["expected_revisions"]["task-u"] = self.revision("task-u")
        request["changes"]["task-u"] = [{"op": "update", "item_id": "missing", "value": "Invalid"}]
        other = self.state("task-u")
        self.assert_rejected(request, "item_not_found")
        self.assertEqual(self.state("task-u"), other)
        request["changes"].pop("task-u")
        self.core.note("task-u", "Changed dependency", self.revision("task-u"))
        self.assert_rejected(request, "revision_conflict")
        request["expected_revisions"]["task-u"] = self.revision("task-u")
        self.core.report(request)
        self.assertEqual(self.revision("task-u"), other["revision"] + 1)

    def test_invalid_report_schema_is_not_written(self):
        base = self.request([define()])
        bad_requests = []
        for key, value in (("schema_version", 2), ("schema_version", True), ("unknown", 1), ("session_id", "")):
            bad = copy.deepcopy(base)
            bad[key] = value
            bad_requests.append(bad)
        for revision in (True, 0, "1"):
            bad = copy.deepcopy(base)
            bad["expected_revisions"]["task-t"] = revision
            bad_requests.append(bad)
        bad = copy.deepcopy(base)
        bad["changes"]["task-t"][0]["notes"] = []
        bad_requests.append(bad)
        bad = copy.deepcopy(base)
        bad["changes"]["task-t"][0]["item_id"] = "../escape"
        bad_requests.append(bad)
        for bad in bad_requests:
            with self.subTest(request=bad):
                self.assert_rejected(bad, "validation_error")
        self.assert_rejected(self.request([define(), define()]), "validation_error")
        self.assert_rejected(self.request([]), "validation_error")
        self.assert_rejected(self.request([{"op": "update", "item_id": "progress"}]))

    def test_schema_size_nonfinite_and_future_state_version_rejected(self):
        request = self.request([define(value="x" * MAX_BYTES)])
        self.assert_rejected(request, "validation_error")
        self.assert_rejected(self.request([define(value=float("nan"))]), "validation_error")
        task = self.state()
        task["schema_version"] = 99
        with self.assertRaises(ValueError):
            validate(task, "task")
        self.assertFalse(published("report")["additionalProperties"])

    def test_report_ids_match_published_safe_80_character_contract(self):
        for report_id in ("", "../escape", "space id", "contains.dot", "a" * 81, "한글", "a\n"):
            with self.subTest(report_id=report_id):
                request = self.request([define()])
                request["report_id"] = report_id
                self.assert_rejected(request, "validation_error")
        request = self.request([define()], report_id="a" * 80)
        self.core.report(request)
        self.assertEqual(self.item()["report_id"], "a" * 80)

    def test_persisted_schemas_match_cli_contract_definitions(self):
        directory = Path(__file__).resolve().parents[1] / "schemas" / "cooperative-v1"
        for kind in ("task", "report", "edit", "context"):
            with self.subTest(kind=kind):
                self.assertEqual(json.loads((directory / (kind + ".schema.json")).read_text()), published(kind))

    def test_all_documents_reject_future_versions_unknown_fields_and_invalid_relations(self):
        self.core.report(self.request([define()]))
        documents = {
            "task": self.state(), "report": self.request([define("new")]),
            "edit": self.core.export_edit("task-t"),
            "context": {"schema_version": 1, "session_id": "session-a", "identity_source": "explicit_registration"},
        }
        for kind, original in documents.items():
            for key, value in (("schema_version", 2), ("schema_version", True), ("unknown", "not allowed")):
                bad = copy.deepcopy(original)
                bad[key] = value
                with self.subTest(kind=kind, key=key, value=value), self.assertRaises(ValueError):
                    validate(bad, kind)
        for mutate in (
            lambda task: task["items"].append(copy.deepcopy(task["items"][0])),
            lambda task: task["contributions"].append(copy.deepcopy(task["contributions"][0])),
            lambda task: task["items"][0].update(commit_sequence=task["revision"] + 1),
            lambda task: task["items"][0].update(updated_by={"kind": "human", "session_id": "session-a"}),
            lambda task: task["items"][0].update(created_by={"kind": "session", "session_id": "unlinked"}),
        ):
            bad = copy.deepcopy(documents["task"])
            mutate(bad)
            with self.assertRaises(ValueError):
                validate(bad, "task")

    def test_task_id_mismatch_is_visible_and_isolated_without_losing_original_bytes(self):
        self.create_task("task-u", ("session-a",))
        before = self.state()
        other = self.state("task-u")
        path = self.root / "tasks" / "task-t" / "state.json"
        changed = copy.deepcopy(before)
        changed["task_id"] = "task-u"
        raw = json.dumps(changed).encode()
        path.write_bytes(raw)
        row = self.core.read("task-t")
        self.assertEqual(row["health"], "invalid")
        self.assertIsNone(row["state"])
        self.assertEqual(row["last_good"], before)
        self.assertEqual(self.state("task-u"), other)
        with self.assertRaises(StateError):
            self.core.report(self.request([define()], revision=before["revision"]))
        self.assertEqual(path.read_bytes(), raw)

    def test_standalone_report_validation_checks_operation_shapes_and_dependencies(self):
        malformed = [
            [{"op": "define", "item_id": "new"}],
            [{"op": "update", "item_id": "progress"}],
            [{"op": "archive", "item_id": "progress", "value": "not accepted"}],
            [define(), define()], [],
        ]
        for changes in malformed:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(self.request(changes), "report")
        undeclared = self.request([define()])
        undeclared["changes"]["task-not-read"] = [define("other")]
        with self.assertRaises(ValueError):
            validate(undeclared, "report")


if __name__ == "__main__":
    unittest.main()
