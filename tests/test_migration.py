"""Synthetic legacy preservation and reversible cutover; no live session reads."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from observer.cooperative import CooperativeState
from observer.file_store import StateError
from observer.migration import (apply_migration, check_migration, plan_migration,
                                rollback_migration)
from observer.registry import create_task, link_session, set_link_active, add_resource
from observer.store import open_store
from observer.views import ensure_view, add_item, add_section, get_view


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'legacy.sqlite'
        self.target = self.root / 'cooperative'
        self.db = open_store(self.source, self.root / 'devin' / 'source.sqlite')
        self.db.execute("INSERT INTO sessions VALUES ('session-a','Synthetic A','/synthetic',1,'current',1)")
        self.db.execute("INSERT INTO sessions VALUES ('session-b','Synthetic B','/synthetic',1,'current',1)")
        self.db.commit()
        self.task = create_task(self.db, 'Migration fixture', 'Human task description')
        self.other = create_task(self.db, 'Second task', 'Shared session, independent cursor')
        link_session(self.db, self.task, 'session-a', role='implement', scope='core')
        archived = link_session(self.db, self.task, 'session-b')
        set_link_active(self.db, self.task, archived, 1, False)
        link_session(self.db, self.other, 'session-a')
        self.view_id = ensure_view(self.db, self.task)
        self.section = self.db.execute('SELECT id FROM view_sections WHERE view_id=?', (self.view_id,)).fetchone()[0]
        self.note, revision = add_item(self.db, self.view_id, self.section, 1, 'Human decision stays', True)
        second, revision = add_section(self.db, self.view_id, revision, 'Custom questions')
        self.question, revision = add_item(self.db, self.view_id, second, revision, 'Which interface is current?')
        self.resource = add_resource(self.db, self.task, 'pr', 'https://github.com/example/synthetic/pull/42')
        self.db.execute('INSERT INTO task_session_progress VALUES (?,?,7,2)', (self.task, 'session-a'))
        self.db.execute('INSERT INTO task_session_progress VALUES (?,?,2,1)', (self.other, 'session-a'))
        self.db.execute('INSERT INTO task_progress_history(task_id,session_id,last_observation_id,revision,acknowledged_at) VALUES (?,?,7,2,10)', (self.task, 'session-a'))
        self.db.execute('INSERT INTO tracking_state VALUES (?,3,11,NULL)', (self.task,))
        self.db.execute('INSERT INTO tracking_cursors VALUES (?,?,7)', (self.task, 'session-a'))
        evidence = json.dumps([{'id': 'obs:7', 'session_id': 'session-a', 'node_id': 1, 'content_hash': 'a' * 64}])
        self.db.execute('INSERT INTO tracking_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('old-tracking', self.task, 'interface', 'blocker', 'Agent reported interface failure', 'open', 'reported', 'machine', 1, 3, evidence, '[]', 10))
        self.db.execute('INSERT INTO tracking_history(item_id,run_id,snapshot,saved_at) VALUES (?,?,?,?)',
                        ('old-tracking', None, '{"content":"Prior semantic state"}', 9))
        self.db.commit()
        self.original_view = get_view(self.db, self.task)
        self.db.close()
        self.original_digest = digest(self.source)

    def connect(self, path):
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        return db

    def apply(self, **kwargs):
        return apply_migration(self.source, self.target, all_tasks=True, **kwargs)

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(StateError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_actual_cli_plan_apply_check_rollback_roundtrip(self):
        cli = Path(__file__).resolve().parents[1] / 'observerctl'
        def run(*args):
            result = subprocess.run([sys.executable, str(cli), 'migrate', *map(str, args)],
                                    capture_output=True, text=True, timeout=15)
            value = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0, value)
            self.assertTrue(value['ok'], value)
            return value
        plan = run('plan', '--source', self.source, '--all')
        run('apply', '--source', self.source, '--target', self.target, '--all',
            '--expected-fingerprint', plan['source_fingerprint'])
        self.assertTrue(run('check', '--target', self.target)['rollback_eligible'])
        restored = self.root / 'cli-restored.sqlite'
        self.assertTrue(run('rollback', '--target', self.target, '--restore-to', restored)['retired'])
        with self.connect(restored) as db:
            self.assertEqual(get_view(db, self.task), self.original_view)
        self.assertEqual(digest(self.source), self.original_digest)

    def test_plan_requires_explicit_selection_and_validates_without_target(self):
        self.assert_error('migration_selection', plan_migration, self.source)
        result = plan_migration(self.source, task_ids=[self.task])
        self.assertEqual(len(result['tasks']), 1)
        self.assertFalse(self.target.exists())
        self.assertEqual(digest(self.source), self.original_digest)

    def test_preserves_structured_view_human_notes_pins_resources_and_history(self):
        result = self.apply()
        state = CooperativeState(self.target).read(self.task)['state']
        self.assertEqual(state['task_id'], self.task)
        self.assertEqual(state['legacy']['description'], 'Human task description')
        self.assertEqual(state['legacy']['view'], self.original_view)
        self.assertEqual(state['legacy']['resources'][0]['id'], self.resource)
        self.assertEqual(state['legacy']['review_cursors'][0]['last_observation_id'], 7)
        self.assertEqual(state['notes'][0]['text'], 'Human decision stays')
        self.assertTrue(next(i for i in state['items'] if i['item_id'] == self.note)['pinned'])
        old = next(i for i in state['items'] if i['item_id'] == 'old-tracking')
        self.assertTrue(old['pinned'])
        self.assertEqual(old['verification'], 'legacy_import')
        self.assertEqual(old['updated_by'], {'kind': 'legacy', 'session_id': None})
        self.assertIn('sha256:', old['evidence'][0]['reference'])
        archive = json.loads((self.target / state['legacy']['archive_ref']).read_text())
        self.assertEqual(archive['tables']['tracking_history'][0]['snapshot'], '{"content":"Prior semantic state"}')
        self.assertEqual(archive['tables']['task_progress_history'][0]['revision'], 2)
        self.assertEqual(digest(self.source), self.original_digest)
        self.assertEqual(check_migration(self.target)['errors'], [])
        self.assertTrue(check_migration(self.target)['rollback_eligible'])
        self.assertFalse(result['replayed'])

    def test_many_to_many_active_links_cursors_independent_and_reporting_disabled(self):
        self.apply(); core = CooperativeState(self.target)
        a, b = (core.read(t)['state'] for t in (self.task, self.other))
        self.assertEqual([c['session_id'] for c in a['contributions']], ['session-a'])
        self.assertFalse(a['contributions'][0]['reporting'])
        self.assertEqual(a['legacy']['archived_contributions'][0]['session_id'], 'session-b')
        self.assertEqual(len(core.list('session-a')), 2)
        self.assertNotEqual(a['legacy']['review_cursors'], b['legacy']['review_cursors'])

    def test_imported_pin_protection_and_nonpinned_report_preserve_notes_archive(self):
        self.apply(); core = CooperativeState(self.target)
        core.reporting(self.task, 'session-a', True, 1)
        request = {'schema_version': 1, 'report_id': 'import-report', 'session_id': 'session-a',
                   'expected_revisions': {self.task: 2}, 'changes': {self.task: [
                       {'op': 'update', 'item_id': self.note, 'value': 'Replace pinned human text'}]}}
        self.assert_error('pinned_item', core.report, request)
        request['changes'][self.task][0]['item_id'] = self.question
        core.report(request)
        row = core.read(self.task)['state']
        self.assertEqual(row['notes'][0]['text'], 'Human decision stays')
        self.assertEqual(row['legacy']['view'], self.original_view)
        self.assertEqual(next(i for i in row['items'] if i['item_id'] == self.question)['updated_by']['session_id'], 'session-a')

    def test_replay_does_not_overwrite_new_reports(self):
        plan = plan_migration(self.source, all_tasks=True)
        self.apply(expected_fingerprint=plan['source_fingerprint'])
        core = CooperativeState(self.target); core.note(self.task, 'New note', 1)
        replay = self.apply(expected_fingerprint=plan['source_fingerprint'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(core.read(self.task)['state']['revision'], 2)
        self.assertFalse(replay['rollback_eligible'])

    def test_same_command_without_fingerprint_replays_but_source_changes_do_not(self):
        self.apply()
        self.assertTrue(self.apply()['replayed'])
        with self.connect(self.source) as db:
            db.execute('UPDATE tasks SET description=? WHERE id=?', ('New source intent', self.task))
        self.assert_error('source_changed', self.apply)
        self.assertEqual(CooperativeState(self.target).read(self.task)['state']['legacy']['description'],
                         'Human task description')

    def test_source_change_refuses_reviewed_plan_without_partial_import(self):
        plan = plan_migration(self.source, all_tasks=True)
        with self.connect(self.source) as db:
            db.execute('UPDATE tasks SET description=? WHERE id=?', ('Changed after review', self.task))
        self.assert_error('source_changed', self.apply, expected_fingerprint=plan['source_fingerprint'])
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob('.cooperative-migration-*')), [])

    def test_existing_target_never_overwritten(self):
        core = CooperativeState(self.target); core.create('keep', 'Existing state')
        self.assert_error('destination_exists', self.apply)
        self.assertEqual(core.read('keep')['state']['title'], 'Existing state')

    def test_rollback_restores_new_db_and_blocks_old_core_after_retirement(self):
        self.apply(); core = CooperativeState(self.target)
        restored = self.root / 'restored.sqlite'
        result = rollback_migration(self.target, restored)
        self.assertTrue(result['retired'])
        self.assertEqual(digest(self.source), self.original_digest)
        with self.connect(restored) as db:
            self.assertEqual(get_view(db, self.task), self.original_view)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assert_error('retired_store', core.note, self.task, 'late write', 1)
        self.assertTrue(rollback_migration(self.target, restored)['replayed'])
        self.assertTrue(check_migration(self.target)['retired'])

    def test_rollback_refuses_intervening_notes_or_raw_edits_and_existing_restore(self):
        self.apply(); core = CooperativeState(self.target)
        old = self.root / 'exists.sqlite'; old.write_bytes(b'keep')
        self.assert_error('destination_exists', rollback_migration, self.target, old)
        self.assertEqual(old.read_bytes(), b'keep')
        core.note(self.task, 'Intervening human change', 1)
        self.assert_error('rollback_conflict', rollback_migration, self.target, self.root / 'restored.sqlite')
        self.assertFalse((self.target / '.retired.json').exists())

    def test_archive_tamper_is_visible_and_prevents_rollback(self):
        self.apply()
        state = CooperativeState(self.target).read(self.task)['state']
        archive = self.target / state['legacy']['archive_ref']; archive.write_text('{}')
        check = check_migration(self.target)
        self.assertEqual(check['errors'][0]['code'], 'archive_changed')
        self.assertFalse(check['rollback_eligible'])
        self.assert_error('rollback_conflict', rollback_migration, self.target, self.root / 'restored.sqlite')

    def test_limit_refuses_all_tasks_without_partial_success(self):
        with self.connect(self.source) as db:
            for i in range(31): create_task(db, f'Task {i}')
        self.assert_error('migration_limit', self.apply)
        self.assertFalse(self.target.exists())

    def test_unrepresentable_text_refuses_without_truncation(self):
        with self.connect(self.source) as db:
            db.execute('UPDATE view_items SET content=? WHERE id=?', ('x' * 17000, self.note))
        self.assert_error('migration_incompatible', self.apply)
        self.assertFalse(self.target.exists())

    def test_unknown_schema_and_devin_source_are_not_guessed(self):
        with self.connect(self.source) as db:
            db.execute('CREATE TABLE future_feature (task_id TEXT)')
        self.assert_error('unsupported_schema', self.apply)
        wrong = self.root / 'not-observer.sqlite'
        with self.connect(wrong) as db: db.execute('CREATE TABLE source (content TEXT)')
        self.assert_error('incompatible_source', plan_migration, wrong, all_tasks=True)

    def test_migration_and_restore_refuse_devin_storage_even_via_alias(self):
        home = self.root / 'fake-home'
        devin = home / '.local' / 'share' / 'devin'
        devin.mkdir(parents=True)
        alias = self.root / 'devin-alias'; alias.symlink_to(devin, target_is_directory=True)
        with patch('observer.migration.Path.home', return_value=home):
            self.assert_error('unsafe_path', apply_migration, self.source, devin / 'state', all_tasks=True)
            self.assert_error('unsafe_path', apply_migration, self.source, alias / 'state', all_tasks=True)
            self.apply()
            self.assert_error('unsafe_path', rollback_migration, self.target, alias / 'restore.sqlite')
        self.assertEqual(list(devin.iterdir()), [])
        self.assertFalse((self.target / '.retired.json').exists())

    def test_manifest_tamper_and_unsafe_source_are_rejected(self):
        self.apply()
        manifest = self.target / 'migration' / 'manifest.json'
        value = json.loads(manifest.read_text()); value['task_ids'] = [self.task]
        manifest.write_text(json.dumps(value))
        self.assert_error('invalid_manifest', check_migration, self.target)
        alias = self.root / 'source-alias.sqlite'; alias.symlink_to(self.source)
        self.assert_error('unsafe_path', plan_migration, alias, all_tasks=True)

    def test_uncommitted_legacy_writes_are_not_imported(self):
        db = sqlite3.connect(self.source)
        self.addCleanup(db.close)
        db.execute('UPDATE tasks SET description=? WHERE id=?', ('Uncommitted', self.task))
        self.apply()
        self.assertEqual(CooperativeState(self.target).read(self.task)['state']['legacy']['description'],
                         'Human task description')
        db.rollback()

    def test_wal_snapshot_includes_committed_rows_without_changing_source(self):
        db = sqlite3.connect(self.source)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('UPDATE tasks SET description=? WHERE id=?', ('WAL update', self.task)); db.commit()
        before = digest(self.source); wal = digest(str(self.source) + '-wal')
        self.apply()
        self.assertEqual(digest(self.source), before)
        self.assertEqual(digest(str(self.source) + '-wal'), wal)
        self.assertEqual(CooperativeState(self.target).read(self.task)['state']['legacy']['description'], 'WAL update')
        db.close()

    def test_publish_failure_leaves_source_and_no_partial_target(self):
        with patch('observer.migration.os.rename', side_effect=OSError('synthetic interruption')):
            self.assert_error('migration_io', self.apply)
        self.assertFalse(self.target.exists())
        self.assertEqual(digest(self.source), self.original_digest)

    def test_interrupted_rollback_can_resume_and_never_resume_writes(self):
        self.apply(); restored = self.root / 'restored.sqlite'
        with patch('observer.migration.os.link', side_effect=OSError('synthetic interruption')):
            with self.assertRaises(OSError): rollback_migration(self.target, restored)
        self.assert_error('retired_store', CooperativeState(self.target).list)
        self.assertFalse(restored.exists())
        result = rollback_migration(self.target, restored)
        self.assertTrue(result['retired'])
        self.assertTrue(restored.is_file())
