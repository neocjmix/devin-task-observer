"""Deterministic cooperative task state. Working sessions own semantic judgment."""
import copy
import uuid
from datetime import datetime, timezone

from .file_store import FileStore, StateError
from .state_schema import validate, ID, SID, _validate

HUMAN = {'kind': 'human', 'session_id': None}


def now():
    return datetime.now(timezone.utc).isoformat()


def checked(value, kind):
    try:
        return validate(value, kind)
    except ValueError as error:
        raise StateError('validation_error', str(error)) from error


class CooperativeState:
    def __init__(self, root, lock_timeout=2):
        self.store = FileStore(root, validator=lambda x: validate(x, 'task'), lock_timeout=lock_timeout)

    def read(self, task_id):
        row = self.store.read(task_id)
        if row['health'] == 'ok' and row['state'] is None:
            raise StateError('task_not_found', 'task does not exist', {'task_id': task_id})
        return row

    def list(self, session_id=None):
        rows = self.store.list()
        if session_id is None:
            return rows
        return [row for row in rows if any(c['session_id'] == session_id for c in
                (row.get('state') or row.get('last_good') or {}).get('contributions', []))]

    def create(self, task_id, title, session_id=None, request_id=None):
        _validate(task_id, ID)
        if session_id is not None:
            _validate(session_id, SID)
        request = {'action': 'create', 'task_id': task_id, 'title': title, 'session_id': session_id}
        def transform(states):
            stamp = now()
            value = {'schema_version': 1, 'task_id': task_id, 'title': title, 'revision': 1,
                     'updated_at': stamp, 'updated_by': HUMAN.copy(), 'items': [], 'notes': [],
                     'view': {'markdown': ''}, 'contributions': []}
            if session_id:
                value['contributions'].append({'session_id': session_id, 'reporting': False,
                                               'linked_at': stamp, 'last_report_at': None})
            checked(value, 'task')
            return {task_id: value}
        return self.store.transact(request_id or str(uuid.uuid4()), request, {task_id: None}, transform)

    def _control(self, task_id, expected_revision, action, arguments, change, request_id=None):
        request = {'action': action, 'task_id': task_id, 'expected_revision': expected_revision, **arguments}
        def transform(states):
            value = copy.deepcopy(states[task_id])
            value['revision'] += 1
            value['updated_at'] = now()
            value['updated_by'] = HUMAN.copy()
            change(value)
            checked(value, 'task')
            return {task_id: value}
        return self.store.transact(request_id or str(uuid.uuid4()), request, {task_id: expected_revision}, transform)

    def link(self, task_id, session_id, expected_revision, request_id=None):
        _validate(session_id, SID)
        def change(value):
            if any(c['session_id'] == session_id for c in value['contributions']):
                raise StateError('already_linked', 'session is already linked')
            value['contributions'].append({'session_id': session_id, 'reporting': False,
                                           'linked_at': now(), 'last_report_at': None})
        return self._control(task_id, expected_revision, 'link', {'session_id': session_id}, change, request_id)

    def reporting(self, task_id, session_id, enabled, expected_revision, request_id=None):
        if type(enabled) is not bool:
            raise StateError('validation_error', 'enabled must be boolean')
        def change(value):
            self._contribution(value, session_id)['reporting'] = enabled
        return self._control(task_id, expected_revision, 'reporting', {'session_id': session_id, 'enabled': enabled}, change, request_id)

    @staticmethod
    def _contribution(value, session_id):
        for contribution in value['contributions']:
            if contribution['session_id'] == session_id:
                return contribution
        raise StateError('session_not_linked', 'session is not linked to task', {'task_id': value['task_id']})

    @staticmethod
    def _item(value, item_id):
        for item in value['items']:
            if item['item_id'] == item_id:
                return item
        raise StateError('item_not_found', 'tracking item not found', {'item_id': item_id})

    @staticmethod
    def _stamp(item, task, actor, report_id):
        item.update(updated_by=copy.deepcopy(actor), item_revision=item['item_revision'] + 1,
                    commit_sequence=task['revision'], updated_at=task['updated_at'], report_id=report_id)
        if actor['kind'] == 'session':
            item['verification'] = 'reported'

    def report(self, request):
        checked(request, 'report')
        if not set(request['changes']) <= set(request['expected_revisions']):
            raise StateError('validation_error', 'each changed task needs an expected revision')
        actor = {'kind': 'session', 'session_id': request['session_id']}
        def transform(states):
            result = {}
            for task_id, changes in request['changes'].items():
                value = copy.deepcopy(states[task_id])
                contribution = self._contribution(value, request['session_id'])
                if not contribution['reporting']:
                    raise StateError('reporting_disabled', 'enable reporting for this task/session before reporting')
                if not changes:
                    raise StateError('validation_error', 'report must contain changed items')
                ids = [c['item_id'] for c in changes]
                if len(ids) != len(set(ids)):
                    raise StateError('validation_error', 'an item may be changed once per report')
                value['revision'] += 1
                value['updated_at'] = now()
                value['updated_by'] = actor.copy()
                contribution['last_report_at'] = value['updated_at']
                for operation in changes:
                    item_id = operation['item_id']
                    if operation['op'] == 'define':
                        if any(i['item_id'] == item_id for i in value['items']):
                            raise StateError('item_exists', 'reuse existing item ID with update')
                        if not {'title', 'value', 'status', 'evidence'} <= operation.keys():
                            raise StateError('validation_error', 'define requires title, value, status and evidence')
                        item = {k: copy.deepcopy(operation[k]) for k in ('item_id', 'title', 'value', 'status', 'evidence')}
                        item.update(pinned=False, archived=False, created_by=actor.copy(), item_revision=0,
                                    verification='reported')
                        value['items'].append(item)
                    else:
                        item = self._item(value, item_id)
                        if item['pinned']:
                            raise StateError('pinned_item', 'explicitly pinned item cannot be changed or archived', {'item_id': item_id})
                        if item['archived']:
                            raise StateError('archived_item', 'archived item must be restored by explicit editing')
                        if operation['op'] == 'archive':
                            if set(operation) != {'op', 'item_id'}:
                                raise StateError('validation_error', 'archive accepts only item_id')
                            item['archived'] = True
                        else:
                            if len(operation) == 2:
                                raise StateError('validation_error', 'update has no changes')
                            for key in ('title', 'value', 'status', 'evidence'):
                                if key in operation:
                                    item[key] = copy.deepcopy(operation[key])
                    self._stamp(item, value, actor, request['report_id'])
                checked(value, 'task')
                result[task_id] = value
            return result
        return self.store.transact(request['report_id'], request, request['expected_revisions'], transform)

    def pin(self, task_id, item_id, pinned, expected_revision, request_id=None):
        if type(pinned) is not bool:
            raise StateError('validation_error', 'pinned must be boolean')
        rid = request_id or str(uuid.uuid4())
        def change(value):
            item = self._item(value, item_id)
            item['pinned'] = pinned
            self._stamp(item, value, HUMAN, rid)
        return self._control(task_id, expected_revision, 'pin', {'item_id': item_id, 'pinned': pinned}, change, rid)

    def note(self, task_id, text, expected_revision, request_id=None):
        def change(value):
            value['notes'].append({'note_id': str(uuid.uuid4()), 'text': text, 'created_at': now()})
        return self._control(task_id, expected_revision, 'note', {'text': text}, change, request_id)

    def export_edit(self, task_id):
        row = self.read(task_id)
        if row['health'] != 'ok':
            raise StateError('unhealthy_task', 'restore/repair task before editing', row)
        value = row['state']
        return {'schema_version': 1, 'task_id': task_id, 'expected_revision': value['revision'],
                'title': value['title'], 'view': copy.deepcopy(value['view']),
                'items': [{k: copy.deepcopy(i[k]) for k in ('item_id', 'title', 'value', 'status', 'evidence', 'pinned', 'archived')}
                          for i in value['items']]}

    def apply_edit(self, document, request_id=None):
        checked(document, 'edit')
        rid = request_id or str(uuid.uuid4())
        def change(value):
            ids = [i['item_id'] for i in document['items']]
            if len(ids) != len(set(ids)) or set(ids) != {i['item_id'] for i in value['items']}:
                raise StateError('validation_error', 'safe edit preserves item IDs; use archive instead of removal')
            value['title'] = document['title']
            value['view'] = copy.deepcopy(document['view'])
            for edited in document['items']:
                item = self._item(value, edited['item_id'])
                if any(item[k] != v for k, v in edited.items()):
                    item.update(copy.deepcopy(edited))
                    self._stamp(item, value, HUMAN, rid)
        return self._control(document['task_id'], document['expected_revision'], 'human_edit',
                             {'document': document}, change, rid)
