"""Synthetic project hook integration; real Desktop evidence is separate."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from observer import integration
from observer.file_store import StateError
from observer.state_schema import validate


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='observer integration ')
        self.base = Path(self.temp.name)
        self.workspace = self.base / "project 'with spaces'"
        self.workspace.mkdir()
        self.root = self.base / 'state with spaces'
        self.hooks = self.workspace / '.devin/hooks.v1.json'

    def tearDown(self):
        self.temp.cleanup()

    def install(self):
        return integration.install(self.workspace, self.root)

    def payload(self, event='SessionStart', session='actual:session-id'):
        return {'session_id': session, 'hook_event_name': event, 'prompt': 'PRIVATE_PROMPT',
                'last_assistant_message': 'PRIVATE_REPLY', 'summary': 'PRIVATE_SUMMARY',
                'tool_input': {'command': 'PRIVATE_COMMAND'}}

    def hook(self, event='SessionStart', session='actual:session-id'):
        return integration.hook(self.workspace, self.root, self.payload(event, session))

    def test_install_is_explicit_local_and_idempotent(self):
        self.install()
        first = self.hooks.read_bytes()
        self.assertTrue(integration.status(self.workspace, self.root)['installed'])
        self.assertTrue(self.install()['unchanged'])
        self.assertEqual(first, self.hooks.read_bytes())
        config = json.loads(first)
        self.assertEqual(set(config), set(integration.EVENTS))
        for entries in config.values():
            self.assertEqual(entries[0]['hooks'][0]['type'], 'command')
            self.assertEqual(entries[0]['hooks'][0]['timeout'], 3)
        skill = self.workspace / integration.SKILL_DIRECTORY / 'SKILL.md'
        self.assertIn(str(self.root), skill.read_text())
        self.assertTrue((skill.parent / 'references/cli.md').exists())
        self.assertNotIn('../../docs/', skill.read_text())
        self.assertNotIn('allowed-tools:', skill.read_text())

    def test_remove_restores_original_bytes_and_preserves_unrelated_files(self):
        self.hooks.parent.mkdir()
        original = b'{ "Stop": [{"hooks":[{"type":"command","command":"echo unrelated"}]}] }\n'
        self.hooks.write_bytes(original)
        self.hooks.chmod(0o640)
        extra = self.hooks.parent / 'config.local.json'
        extra.write_text('{"unrelated":true}')
        self.install()
        integration.remove(self.workspace, self.root)
        self.assertEqual(self.hooks.read_bytes(), original)
        self.assertEqual(self.hooks.stat().st_mode & 0o777, 0o640)
        self.assertEqual(extra.read_text(), '{"unrelated":true}')
        self.assertFalse((self.workspace / integration.SKILL_DIRECTORY).exists())
        self.assertEqual(integration.status(self.workspace, self.root)['health'], 'not_installed')
        self.assertTrue(integration.remove(self.workspace, self.root)['unchanged'])

    def test_remove_preserves_later_foreign_hooks_and_extra_skill_file(self):
        self.install()
        hooks = json.loads(self.hooks.read_text())
        foreign = {'hooks': [{'type': 'command', 'command': 'echo later-user-hook'}]}
        hooks['Stop'].append(foreign)
        hooks['PreToolUse'] = [foreign]
        self.hooks.write_text(json.dumps(hooks))
        extra = self.workspace / integration.SKILL_DIRECTORY / 'my-notes.md'
        extra.write_text('keep this')
        integration.remove(self.workspace, self.root)
        self.assertEqual(json.loads(self.hooks.read_text()), {'Stop': [foreign], 'PreToolUse': [foreign]})
        self.assertEqual(extra.read_text(), 'keep this')

    def test_refuses_foreign_skill_and_malformed_hook_configuration(self):
        skill = self.workspace / integration.SKILL_DIRECTORY
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('foreign')
        with self.assertRaisesRegex(StateError, 'already exists'):
            self.install()
        self.assertEqual((skill / 'SKILL.md').read_text(), 'foreign')
        (skill / 'SKILL.md').unlink()
        skill.rmdir()
        self.hooks.write_text('{ malformed')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.hooks.read_text(), '{ malformed')

    def test_refuses_modified_owned_files_and_hooks(self):
        self.install()
        skill = self.workspace / integration.SKILL_DIRECTORY / 'SKILL.md'
        original = skill.read_bytes()
        skill.write_text('user edits')
        self.assertEqual(integration.status(self.workspace, self.root)['health'], 'attention')
        for operation in (integration.remove, integration.install):
            with self.assertRaises(StateError):
                operation(self.workspace, self.root)
        self.assertEqual(skill.read_text(), 'user edits')
        skill.write_bytes(original)
        hooks = json.loads(self.hooks.read_text())
        hooks['SessionStart'][0]['hooks'][0]['timeout'] = 9
        self.hooks.write_text(json.dumps(hooks))
        with self.assertRaises(StateError):
            integration.remove(self.workspace, self.root)
        hooks['SessionStart'][0]['hooks'][0]['command'] += ' --unknown-flag'
        self.hooks.write_text(json.dumps(hooks))
        with self.assertRaises(StateError):
            integration.remove(self.workspace, self.root)

    def test_symlinks_cannot_escape_workspace_or_state(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (self.workspace / '.devin').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(StateError):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])

    def test_interrupted_install_resumes_and_interrupted_remove_is_repeatable(self):
        real_write = integration._write
        calls = 0
        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated interruption')
            return real_write(*args, **kwargs)
        with patch.object(integration, '_write', fail_once):
            with self.assertRaises(StateError):
                self.install()
        self.assertEqual(integration.status(self.workspace, self.root)['phase'], 'installing')
        self.install()
        self.assertEqual(integration.status(self.workspace, self.root)['health'], 'ok')
        real_save = integration._save_manifest
        def fail_final(store, workspace, value):
            if value['phase'] == 'removed':
                raise OSError('simulated removal interruption')
            return real_save(store, workspace, value)
        with patch.object(integration, '_save_manifest', fail_final):
            with self.assertRaises(StateError):
                integration.remove(self.workspace, self.root)
        integration.remove(self.workspace, self.root)
        self.assertFalse(self.hooks.exists())

    def test_activity_reader_validates_context_and_does_not_follow_symlinks(self):
        self.assertEqual(integration.session_activity(self.root, 'a')['health'], 'not_seen')
        self.install()
        self.hook(session='a')
        value = integration.session_activity(self.root, 'a')
        self.assertEqual((value['health'], value['last_event']), ('ok', 'SessionStart'))
        self.assertEqual(integration.session_activity(self.root, 'b')['health'], 'not_seen')
        activity = next(self.root.glob('sessions/*/activity.json'))
        activity.unlink()
        private = self.base / 'private.json'
        private.write_text('{"secret":"do not read"}')
        activity.symlink_to(private)
        value = integration.session_activity(self.root, 'a')
        self.assertEqual(value['health'], 'attention')
        self.assertNotIn('secret', json.dumps(value))

    def test_context_identity_and_activity_do_not_store_private_payload(self):
        self.install()
        output = self.hook()
        self.assertEqual(output['hookSpecificOutput']['hookEventName'], 'SessionStart')
        contexts = list(self.root.glob('sessions/*/context.json'))
        self.assertEqual(len(contexts), 1)
        value = json.loads(contexts[0].read_text())
        validate(value, 'context')
        self.assertEqual(value['session_id'], 'actual:session-id')
        self.assertEqual(value['identity_source'], 'devin_hook')
        self.assertIn(str(contexts[0]), output['hookSpecificOutput']['additionalContext'])
        for file in self.root.rglob('*'):
            if file.is_file():
                self.assertNotIn(b'PRIVATE_', file.read_bytes())
        self.assertEqual(self.hook('Stop'), {})
        self.assertNotIn('decision', output)
        activity = json.loads(next(self.root.glob('sessions/*/activity.json')).read_text())
        self.assertEqual(activity['last_event'], 'Stop')

    def test_context_reinjection_after_compaction_resume_and_missing_start(self):
        self.install()
        self.hook()
        self.assertEqual(self.hook('UserPromptSubmit'), {})
        self.assertEqual(self.hook('PostCompaction'), {})
        self.assertIn('hookSpecificOutput', self.hook('UserPromptSubmit'))
        self.assertEqual(self.hook('UserPromptSubmit'), {})
        self.hook('SessionEnd')
        self.assertIn('hookSpecificOutput', self.hook('UserPromptSubmit'))
        self.assertIn('hookSpecificOutput', self.hook('UserPromptSubmit', 'second-session'))
        self.assertEqual(len(list(self.root.glob('sessions/*/context.json'))), 2)

    def test_hook_identity_conflict_preserves_existing_context(self):
        self.install()
        self.hook()
        context = next(self.root.glob('sessions/*/context.json'))
        context.write_text('{"private":"external edit"}')
        with self.assertRaisesRegex(StateError, 'differs'):
            self.hook('UserPromptSubmit')
        self.assertEqual(context.read_text(), '{"private":"external edit"}')

    def test_installed_shell_command_handles_quoted_paths_and_never_blocks(self):
        self.install()
        command = json.loads(self.hooks.read_text())['SessionStart'][0]['hooks'][0]['command']
        for payload, expect_context in ((json.dumps(self.payload()), True), ('not json', False),
                                        (json.dumps(self.payload(session='../bad')), False)):
            result = subprocess.run(command, shell=True, executable='/bin/sh', input=payload,
                                    capture_output=True, text=True, cwd=self.workspace, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertNotIn('decision', value)
            self.assertEqual('hookSpecificOutput' in value, expect_context)
            self.assertEqual(result.stderr, '')
        integration.remove(self.workspace, self.root)
        result = subprocess.run(command, shell=True, executable='/bin/sh', input=json.dumps(self.payload()),
                                capture_output=True, text=True, cwd=self.workspace, timeout=10)
        self.assertEqual((result.returncode, json.loads(result.stdout)), (0, {}))


if __name__ == '__main__':
    unittest.main()
