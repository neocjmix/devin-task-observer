"""Explicit per-task read positions, separate from semantic task revisions.

Only cooperating cursor writers use this contract. A schema-valid direct edit is
human-owned UI state, not an authenticated claim that a report was read. Damaged
files are reported and preserved; they are never silently replaced with defaults.
"""
from copy import deepcopy
from datetime import datetime, timezone

from .file_store import FileStore, StateError, MAX_STATE_BYTES, _decode, _encode
from .state_schema import ID, REV, _validate, obj, validate


CURSOR = obj({'through_report_revision': REV,
              'updated_at': {'type': 'string', 'minLength': 1, 'maxLength': 100}})
READ_CURSORS = obj({'schema_version': {'const': 1},
                    'revision': {'type': 'integer', 'minimum': 0},
                    'cursors': {'type': 'object', 'maxProperties': 10000,
                                'propertyNames': ID, 'additionalProperties': CURSOR}})


def published():
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'Observer UI read cursors v1', **deepcopy(READ_CURSORS)}


def validate_cursors(value):
    _validate(value, READ_CURSORS)
    if value['revision'] == 0 and value['cursors']:
        raise ValueError('nonempty cursors require a positive file revision')
    for cursor in value['cursors'].values():
        stamp = datetime.fromisoformat(cursor['updated_at'])
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError('cursor timestamp requires an explicit timezone')
    return value


class ReadCursors:
    def __init__(self, root, lock_timeout=2):
        self.store = FileStore(root, validator=lambda value: validate(value, 'task'),
                               lock_timeout=lock_timeout)
        self.path = self.store.root / 'ui' / 'read-cursors.json'

    def _load(self):
        raw = self.store._read_bytes(self.path, MAX_STATE_BYTES)
        if raw is None:
            return {'schema_version': 1, 'revision': 0, 'cursors': {}}
        try:
            return validate_cursors(_decode(raw))
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            raise StateError('invalid_read_cursors',
                             'Read cursor file is damaged; original preserved',
                             {'path': str(self.path), 'reason': str(error)}) from error

    def snapshot(self):
        """Return cursor state only; opening/refreshing a UI never marks it read."""
        with self.store._locked():
            blocked = self.store._recover()
            value = self._load()
            self._check_positions(value, blocked)
            return value

    def _check_positions(self, value, blocked):
        for task_id, cursor in value['cursors'].items():
            row = self.store._snapshot(task_id, blocked)
            # Preserve orphaned positions and positions for temporarily unhealthy
            # tasks. Their report state is unknown, not evidence of corruption.
            if row['health'] != 'ok' or row['state'] is None:
                continue
            latest = row['state'].get('last_report_revision')
            if latest is not None and cursor['through_report_revision'] > latest:
                raise StateError('invalid_read_cursors', 'Stored read position exceeds the task report position',
                                 {'task_id': task_id})

    def mark(self, task_id, expected_revision, through_report_revision, request_id=None):
        """Confirm only the displayed latest report, without advancing task state.

        Request ID is response correlation, not a second report receipt store.
        Idempotence is positional: a repeated/older acknowledged position is a
        no-op. An advancing mark must match current task and report revisions.
        """
        _validate(task_id, ID)
        _validate(expected_revision, REV)
        _validate(through_report_revision, REV)
        if request_id is not None:
            _validate(request_id, ID)
        with self.store._locked():
            blocked = self.store._recover()
            row = self.store._snapshot(task_id, blocked)
            if row['health'] != 'ok':
                raise StateError('state_unavailable', 'Task state must be healthy before confirming a report',
                                 {'current': row})
            state = row['state']
            if state is None:
                raise StateError('task_not_found', 'task does not exist', {'task_id': task_id})
            latest_report = state.get('last_report_revision')
            if latest_report is None:
                raise StateError('report_position_unknown',
                                 'No reliable report position exists for this older task yet')
            value = self._load()
            self._check_positions(value, blocked)
            cursor = value['cursors'].get(task_id)
            if through_report_revision > latest_report:
                raise StateError('invalid_read_position', 'Read position exceeds the latest committed report')
            result = {'task_id': task_id, 'request_id': request_id}
            if cursor and cursor['through_report_revision'] >= through_report_revision:
                return {**result, 'changed': False, 'cursor_revision': value['revision'],
                        'cursor': deepcopy(cursor)}
            if state['revision'] != expected_revision:
                raise StateError('revision_conflict', 'Task changed after the displayed report; refresh before confirming',
                                 {'current': row})
            if through_report_revision != latest_report:
                raise StateError('read_position_conflict', 'Confirm the displayed latest report position')
            cursor = {'through_report_revision': through_report_revision,
                      'updated_at': datetime.now(timezone.utc).isoformat()}
            value['cursors'][task_id] = cursor
            value['revision'] += 1
            validate_cursors(value)
            self.store._atomic(self.path, _encode(value, MAX_STATE_BYTES))
            return {**result, 'changed': True, 'cursor_revision': value['revision'],
                    'cursor': deepcopy(cursor)}
