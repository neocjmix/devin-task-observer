"""Bounded, read-only metrics from the observed Devin Local schema 17.

Native ``input_tokens`` excludes cache reads; ATIF-v1.7/ACP total input includes
them. This mapping was cross-checked against existing personal synthetic exports.
Nonzero cache-creation metrics and other source versions remain unsupported.

Totals describe retained message metrics, including possible inherited fork
history. They are neither a billing estimate nor a complete lifetime ledger.
Nothing is allocated to tasks. This optional reader never attaches to an agent.
"""
from datetime import datetime, timezone
import copy
import json
import math
from pathlib import Path
import sqlite3
import time


MAX_TOKENS = 2 ** 53 - 1
MAX_SESSIONS = 256
MAX_NODES = 50_000
MAX_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_SESSION_BYTES = 64 * 1024 * 1024
MAX_READ_SECONDS = 3
SOURCE = 'devin_local_sqlite_v17'


class SessionUsageUnavailable(ValueError):
    """Optional metrics acquisition failed; existing observed state is untouched."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _scope(config):
    if not isinstance(config, dict):
        raise SessionUsageUnavailable('invalid_configuration')
    source, workspaces = config.get('source'), config.get('workspaces')
    if (not isinstance(source, str) or not Path(source).is_absolute() or
            not isinstance(workspaces, list) or not workspaces or
            any(not isinstance(w, str) or not Path(w).is_absolute() for w in workspaces)):
        raise SessionUsageUnavailable('invalid_configuration')
    return source, tuple(sorted(set(workspaces)))


def _tokens(value):
    return type(value) is int and 0 <= value <= MAX_TOKENS


def _iso_seconds(value):
    if type(value) is not int or value < 0:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _empty(session_id, workspace, activity, measured_at):
    return {'session_id': session_id, 'workspace': workspace,
            'status': 'unavailable', 'reason': 'metrics_missing',
            'input_total': None, 'input_new': None, 'input_cached': None, 'output': None,
            'measured_at': measured_at, 'source_activity_at': _iso_seconds(activity),
            'source': SOURCE, 'coverage': 'retained_message_metrics',
            'lifetime_complete': False, 'may_include_inherited_history': True,
            'billing_usage': False, 'message_count': 0, 'metric_node_count': 0,
            'duplicate_node_count': 0, 'evidence': []}


def _session(db, entry, measured_at, deadline):
    sid, workspace, activity = entry
    result = _empty(sid, workspace, activity, measured_at)
    # Bound allocation before SQLite returns a potentially large transcript body.
    rows = db.execute('''SELECT node_id, length(CAST(chat_message AS BLOB)),
        CASE WHEN length(CAST(chat_message AS BLOB)) <= ? THEN chat_message END
        FROM message_nodes WHERE session_id=? ORDER BY node_id LIMIT ?''',
        (MAX_MESSAGE_BYTES, sid, MAX_NODES + 1))
    records, seen_nodes, total_bytes, metric_nodes = {}, set(), 0, 0
    try:
        for node_id, size, raw in rows:
            if time.monotonic() >= deadline:
                raise SessionUsageUnavailable('read_budget_exceeded')
            if len(seen_nodes) >= MAX_NODES:
                raise SessionUsageUnavailable('node_limit_exceeded')
            if type(node_id) is not int or node_id in seen_nodes:
                raise SessionUsageUnavailable('invalid_node_identity')
            seen_nodes.add(node_id)
            if type(size) is not int or size > MAX_MESSAGE_BYTES or not isinstance(raw, str):
                raise SessionUsageUnavailable('message_size_or_type_unsupported')
            total_bytes += size
            if total_bytes > MAX_SESSION_BYTES:
                raise SessionUsageUnavailable('session_size_exceeded')
            try:
                message = json.loads(raw)
            except (ValueError, RecursionError):
                raise SessionUsageUnavailable('malformed_message') from None
            if not isinstance(message, dict):
                raise SessionUsageUnavailable('malformed_message')
            metadata = message.get('metadata')
            if metadata is None:
                continue
            if not isinstance(metadata, dict):
                raise SessionUsageUnavailable('unsupported_metadata')
            metrics = metadata.get('metrics')
            if metrics is None:
                continue
            if message.get('role') != 'assistant' or not isinstance(metrics, dict):
                raise SessionUsageUnavailable('unsupported_metrics')
            mid = message.get('message_id')
            if not isinstance(mid, str) or not mid or len(mid) > 512:
                raise SessionUsageUnavailable('invalid_message_identity')
            values = tuple(metrics.get(k) for k in ('input_tokens', 'cache_read_tokens', 'output_tokens'))
            if not all(_tokens(v) for v in values):
                raise SessionUsageUnavailable('invalid_token_count')
            creation = metrics.get('cache_creation_tokens')
            if creation is not None and (type(creation) is not int or creation != 0):
                raise SessionUsageUnavailable('cache_creation_unsupported')
            new, cached, output = values
            total = new + cached
            if total > MAX_TOKENS:
                raise SessionUsageUnavailable('token_limit_exceeded')
            context = metrics.get('context_input_tokens')
            if context is not None and (not _tokens(context) or context != total):
                raise SessionUsageUnavailable('inconsistent_context_input')
            metric_nodes += 1
            if mid in records:
                if records[mid]['values'] != values:
                    raise SessionUsageUnavailable('conflicting_message_metrics')
                records[mid]['node_ids'].append(node_id)
            else:
                records[mid] = {'values': values, 'node_ids': [node_id]}
    except SessionUsageUnavailable as error:
        result['reason'] = error.code
        return result
    if not records:
        return result
    new, cached, output = (sum(record['values'][i] for record in records.values()) for i in range(3))
    if any(v > MAX_TOKENS for v in (new, cached, new + cached, output)):
        result['reason'] = 'token_limit_exceeded'
        return result
    result.update(status='available', reason=None, input_total=new + cached,
                  input_new=new, input_cached=cached, output=output,
                  message_count=len(records), metric_node_count=metric_nodes,
                  duplicate_node_count=metric_nodes - len(records),
                  evidence=[{'message_id': mid, 'node_ids': record['node_ids']}
                            for mid, record in sorted(records.items())])
    return result


def load_session_usage(config, *, measured_at=None):
    """Return ``{session_id: usage}`` for exact configured workspaces only.

    A source-wide failure raises SessionUsageUnavailable with a non-sensitive
    code; never interpret this as zero usage. A bad individual session returns
    unavailable/null metrics without hiding other valid sessions. The caller
    owns previous-value/freshness UI policy. ``measured_at`` is the observer's
    read time, not a generation timestamp, and can be fixed in synthetic tests.

    Read-only WAL access can still coordinate via SQLite shared memory and hold
    short read locks, as documented for the existing Local collector.
    """
    source, workspaces = _scope(config)
    if measured_at is None:
        measured_at = datetime.now(timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(measured_at) if isinstance(measured_at, str) else None
        if parsed is None or parsed.utcoffset() is None:
            raise ValueError
    except ValueError:
        raise SessionUsageUnavailable('invalid_measurement_time') from None
    deadline = time.monotonic() + MAX_READ_SECONDS
    db = None
    try:
        db = sqlite3.connect(Path(source).as_uri() + '?mode=ro', uri=True, timeout=.25)
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.execute('BEGIN')
        version = db.execute('SELECT max(version) FROM refinery_schema_history').fetchone()[0]
        if version != 17:
            raise SessionUsageUnavailable('unsupported_source_schema')
        entries, ids = [], set()
        for workspace in sorted(set(workspaces)):
            for sid, activity in db.execute('''SELECT id,last_activity_at FROM sessions
                    WHERE working_directory=? ORDER BY id LIMIT ?''', (workspace, MAX_SESSIONS + 1)):
                if not isinstance(sid, str) or not sid or len(sid) > 512 or sid in ids:
                    raise SessionUsageUnavailable('invalid_session_identity')
                if len(entries) >= MAX_SESSIONS:
                    raise SessionUsageUnavailable('session_limit_exceeded')
                ids.add(sid)
                entries.append((sid, workspace, activity))
        result = {}
        for entry in entries:
            result[entry[0]] = _session(db, entry, measured_at, deadline)
        db.rollback()
        return result
    except sqlite3.Error:
        raise SessionUsageUnavailable('source_read_failed') from None
    finally:
        if db is not None:
            db.close()


class UsageCache:
    """One scope's optional usage snapshot, for the acquisition worker only.

    Normal UI polls reuse the snapshot for 15 seconds, including after a failed
    read. Only explicit manual refresh should request ``force=True``. Cached
    per-session measured_at values stay unchanged; a failed later attempt is
    exposed separately and never makes old metrics look newly measured.

    A source/workspace change clears the old snapshot before reading, even when
    the new read fails. There is no on-disk cache, watcher, background thread or
    cross-scope registry. The caller keeps this instance on its existing worker.
    """
    def __init__(self, *, interval=15, monotonic=None, timestamp=None, loader=None):
        if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
            raise ValueError('usage interval must be positive and finite')
        self.interval = interval
        self._clock = monotonic or time.monotonic
        self._timestamp = timestamp or (lambda: datetime.now(timezone.utc).isoformat())
        self._loader = loader or load_session_usage
        self._key, self._next_read = None, None
        self._value = {'sessions': {}, 'error': None, 'stale': False, 'last_attempt': None}

    def read(self, config, *, force=False):
        try:
            key = _scope(config)
        except SessionUsageUnavailable as error:
            self._key, self._next_read = None, None
            self._value = {'sessions': {}, 'error': error.code, 'stale': True,
                           'last_attempt': self._timestamp()}
            return copy.deepcopy(self._value)
        if key != self._key:
            self._key, self._next_read = key, None
            self._value = {'sessions': {}, 'error': None, 'stale': False, 'last_attempt': None}
        if not force and self._next_read is not None and self._clock() < self._next_read:
            return copy.deepcopy(self._value)
        attempted = self._timestamp()
        try:
            sessions = self._loader(config, measured_at=attempted)
            self._value = {'sessions': copy.deepcopy(sessions), 'error': None,
                           'stale': False, 'last_attempt': attempted}
        except SessionUsageUnavailable as error:
            self._value = {'sessions': self._value['sessions'], 'error': error.code,
                           'stale': True, 'last_attempt': attempted}
        self._next_read = self._clock() + self.interval
        return copy.deepcopy(self._value)
