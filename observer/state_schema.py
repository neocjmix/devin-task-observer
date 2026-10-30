"""Version 1 cooperative contracts; only the schema keywords used here are evaluated.

No model, remote references or optional validator dependency is needed. Published
schemas are generated from these same definitions by `observerctl state schema`.
"""
import json
import re

ID = {'type': 'string', 'pattern': r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$'}
TEXT = {'type': 'string', 'maxLength': 16000}
SID = {'type': 'string', 'minLength': 1, 'maxLength': 200, 'pattern': r'^[^\s/\\]+$'}
REV = {'type': 'integer', 'minimum': 1}


def obj(properties, required=None):
    return {'type': 'object', 'properties': properties,
            'required': list(properties) if required is None else required,
            'additionalProperties': False}


def array(items, limit=1000):
    return {'type': 'array', 'items': items, 'maxItems': limit}


ACTOR = obj({'kind': {'enum': ['human', 'session', 'legacy']},
             'session_id': {'anyOf': [SID, {'type': 'null'}]}})
EVIDENCE = obj({'session_id': SID, 'reference': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
                'kind': {'enum': ['session_event', 'local_artifact', 'report']}})
ITEM = obj({'item_id': ID, 'title': {'type': 'string', 'minLength': 1, 'maxLength': 300},
            'value': {}, 'status': {'type': 'string', 'minLength': 1, 'maxLength': 100},
            'evidence': array(EVIDENCE, 100), 'pinned': {'type': 'boolean'},
            'archived': {'type': 'boolean'}, 'created_by': ACTOR, 'updated_by': ACTOR,
            'item_revision': REV, 'commit_sequence': REV, 'updated_at': TEXT,
            'report_id': ID, 'verification': {'enum': ['reported', 'legacy_import']}})
TASK = obj({'schema_version': {'const': 1}, 'task_id': ID, 'title': {'type': 'string', 'minLength': 1, 'maxLength': 300},
            'revision': REV, 'updated_at': TEXT, 'updated_by': ACTOR,
            'contributions': array(obj({'session_id': SID, 'reporting': {'type': 'boolean'},
                'linked_at': TEXT, 'last_report_at': {'anyOf': [TEXT, {'type': 'null'}]}}), 100),
            'items': array(ITEM), 'notes': array(obj({'note_id': ID, 'text': TEXT, 'created_at': TEXT})),
            'view': obj({'markdown': TEXT})})
# Imported source facts remain explicit snapshots; arbitrary original row data lives
# in checksummed, immutable archive files rather than being guessed into new fields.
LEGACY = obj({'migration_id': ID, 'source_task_id': ID, 'source_revision': REV,
              'description': TEXT, 'lifecycle': TEXT, 'created_at': {'type': 'integer'},
              'archive_ref': TEXT, 'archive_sha256': {'type': 'string', 'pattern': r'^[a-f0-9]{64}$'},
              'view': {'anyOf': [{'type': 'object'}, {'type': 'null'}]},
              'review_cursors': array({'type': 'object'}),
              'resources': array({'type': 'object'}),
              'archived_contributions': array({'type': 'object'})})
TASK['properties']['legacy'] = LEGACY

CHANGE_FIELDS = {'item_id': ID, 'title': ITEM['properties']['title'], 'value': {},
                 'status': ITEM['properties']['status'], 'evidence': array(EVIDENCE, 100)}
CHANGE = {'anyOf': [
    obj({'op': {'const': 'define'}, **CHANGE_FIELDS}),
    {**obj({'op': {'const': 'update'}, **CHANGE_FIELDS}, ['op', 'item_id']), 'minProperties': 3},
    obj({'op': {'const': 'archive'}, 'item_id': ID}),
]}
REVISIONS = {'type': 'object', 'minProperties': 1, 'maxProperties': 20,
             'propertyNames': ID, 'additionalProperties': REV}
REPORT = obj({'schema_version': {'const': 1}, 'report_id': ID,
              'session_id': SID, 'expected_revisions': REVISIONS,
              'changes': {'type': 'object', 'minProperties': 1, 'maxProperties': 20,
                          'propertyNames': ID, 'additionalProperties': array(CHANGE, 100)}})
EDIT_ITEM = obj({k: ITEM['properties'][k] for k in ('item_id', 'title', 'value', 'status', 'evidence', 'pinned', 'archived')})
EDIT = obj({'schema_version': {'const': 1}, 'task_id': ID, 'expected_revision': REV,
            'title': TASK['properties']['title'], 'items': array(EDIT_ITEM),
            'view': TASK['properties']['view']})
CONTEXT = obj({'schema_version': {'const': 1}, 'session_id': SID,
               'identity_source': {'enum': ['explicit_registration', 'devin_hook']}})
SCHEMAS = {'task': TASK, 'report': REPORT, 'edit': EDIT, 'context': CONTEXT}
MAX_BYTES = 2 * 1024 * 1024


def _validate(value, rule, path='$'):
    def fail(message):
        raise ValueError(path + ': ' + message)
    if 'anyOf' in rule:
        for child in rule['anyOf']:
            try:
                _validate(value, child, path)
                return
            except ValueError:
                pass
        fail('does not match a supported schema')
    kinds = {'object': dict, 'array': list, 'string': str, 'integer': int,
             'boolean': bool, 'null': type(None)}
    if 'type' in rule and type(value) is not kinds[rule['type']]:
        fail('expected ' + rule['type'])
    if 'const' in rule and (type(value) is not type(rule['const']) or value != rule['const']):
        fail('unsupported value/version')
    if 'enum' in rule and value not in rule['enum']:
        fail('unsupported value')
    if type(value) is dict:
        if len(value) < rule.get('minProperties', 0) or len(value) > rule.get('maxProperties', 10000):
            fail('object size outside limit')
        for key in rule.get('required', []):
            if key not in value:
                fail('missing ' + key)
        props = rule.get('properties', {})
        for key, child in value.items():
            if 'propertyNames' in rule:
                _validate(key, rule['propertyNames'], path + '.<key>')
            extra = rule.get('additionalProperties', {})
            if key not in props and extra is False:
                fail('unknown field ' + key)
            _validate(child, props.get(key, extra) or {}, path + '.' + key)
    elif type(value) is list:
        if len(value) > rule.get('maxItems', 10000):
            fail('too many entries')
        for i, child in enumerate(value):
            _validate(child, rule.get('items', {}), f'{path}[{i}]')
    elif type(value) is str:
        if not rule.get('minLength', 0) <= len(value) <= rule.get('maxLength', MAX_BYTES):
            fail('text length outside limit')
        if 'pattern' in rule and re.fullmatch(rule['pattern'], value) is None:
            fail('invalid identifier')
    elif type(value) is int and value < rule.get('minimum', value):
        fail('number below minimum')


def validate(value, kind):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise ValueError('not finite UTF-8 JSON') from error
    if len(encoded) > MAX_BYTES:
        raise ValueError('document exceeds 2 MiB')
    try:
        _validate(value, SCHEMAS[kind])
    except RecursionError as error:
        raise ValueError('JSON nesting too deep') from error
    if kind == 'edit':
        ids = [i['item_id'] for i in value['items']]
        if len(ids) != len(set(ids)):
            raise ValueError('$.items: duplicate ID')
    if kind == 'report':
        if not set(value['changes']) <= set(value['expected_revisions']):
            raise ValueError('$.changes: every changed task needs an expected revision')
        for task_id, changes in value['changes'].items():
            ids = [c['item_id'] for c in changes]
            if not ids or len(ids) != len(set(ids)):
                raise ValueError('$.changes.' + task_id + ': expected nonempty changes with unique item IDs')
    if kind == 'task':
        for key, id_key in (('items', 'item_id'), ('contributions', 'session_id'), ('notes', 'note_id')):
            ids = [x[id_key] for x in value[key]]
            if len(ids) != len(set(ids)):
                raise ValueError('$.' + key + ': duplicate ID')
        sessions = {x['session_id'] for x in value['contributions']}
        for item in value['items']:
            if item['commit_sequence'] > value['revision']:
                raise ValueError('item commit_sequence exceeds task revision')
            for evidence in item['evidence']:
                if evidence['session_id'] not in sessions:
                    raise ValueError('evidence refers to an unlinked session')
        for actor in [value['updated_by']] + [i[k] for i in value['items'] for k in ('created_by', 'updated_by')]:
            if actor['kind'] in ('human', 'legacy') and actor['session_id'] is not None:
                raise ValueError('human/legacy actor cannot claim a session ID')
            if actor['kind'] == 'session' and actor['session_id'] not in sessions:
                raise ValueError('actor refers to an unlinked session')
    return value


def published(kind):
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'Observer cooperative ' + kind + ' v1', **SCHEMAS[kind]}
