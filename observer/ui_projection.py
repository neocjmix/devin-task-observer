"""UI V2 S0: pure, JSON-compatible projection of already acquired snapshots.

This module performs no I/O, recovery, writes, model calls or UI actions. Acquire
task snapshots through CooperativeState.list(), not independent raw-file reads:
that existing boundary may recover transactions before returning coherent tasks.
Observations, runtime and hook activity are separately acquired snapshots, not
one globally atomic view. See docs/slices/UI-V2-S0.md for the input contract.
"""
from copy import deepcopy
import hashlib
import json

from .state_schema import validate
from .discovery import resource_key, scoped_resource


# False means there is no live source contract yet, not an empty/zero value.
CAPABILITIES = {
    'task_lifecycle': False,
    'task_observation_toggle': False,
    'task_read_cursor': False,
    'typed_item_roles': True,
    'registered_resources': False,
    'session_tokens': False,
    'session_locator': False,
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _display_state(row):
    """Never render external edits as committed state, even if valid JSON."""
    errors = []
    choices = []
    if row['health'] == 'ok':
        choices.append(('current', row.get('state')))
    choices.append(('last_good', row.get('last_good')))
    for origin, state in choices:
        if state is None:
            if origin == 'current':
                errors.append('current: missing state')
            continue
        try:
            validate(state, 'task')
            if state['task_id'] != row['task_id']:
                raise ValueError('task ID does not match snapshot')
        except ValueError as error:
            errors.append(origin + ': ' + str(error))
            continue
        return state, origin, errors
    return None, 'unavailable', errors


def project(task_snapshots, *, observations=None, session_activity=None,
            runtime=None, state_errors=()):
    """Normalize existing v1 data without inferring semantics or task ownership.

    Input is internal JSON data: FileStore.list() envelopes, observations() output,
    a session-ID keyed session_activity() map, and runtime.snapshot() output.
    Pass acquisition failures in state_errors instead of pretending no tasks exist.
    Output is detached from inputs. Items/contributions are keyed within each task;
    sessions and resource candidates are shared. Original item/note order survives
    in item_ids/metadata.notes. No persisted state format changes are needed.
    """
    rows, observed, activities, run, errors = deepcopy(
        (task_snapshots, observations, session_activity or {}, runtime, list(state_errors)))
    result = {
        'projection_version': 1,
        'capabilities': dict(CAPABILITIES),
        'tasks': {}, 'items': {}, 'contributions': {}, 'sessions': {},
        'resource_candidates': {},
        'health': {
            'state_errors': errors,
            'runtime': run,
            'observations': (None if observed is None else
                             {key: value for key, value in observed.items() if key != 'sessions'}),
        },
    }

    def session(session_id):
        return result['sessions'].setdefault(session_id, {
            'session_id': session_id, 'task_ids': [],
            'observation': None, 'activity': activities.get(session_id),
            'plan_snapshots': [], 'resource_candidate_ids': [],
        })

    for row in sorted(rows, key=lambda value: value['task_id']):
        task_id = row['task_id']
        if task_id in result['tasks']:
            raise ValueError('duplicate task snapshot: ' + task_id)
        state, origin, projection_errors = _display_state(row)
        task = {
            'task_id': task_id, 'health': row['health'], 'errors': row.get('errors', []),
            'projection_errors': projection_errors, 'display_origin': origin,
            'displayed_revision': None if state is None else state['revision'],
            'displayed_updated_at': None if state is None else state['updated_at'],
            'metadata': None if state is None else
                        {key: value for key, value in state.items() if key not in ('items', 'contributions')},
            'item_ids': [], 'session_ids': [], 'resource_candidate_ids': [],
        }
        result['tasks'][task_id] = task
        result['items'][task_id] = {}
        result['contributions'][task_id] = {}
        if state is None:
            continue
        for item in state['items']:
            task['item_ids'].append(item['item_id'])
            result['items'][task_id][item['item_id']] = item
        for edge in state['contributions']:
            session_id = edge['session_id']
            task['session_ids'].append(session_id)
            result['contributions'][task_id][session_id] = edge
            session(session_id)['task_ids'].append(task_id)

    # Reuse existing resource identity: branch/bare ticket/commit are workspace
    # scoped; recognized GitHub URLs use discovery's normalization. This does
    # not confirm task ownership or external state. Preserve distinct sources.
    source_keys = {}
    seen_sessions = set()
    for record in sorted((observed or {}).get('sessions', []), key=lambda value: value['id']):
        session_id = record['id']
        if session_id in seen_sessions:
            raise ValueError('duplicate observed session: ' + session_id)
        seen_sessions.add(session_id)
        entry = session(session_id)
        entry['observation'] = {key: value for key, value in record.items()
                                if key not in ('resource_mentions', 'plan_snapshots')}
        entry['plan_snapshots'] = record.get('plan_snapshots', [])
        for mention in record.get('resource_mentions', []):
            workspace = record['workspace']
            identity = resource_key(mention['kind'], mention['value'], workspace)
            candidate_id = hashlib.sha256(identity.encode('utf-8')).hexdigest()
            candidate = result['resource_candidates'].setdefault(candidate_id, {
                'candidate_id': candidate_id, 'kind': mention['kind'], 'value': mention['value'],
                'key': identity,
                'workspace': workspace if scoped_resource(mention['kind'], mention['value']) else None,
                'association': 'candidate', 'external_status': 'unknown',
                'sources': [], 'related_task_ids': [],
            })
            source = {**mention, 'session_id': session_id, 'workspace': workspace}
            source_key = _json(source)
            keys = source_keys.setdefault(candidate_id, set())
            if source_key not in keys:
                candidate['sources'].append(source)
                keys.add(source_key)
            if candidate_id not in entry['resource_candidate_ids']:
                entry['resource_candidate_ids'].append(candidate_id)
            for task_id in entry['task_ids']:
                # This is discovery via a linked session, NOT a confirmed link.
                if task_id not in candidate['related_task_ids']:
                    candidate['related_task_ids'].append(task_id)
                ids = result['tasks'][task_id]['resource_candidate_ids']
                if candidate_id not in ids:
                    ids.append(candidate_id)
    return result
