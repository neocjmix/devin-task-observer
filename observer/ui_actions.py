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
               'history': {'limit', 'before_revision'}}
    if action not in allowed:
        raise ValueError('unsupported UI action')
    keys = {'action', 'task_id', 'request_id'} | allowed[action]
    if action != 'history':
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
    revision = body.get('expected_revision')
    if type(revision) is not int or revision < 1:
        raise ValueError('a displayed revision is required')
    if action == 'note':
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise ValueError('note must contain 1 to 16000 characters')
        return core.note(task_id, text, revision, rid)
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
