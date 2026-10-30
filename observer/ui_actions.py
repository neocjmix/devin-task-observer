"""Small, explicit UI command boundary; all mutations use existing OCC core."""
import json

from .cooperative import CooperativeState
from .file_store import StateError
from .state_schema import ID, _validate


def dispatch(root, body):
    if not isinstance(body, dict) or len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode()) > 65536:
        raise ValueError('invalid or oversized UI request')
    action = body.get('action')
    allowed = {'note': {'text'}, 'pin': {'item_id', 'pinned'},
               'history': {'limit', 'before_revision'},
               'resource_put': {'resource'}, 'resource_archive': {'resource_id', 'archived'},
               'resource_open': {'resource_id'}, 'resource_copy': {'resource_id'},
               'lifecycle': {'completed'}, 'observation': {'enabled'}}
    if action not in allowed:
        raise ValueError('unsupported UI action')
    keys = {'action', 'task_id', 'request_id'} | allowed[action]
    if action not in ('history', 'resource_open', 'resource_copy'):
        keys.add('expected_revision')
    if set(body) - keys:
        raise ValueError('unexpected UI request fields')
    _validate(body.get('task_id'), ID)
    _validate(body.get('request_id'), ID)
    core = CooperativeState(root)
    task_id, rid = body['task_id'], body['request_id']
    if action == 'history':
        limit, before = body.get('limit', 20), body.get('before_revision')
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('history limit must be 1 to 20')
        if before is not None and (type(before) is not int or before < 1):
            raise ValueError('invalid history cursor')
        return core.store.history_page(task_id, limit, before)
    if action in ('resource_open', 'resource_copy'):
        row = core.read(task_id)
        if row['health'] != 'ok':
            raise StateError('unhealthy_task', '마지막 정상 기록의 링크는 열 수 없습니다. 기록을 복구한 뒤 시도하세요.')
        resource = next((r for r in row['state'].get('resources', []) if r['resource_id'] == body.get('resource_id')), None)
        if resource is None:
            raise StateError('resource_not_found', '등록된 리소스를 찾을 수 없습니다.')
        return {'native_action': {'kind': 'copy' if action == 'resource_copy' else resource['open_mode'],
                                  'target': resource['target']}}
    revision = body.get('expected_revision')
    if type(revision) is not int or revision < 1:
        raise ValueError('a displayed revision is required')
    if action == 'note':
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise ValueError('note must contain 1 to 16000 characters')
        return core.note(task_id, text, revision, rid)
    if action == 'resource_put':
        return core.resource_put(task_id, body.get('resource'), revision, rid)
    if action == 'resource_archive':
        return core.resource_archive(task_id, body.get('resource_id'), body.get('archived'), revision, rid)
    if action == 'lifecycle':
        return core.lifecycle(task_id, body.get('completed'), revision, rid)
    if action == 'observation':
        return core.observation(task_id, body.get('enabled'), revision, rid)
    _validate(body.get('item_id'), ID)
    return core.pin(task_id, body['item_id'], body.get('pinned'), revision, rid)


def response(root, body, *, read_only=False):
    rid = body.get('request_id') if isinstance(body, dict) else None
    try:
        if read_only:
            raise StateError('read_only', '이 화면은 합성 데이터 미리보기입니다. 저장할 수 없습니다.')
        return {'request_id': rid, 'ok': True, 'result': dispatch(root, body)}
    except (StateError, ValueError, TypeError, OSError, RecursionError) as error:
        return {'request_id': rid, 'ok': False, 'error': {
            'code': getattr(error, 'code', 'invalid_action'), 'message': str(error)}}
