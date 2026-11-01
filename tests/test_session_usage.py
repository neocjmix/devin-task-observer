"""Synthetic-only optional usage reads; never start Devin or a model."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from observer.session_usage import load_session_usage, SessionUsageUnavailable, UsageCache, MAX_TOKENS


NOW = '2026-10-31T00:00:00+00:00'


def message(mid='message-a', new=100, cache=900, output=50, **extra):
    return {'role': 'assistant', 'message_id': mid,
            'metadata': {'metrics': {'input_tokens': new, 'cache_read_tokens': cache,
                                    'output_tokens': output, 'cache_creation_tokens': None, **extra}}}


class SessionUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'synthetic.sqlite3'
        self.workspace = str(self.root / 'allowed')
        self.config = {'source': str(self.path), 'workspaces': [self.workspace]}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''CREATE TABLE refinery_schema_history(version INTEGER);
                INSERT INTO refinery_schema_history VALUES(17);
                CREATE TABLE sessions(id TEXT, working_directory TEXT, last_activity_at INTEGER);
                CREATE TABLE message_nodes(session_id TEXT,node_id INTEGER,chat_message TEXT,created_at INTEGER);''')
            db.execute('INSERT INTO sessions VALUES(?,?,?)', ('session-a', self.workspace, 1793356115))

    def add(self, value, node=1, session='session-a', raw=False):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO message_nodes VALUES(?,?,?,?)',
                       (session, node, value if raw else json.dumps(value), 9999999999))

    def read(self):
        return load_session_usage(self.config, measured_at=NOW)

    def test_native_cache_is_additive_and_timing_duplicates_are_not_billed_twice(self):
        self.add(message(tpot_ms=1.5))
        self.add(message(tpot_ms=2.5), 2)
        self.add(message('message-b', 20, 1000, 10, context_input_tokens=1020), 3)
        row = self.read()['session-a']
        self.assertEqual((row['input_total'], row['input_new'], row['input_cached'], row['output']),
                         (2020, 120, 1900, 60))
        self.assertEqual((row['message_count'], row['metric_node_count'], row['duplicate_node_count']), (2, 3, 1))
        self.assertEqual(row['evidence'][0], {'message_id': 'message-a', 'node_ids': [1, 2]})
        self.assertEqual(row['source'], 'devin_local_sqlite_v17')
        self.assertTrue(row['may_include_inherited_history'])
        self.assertFalse(row['billing_usage'])
        self.assertFalse(row['lifetime_complete'])

    def test_read_time_and_source_activity_are_separate_from_rewritten_node_timestamp(self):
        self.add(message())
        row = self.read()['session-a']
        self.assertEqual(row['measured_at'], NOW)
        self.assertEqual(row['source_activity_at'], '2026-10-30T10:28:35+00:00')
        self.assertNotIn('created_at', row)

    def test_exact_workspace_allowlist_and_no_implicit_path_expansion(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO sessions VALUES(?,?,?)', ('excluded', self.workspace + '-other', 1))
        self.add(message())
        self.add('not-json', session='excluded', raw=True)
        self.config['workspaces'].append(self.workspace)
        self.assertEqual(set(self.read()), {'session-a'})

    def test_source_and_input_config_unchanged_after_repeated_read(self):
        self.add(message())
        before, config = self.path.read_bytes(), deepcopy(self.config)
        first = self.read()
        self.assertEqual(first, self.read())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.config, config)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['synthetic.sqlite3'])

    def test_missing_database_is_not_created(self):
        self.config['source'] = str(self.root / 'missing.sqlite3')
        with self.assertRaisesRegex(SessionUsageUnavailable, 'source_read_failed'):
            self.read()
        self.assertFalse(Path(self.config['source']).exists())

    def test_unsupported_schema_rejected_before_metrics_query(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE refinery_schema_history SET version=18')
        with self.assertRaisesRegex(SessionUsageUnavailable, 'unsupported_source_schema'):
            self.read()

    def test_conflicting_message_metrics_are_unknown_and_do_not_hide_other_session(self):
        self.add(message())
        self.add(message(output=51), 2)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO sessions VALUES(?,?,?)', ('session-b', self.workspace, 1))
        self.add(message(), session='session-b')
        rows = self.read()
        self.assertEqual(rows['session-a']['reason'], 'conflicting_message_metrics')
        self.assertIsNone(rows['session-a']['input_total'])
        self.assertEqual(rows['session-a']['evidence'], [])
        self.assertEqual(rows['session-b']['input_total'], 1000)

    def test_inherited_message_ids_in_different_sessions_are_not_merged(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO sessions VALUES(?,?,?)', ('fork', self.workspace, 1))
        self.add(message())
        self.add(message(), session='fork')
        rows = self.read()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows['fork']['input_total'], rows['session-a']['input_total'])
        self.assertNotIn('task_id', rows['fork'])

    def test_absent_metrics_are_unknown_not_zero_but_measured_zero_is_valid(self):
        self.add({'message_id': 'user-a', 'role': 'user', 'content': 'Synthetic'})
        self.assertEqual(self.read()['session-a']['reason'], 'metrics_missing')
        self.add(message(new=0, cache=0, output=0), 2)
        self.assertEqual(self.read()['session-a']['input_total'], 0)

    def test_invalid_counts_and_unsupported_shapes_never_produce_partial_totals(self):
        cases = [
            (message(new=-1), 'invalid_token_count'),
            (message(new=True), 'invalid_token_count'),
            (message(cache=None), 'invalid_token_count'),
            (message(output=1.5), 'invalid_token_count'),
            (message(output=MAX_TOKENS + 1), 'invalid_token_count'),
            (message(cache_creation_tokens=1), 'cache_creation_unsupported'),
            (message(cache_creation_tokens=False), 'cache_creation_unsupported'),
            (message(context_input_tokens=999), 'inconsistent_context_input'),
            (message(mid=''), 'invalid_message_identity'),
            ({'role': 'tool', 'metadata': {'metrics': {}}}, 'unsupported_metrics'),
        ]
        for value, reason in cases:
            with self.subTest(reason=reason, value=value):
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute('DELETE FROM message_nodes')
                self.add(message('good'))
                self.add(value, 2)
                row = self.read()['session-a']
                self.assertEqual(row['reason'], reason)
                self.assertTrue(all(row[k] is None for k in ('input_total', 'input_new', 'input_cached', 'output')))

    def test_total_count_bound(self):
        self.add(message('a', MAX_TOKENS, 0, 0))
        self.add(message('b', 1, 0, 0), 2)
        self.assertEqual(self.read()['session-a']['reason'], 'token_limit_exceeded')

    def test_malformed_and_size_bounds(self):
        self.add('{malformed', raw=True)
        self.assertEqual(self.read()['session-a']['reason'], 'malformed_message')
        with patch('observer.session_usage.MAX_MESSAGE_BYTES', 2):
            self.assertEqual(self.read()['session-a']['reason'], 'message_size_or_type_unsupported')

    def test_node_and_session_limits(self):
        self.add(message())
        self.add(message(), 2)
        with patch('observer.session_usage.MAX_NODES', 1):
            self.assertEqual(self.read()['session-a']['reason'], 'node_limit_exceeded')
        with patch('observer.session_usage.MAX_SESSIONS', 0):
            with self.assertRaisesRegex(SessionUsageUnavailable, 'session_limit_exceeded'):
                self.read()

    def test_session_byte_and_elapsed_bounds(self):
        self.add(message())
        with patch('observer.session_usage.MAX_SESSION_BYTES', 1):
            self.assertEqual(self.read()['session-a']['reason'], 'session_size_exceeded')
        with patch('observer.session_usage.MAX_READ_SECONDS', -1):
            self.assertEqual(self.read()['session-a']['reason'], 'read_budget_exceeded')

    def test_native_query_is_read_only_and_transaction_scoped(self):
        self.add(message())
        original_connect = sqlite3.connect
        queries, connections = [], []
        def connect(database_uri, **kwargs):
            self.assertTrue(database_uri.endswith('?mode=ro'))
            self.assertTrue(kwargs['uri'])
            db = original_connect(database_uri, **kwargs)
            db.set_trace_callback(queries.append)
            connections.append(db)
            return db
        with patch('observer.session_usage.sqlite3.connect', side_effect=connect):
            self.assertEqual(self.read()['session-a']['status'], 'available')
        self.assertIn('PRAGMA query_only=ON', queries)
        self.assertIn('BEGIN', queries)
        self.assertIn('ROLLBACK', queries)
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute('SELECT 1')

    def test_exclusive_source_lock_fails_without_source_mutation(self):
        self.add(message())
        with closing(sqlite3.connect(self.path)) as writer, writer:
            writer.execute('BEGIN EXCLUSIVE')
            with self.assertRaisesRegex(SessionUsageUnavailable, 'source_read_failed'):
                self.read()
            writer.rollback()
        self.assertEqual(self.read()['session-a']['status'], 'available')

    def test_requires_explicit_configuration_and_timezone(self):
        for config in ({}, {'source': 'relative', 'workspaces': [self.workspace]},
                       {'source': str(self.path), 'workspaces': []}):
            with self.assertRaisesRegex(SessionUsageUnavailable, 'invalid_configuration'):
                load_session_usage(config)
        with self.assertRaisesRegex(SessionUsageUnavailable, 'invalid_measurement_time'):
            load_session_usage(self.config, measured_at='2026-10-31T00:00:00')


class UsageCacheTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0
        self.stamp = NOW
        self.calls = []
        self.failure = None
        self.config = {'source': '/synthetic/source.db', 'workspaces': ['/synthetic/a']}
        self.cache = UsageCache(monotonic=lambda: self.clock, timestamp=lambda: self.stamp,
                                loader=self.load)

    def load(self, config, *, measured_at):
        self.calls.append((deepcopy(config), measured_at))
        if self.failure:
            raise SessionUsageUnavailable(self.failure)
        return {'session': {'workspace': config['workspaces'][0], 'measured_at': measured_at,
                            'input_total': len(self.calls), 'status': 'available'}}

    def test_two_second_poll_reuses_snapshot_until_fifteen_seconds(self):
        first = self.cache.read(self.config)
        for second in range(2, 15, 2):
            self.clock = second
            self.stamp = '2026-10-31T00:00:14+00:00'
            self.assertEqual(self.cache.read(self.config), first)
        self.assertEqual(len(self.calls), 1)
        self.clock = 15
        self.stamp = '2026-10-31T00:00:15+00:00'
        second = self.cache.read(self.config)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(second['sessions']['session']['measured_at'], self.stamp)
        self.assertEqual(second['last_attempt'], self.stamp)

    def test_manual_force_refresh_bypasses_interval(self):
        self.cache.read(self.config)
        self.clock = 2
        self.cache.read(self.config, force=True)
        self.assertEqual(len(self.calls), 2)
        self.clock = 16
        self.cache.read(self.config)
        self.assertEqual(len(self.calls), 2)
        self.clock = 17
        self.cache.read(self.config)
        self.assertEqual(len(self.calls), 3)

    def test_failure_keeps_same_scope_last_good_and_recovery_clears_error(self):
        first = self.cache.read(self.config)
        self.clock = 15
        self.stamp = '2026-10-31T00:00:15+00:00'
        self.failure = 'source_read_failed'
        failed = self.cache.read(self.config)
        self.assertTrue(failed['stale'])
        self.assertEqual(failed['error'], self.failure)
        self.assertEqual(failed['sessions'], first['sessions'])
        self.assertEqual(failed['last_attempt'], self.stamp)
        self.clock = 17
        self.assertEqual(self.cache.read(self.config), failed)
        self.assertEqual(len(self.calls), 2)
        self.clock = 30
        self.failure = None
        recovered = self.cache.read(self.config)
        self.assertFalse(recovered['stale'])
        self.assertIsNone(recovered['error'])
        self.assertEqual(len(self.calls), 3)

    def test_workspace_or_source_change_drops_last_good_before_failed_read(self):
        for field, replacement in [('workspaces', ['/synthetic/other']), ('source', '/other/source.db')]:
            with self.subTest(field=field):
                self.failure = None
                self.cache.read(self.config, force=True)
                changed = {**self.config, field: replacement}
                self.failure = 'source_read_failed'
                result = self.cache.read(changed)
                self.assertEqual(result['sessions'], {})
                self.assertTrue(result['stale'])
                self.assertEqual(result['error'], 'source_read_failed')
                self.failure = None
                # Returning to a previous scope also reads again; no old-scope registry.
                self.assertTrue(self.cache.read(self.config)['sessions'])

    def test_invalid_scope_never_leaks_prior_sessions(self):
        self.cache.read(self.config)
        result = self.cache.read({'source': 'relative', 'workspaces': []})
        self.assertEqual(result['sessions'], {})
        self.assertEqual(result['error'], 'invalid_configuration')
        self.assertTrue(result['stale'])
        self.assertEqual(len(self.calls), 1)

    def test_equivalent_workspace_order_and_other_config_fields_do_not_invalidate(self):
        config = {**self.config, 'workspaces': ['/synthetic/a', '/synthetic/b']}
        first = self.cache.read(config)
        changed = {**config, 'workspaces': ['/synthetic/b', '/synthetic/a', '/synthetic/a'], 'port': 8771}
        self.assertEqual(self.cache.read(changed), first)
        self.assertEqual(len(self.calls), 1)

    def test_returned_objects_cannot_modify_cached_state(self):
        result = self.cache.read(self.config)
        result['sessions']['session']['input_total'] = -999
        result['error'] = 'fake'
        again = self.cache.read(self.config)
        self.assertEqual(again['sessions']['session']['input_total'], 1)
        self.assertIsNone(again['error'])

    def test_missing_metrics_are_not_a_global_error_and_first_failure_has_no_values(self):
        cache = UsageCache(monotonic=lambda: self.clock, timestamp=lambda: self.stamp,
                           loader=lambda *a, **k: {'session': {'status': 'unavailable', 'reason': 'metrics_missing'}})
        result = cache.read(self.config)
        self.assertIsNone(result['error'])
        self.assertFalse(result['stale'])
        self.assertEqual(result['sessions']['session']['reason'], 'metrics_missing')
        self.failure = 'unsupported_source_schema'
        result = self.cache.read(self.config)
        self.assertTrue(result['stale'])
        self.assertEqual(result['sessions'], {})

    def test_interval_must_be_positive_and_finite(self):
        for interval in [0, -1, True, float('nan'), float('inf'), '15']:
            with self.assertRaises(ValueError):
                UsageCache(interval=interval)


if __name__ == '__main__':
    unittest.main()
