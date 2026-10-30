"""Optional S2 presentation metadata keeps the cooperative transaction contract."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from observer.cooperative import CooperativeState
from observer.file_store import StateError
from observer.state_schema import validate
from observer.ui_projection import project


class ItemRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'state'
        self.core = CooperativeState(self.root)
        self.core.create('task-a', 'Synthetic roles', 'session-a')
        self.core.reporting('task-a', 'session-a', True, self.state()['revision'])
        self.core.link('task-a', 'session-b', self.state()['revision'])
        self.core.reporting('task-a', 'session-b', True, self.state()['revision'])
        self.serial = 0

    def state(self):
        return self.core.read('task-a')['state']

    def item(self, item_id='item-a'):
        return next(i for i in self.state()['items'] if i['item_id'] == item_id)

    def request(self, changes, session='session-a'):
        self.serial += 1
        return {'schema_version': 1, 'report_id': f'role-report-{self.serial}',
                'session_id': session, 'expected_revisions': {'task-a': self.state()['revision']},
                'changes': {'task-a': changes}}

    @staticmethod
    def define(item_id='item-a', **fields):
        return {'op': 'define', 'item_id': item_id, 'title': 'Decision needed',
                'value': 'Synthetic interface decision', 'status': 'blocked', 'evidence': [], **fields}

    def assert_rejected_unchanged(self, request, code):
        before = self.state()
        history = self.core.store.history('task-a')
        with self.assertRaises(StateError) as caught:
            self.core.report(request)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.core.store.history('task-a'), history)

    def test_old_v1_define_stays_valid_without_inferred_metadata_or_read_writes(self):
        self.core.report(self.request([self.define()]))
        original = self.state()
        self.assertNotIn('role', self.item())
        self.assertNotIn('attention_required', self.item())
        validate(original, 'task')
        paths = [p for p in self.root.rglob('*') if p.is_file()]
        before = {str(p): p.read_bytes() for p in paths}
        view = project(self.core.list())
        self.assertTrue(view['capabilities']['typed_item_roles'])
        self.assertNotIn('role', view['items']['task-a']['item-a'])
        self.assertNotIn('attention_required', view['items']['task-a']['item-a'])
        self.assertEqual({str(p): p.read_bytes() for p in paths}, before)
        self.assertEqual(self.state(), original)

    def test_define_update_and_idempotent_replay_preserve_source_and_notes(self):
        self.core.note('task-a', 'Keep the human decision.', self.state()['revision'])
        notes = self.state()['notes']
        first = self.request([self.define(role='intervention', attention_required=True)])
        self.core.report(first)
        created = self.item()
        update = self.request([{'op': 'update', 'item_id': 'item-a',
                                'attention_required': False}], session='session-b')
        self.core.report(update)
        item = self.item()
        self.assertEqual(item['role'], 'intervention')
        self.assertFalse(item['attention_required'])
        self.assertEqual(item['created_by'], created['created_by'])
        self.assertEqual(item['updated_by']['session_id'], 'session-b')
        self.assertEqual(item['item_revision'], created['item_revision'] + 1)
        self.assertEqual(item['commit_sequence'], self.state()['revision'])
        self.assertEqual(item['updated_at'], self.state()['updated_at'])
        self.assertEqual(self.state()['notes'], notes)
        current = self.state()
        self.assertTrue(self.core.report(first)['replayed'])
        self.assertEqual(self.state(), current)
        self.assertTrue(self.core.store.history('task-a')[-2]['items'][0]['attention_required'])

    def test_status_change_does_not_clear_explicit_attention(self):
        self.core.report(self.request([self.define(role='intervention', attention_required=True)]))
        self.core.report(self.request([{'op': 'update', 'item_id': 'item-a', 'status': 'completed'}]))
        self.assertTrue(self.item()['attention_required'])
        self.assertEqual(self.item()['role'], 'intervention')

    def test_pins_protect_role_attention_and_archive(self):
        self.core.report(self.request([self.define(role='intervention', attention_required=True)]))
        self.core.pin('task-a', 'item-a', True, self.state()['revision'])
        for change in ({'op': 'update', 'item_id': 'item-a', 'role': 'general'},
                       {'op': 'update', 'item_id': 'item-a', 'attention_required': False},
                       {'op': 'archive', 'item_id': 'item-a'}):
            with self.subTest(change=change):
                self.assert_rejected_unchanged(self.request([change]), 'pinned_item')

    def test_stale_role_update_cannot_overwrite_other_session(self):
        self.core.report(self.request([self.define(role='situation')]))
        stale = self.request([{'op': 'update', 'item_id': 'item-a', 'role': 'decision'}])
        self.core.report(self.request([{'op': 'update', 'item_id': 'item-a',
                                        'role': 'next_action'}], session='session-b'))
        self.assert_rejected_unchanged(stale, 'revision_conflict')
        self.assertEqual(self.item()['role'], 'next_action')
        self.assertEqual(self.item()['updated_by']['session_id'], 'session-b')

    def test_safe_edits_roundtrip_metadata_and_older_edits_preserve_it(self):
        self.core.report(self.request([self.define(role='intervention', attention_required=True)]))
        edit = self.core.export_edit('task-a')
        self.assertEqual(edit['items'][0]['role'], 'intervention')
        self.assertTrue(edit['items'][0]['attention_required'])
        edit['items'][0].pop('role')
        edit['items'][0].pop('attention_required')
        edit['items'][0]['value'] = 'Human clarification'
        self.core.apply_edit(edit)
        self.assertEqual(self.item()['role'], 'intervention')
        self.assertTrue(self.item()['attention_required'])
        self.assertEqual(self.item()['updated_by']['kind'], 'human')
        edit = self.core.export_edit('task-a')
        edit['items'][0].update(role='general', attention_required=False)
        self.core.apply_edit(edit)
        self.assertEqual(self.item()['role'], 'general')
        self.assertFalse(self.item()['attention_required'])

    def test_safe_edit_can_add_role_to_legacy_item_without_key_error(self):
        self.core.report(self.request([self.define()]))
        edit = self.core.export_edit('task-a')
        edit['items'][0].update(role='verification', attention_required=False)
        self.core.apply_edit(edit)
        self.assertEqual(self.item()['role'], 'verification')
        self.assertEqual(self.item()['updated_by']['kind'], 'human')

    def test_invalid_roles_and_nonboolean_attention_write_nothing(self):
        for fields in ({'role': 'urgent'}, {'role': None}, {'role': True},
                       {'attention_required': 'true'}, {'attention_required': 1},
                       {'attention_required': None}):
            with self.subTest(fields=fields):
                self.assert_rejected_unchanged(self.request([self.define(**fields)]), 'validation_error')
        self.core.report(self.request([self.define(role='general')]))
        self.assert_rejected_unchanged(self.request([{'op': 'archive', 'item_id': 'item-a',
                                                       'role': 'decision'}]), 'validation_error')

    def test_cli_validates_reports_and_returns_explicit_metadata(self):
        repository = Path(__file__).resolve().parents[1]
        document = self.request([self.define(role='intervention', attention_required=True)])
        path = Path(self.temp.name) / 'report.json'
        path.write_text(json.dumps(document))
        command = [sys.executable, str(repository / 'observerctl'), 'state', '--root', str(self.root)]
        for arguments in (['validate', '--kind', 'report', '--file', str(path)],
                          ['report', '--file', str(path)], ['read', '--task', 'task-a']):
            result = subprocess.run(command + arguments, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            output = json.loads(result.stdout)
            self.assertTrue(output['ok'])
        self.assertEqual(output['state']['items'][0]['role'], 'intervention')
        self.assertTrue(output['state']['items'][0]['attention_required'])


if __name__ == '__main__':
    unittest.main()
