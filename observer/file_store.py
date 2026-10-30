"""Small file-backed task transactions. No models, network, or Devin writes.

Cooperating readers/writers serialize only validation and durable commit. A prepared
journal is rolled forward before any read; out-of-protocol edits are preserved and
reported instead of being mistaken for committed state. This is a local store,
not a security boundary against another process with access to its directory.
"""
from contextlib import contextmanager
import copy
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid


MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_TRANSACTION_TASKS = 32
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class StateError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise StateError("invalid_id", "IDs must be 1–128 safe letters, numbers, dots, dashes or underscores")
    return value


def _encode(value, limit=MAX_RECORD_BYTES):
    try:
        data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                           allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise StateError("invalid_json", "Value must be finite, serializable JSON") from exc
    if len(data) > limit:
        raise StateError("size_limit", "JSON exceeds the local state size limit", {"limit_bytes": limit})
    return data


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _decode(data):
    def reject_constant(value):
        raise ValueError("Non-finite JSON number")
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=reject_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise StateError("invalid_json", "File is not valid unambiguous JSON") from exc


def _hash(data):
    return hashlib.sha256(data).hexdigest()


class FileStore:
    def __init__(self, root, validator=None, lock_timeout=2):
        requested = Path(root).expanduser().absolute()
        if requested.is_symlink():
            raise StateError("unsafe_path", "State root must not be a symbolic link")
        # Resolve OS-managed parent aliases (e.g. macOS /var), not child store paths.
        self.root = requested.parent.resolve() / requested.name
        self.validator = validator
        if not isinstance(lock_timeout, (int, float)) or not math.isfinite(lock_timeout) or lock_timeout < 0:
            raise StateError("invalid_timeout", "Lock timeout must be finite and nonnegative")
        self.lock_timeout = lock_timeout
        self._mkdir(self.root)
        for name in ("tasks", "reports", ".transactions"):
            self._mkdir(self.root / name)

    def _safe(self, path):
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise StateError("unsafe_path", "Path escaped the state root") from exc
        current = self.root
        parts = (None, *relative.parts)
        for index, part in enumerate(parts):
            if part is not None:
                current /= part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(mode):
                raise StateError("unsafe_path", "Symbolic links are not allowed inside the state store", {"path": str(current)})
            if index < len(parts) - 1 and not stat.S_ISDIR(mode):
                raise StateError("unsafe_path", "A parent state path is not a directory", {"path": str(current)})

    def _mkdir(self, path):
        self._safe(path)
        missing = []
        current = path
        while not current.exists():
            missing.append(current)
            current = current.parent
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            for created in reversed(missing):
                self._sync_dir(created.parent)
        except OSError as exc:
            raise StateError("storage_io", "Could not create state directory", {"path": str(path)}) from exc
        self._safe(path)

    def _read_bytes(self, path, limit=MAX_RECORD_BYTES):
        self._safe(path)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StateError("storage_io", "Could not read state file", {"path": str(path)}) from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise StateError("unsafe_path", "State files must be ordinary, unlinked files", {"path": str(path)})
            if info.st_size > limit:
                raise StateError("size_limit", "State file exceeds size limit", {"path": str(path), "limit_bytes": limit})
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise StateError("size_limit", "State file exceeds size limit", {"path": str(path)})
            return data
        finally:
            os.close(fd)

    @staticmethod
    def _sync_dir(path):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _atomic(self, path, data):
        self._mkdir(path.parent)
        self._safe(path)
        temp = path.parent / ("." + path.name + "." + uuid.uuid4().hex + ".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
            self._safe(path)
            os.replace(temp, path)
            self._sync_dir(path.parent)
        finally:
            os.close(fd)
            if temp.exists():
                temp.unlink()

    @contextmanager
    def _locked(self):
        path = self.root / ".lock"
        self._safe(path)
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        except OSError as exc:
            raise StateError("storage_io", "Cannot open state commit lock") from exc
        acquired = False
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise StateError("unsafe_path", "Commit lock must be an ordinary file")
            deadline = time.monotonic() + self.lock_timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if time.monotonic() >= deadline:
                        raise StateError("busy", "Another local state commit is busy; retry later") from exc
                    time.sleep(min(.02, max(0, deadline - time.monotonic())))
            yield
        except OSError as exc:
            raise StateError("storage_io", "State file operation failed", {"reason": str(exc)}) from exc
        finally:
            if acquired:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _validate(self, state, task_id=None):
        if not isinstance(state, dict) or type(state.get("revision")) is not int or not 1 <= state["revision"] <= 2**63 - 1:
            raise StateError("invalid_state", "Task state needs a positive integer revision")
        if task_id is not None and "task_id" in state and state["task_id"] != task_id:
            raise StateError("invalid_state", "State task ID does not match its storage directory", {"task_id": task_id})
        _encode(state, MAX_STATE_BYTES)
        if self.validator:
            try:
                self.validator(state)
            except StateError:
                raise
            except (ValueError, TypeError, KeyError) as exc:
                raise StateError("invalid_state", str(exc)) from exc

    def _state_path(self, task_id):
        return self.root / "tasks" / _id(task_id) / "state.json"

    def _history_path(self, task_id, revision):
        return self.root / "tasks" / _id(task_id) / "history" / (f"{revision:020d}.json")

    def _record_path(self, directory, report_id):
        return self.root / directory / (_hash(report_id.encode("utf-8")) + ".json")

    def _history_paths(self, task_id):
        directory = self.root / "tasks" / _id(task_id) / "history"
        self._safe(directory)
        if not directory.exists():
            return []
        if not directory.is_dir():
            raise StateError("invalid_history", "History path is not a directory", {"path": str(directory)})
        paths = sorted(directory.glob("*.json"))
        for revision, path in enumerate(paths, 1):
            if not re.fullmatch(r"[0-9]{20}\.json", path.name) or int(path.stem) != revision:
                raise StateError("invalid_history", "Unexpected history filename", {"path": str(path)})
        return paths

    def _history_state(self, task_id, path):
        value = _decode(self._read_bytes(path, MAX_STATE_BYTES))
        self._validate(value, task_id)
        if value["revision"] != int(path.stem):
            raise StateError("invalid_history", "History content does not match its revision", {"path": str(path)})
        return value

    def _history(self, task_id):
        return [self._history_state(task_id, path) for path in self._history_paths(task_id)]

    def _last_good(self, task_id):
        # Ordinary reads must not deserialize every prior full state snapshot.
        paths = self._history_paths(task_id)
        return self._history_state(task_id, paths[-1]) if paths else None

    @staticmethod
    def _problem(exc):
        return {"code": exc.code, "message": str(exc), "details": exc.details}

    def _snapshot(self, task_id, blocked):
        task_id = _id(task_id)
        snapshot = {"task_id": task_id, "state": None, "health": "ok", "errors": [], "last_good": None,
                    "content_digest": None}
        if task_id in blocked:
            snapshot.update(health="unavailable", errors=blocked[task_id]["errors"], last_good=blocked[task_id]["before"])
            return snapshot
        try:
            last_good = self._last_good(task_id)
            snapshot["last_good"] = last_good
            raw = self._read_bytes(self._state_path(task_id), MAX_STATE_BYTES)
            snapshot["content_digest"] = _hash(raw) if raw is not None else None
            if raw is None:
                if last_good:
                    raise StateError("external_edit", "Committed state file was removed; restore via a reviewed recovery")
                return snapshot
            state = _decode(raw)
            self._validate(state, task_id)
            if not last_good or raw != _encode(last_good, MAX_STATE_BYTES):
                snapshot.update(state=state, health="external_edit", errors=[{
                    "code": "external_edit", "message": "File differs from committed history; it has not been adopted as current", "details": {}}])
            else:
                snapshot["state"] = state
        except StateError as exc:
            snapshot["health"] = "external_edit" if exc.code == "external_edit" else "invalid"
            snapshot["errors"].append(self._problem(exc))
        return snapshot

    def _receipt(self, journal):
        return {"schema_version": 1, "report_id": journal["report_id"],
                "request_digest": journal["request_digest"], "tasks": journal["after"]}

    def _validate_journal(self, journal, path):
        if not isinstance(journal, dict) or journal.get("schema_version") != 1:
            raise StateError("invalid_journal", "Unsupported transaction journal", {"path": str(path)})
        _id(journal.get("report_id"))
        if self._record_path(".transactions", journal["report_id"]) != path:
            raise StateError("invalid_journal", "Transaction filename does not match report ID")
        if not isinstance(journal.get("request_digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", journal["request_digest"]):
            raise StateError("invalid_journal", "Invalid transaction digest")
        before, after = journal.get("before"), journal.get("after")
        if not isinstance(before, dict) or not isinstance(after, dict) or not 1 <= len(after) <= MAX_TRANSACTION_TASKS or before.keys() != after.keys():
            raise StateError("invalid_journal", "Invalid transaction task set")
        for task_id, value in after.items():
            _id(task_id)
            self._validate(value, task_id)
            prior = before[task_id]
            if prior is not None:
                self._validate(prior, task_id)
            if value["revision"] != (prior["revision"] + 1 if prior else 1):
                raise StateError("invalid_journal", "Invalid journal revision transition")

    def _preflight(self, journal):
        for task_id, after in journal["after"].items():
            before = journal["before"][task_id]
            prior_bytes = _encode(before, MAX_STATE_BYTES) if before is not None else None
            if before is not None and self._read_bytes(self._history_path(task_id, before["revision"]), MAX_STATE_BYTES) != prior_bytes:
                raise StateError("recovery_conflict", "Previous history conflicts with pending commit; original preserved", {"task_id": task_id})
            current = self._read_bytes(self._state_path(task_id), MAX_STATE_BYTES)
            if current not in (prior_bytes, _encode(after, MAX_STATE_BYTES)):
                raise StateError("recovery_conflict", "External edit conflicts with pending commit; original file preserved", {"task_id": task_id})
            history_bytes = self._read_bytes(self._history_path(task_id, after["revision"]), MAX_STATE_BYTES)
            if history_bytes not in (None, _encode(after, MAX_STATE_BYTES)):
                raise StateError("recovery_conflict", "History conflicts with pending commit; original file preserved", {"task_id": task_id})
        receipt = self._read_bytes(self._record_path("reports", journal["report_id"]))
        if receipt not in (None, _encode(self._receipt(journal))):
            raise StateError("recovery_conflict", "Receipt conflicts with pending commit; original file preserved")

    def _finish(self, journal, path):
        self._preflight(journal)
        for task_id, after in sorted(journal["after"].items()):
            data = _encode(after, MAX_STATE_BYTES)
            self._atomic(self._history_path(task_id, after["revision"]), data)
            self._atomic(self._state_path(task_id), data)
        self._atomic(self._record_path("reports", journal["report_id"]), _encode(self._receipt(journal)))
        path.unlink()
        self._sync_dir(path.parent)

    def _recover(self):
        directory = self.root / ".transactions"
        self._safe(directory)
        blocked = {}
        for path in sorted(directory.glob("*.json")):
            journal = _decode(self._read_bytes(path))
            self._validate_journal(journal, path)
            overlap = set(journal["after"]) & blocked.keys()
            if overlap:
                raise StateError("invalid_journal", "Overlapping unfinished transactions need review")
            try:
                self._finish(journal, path)
            except (StateError, OSError) as exc:
                if isinstance(exc, OSError):
                    exc = StateError("storage_io", "Transaction recovery could not write files", {"reason": str(exc)})
                for task_id, before in journal["before"].items():
                    blocked[task_id] = {"before": before, "errors": [self._problem(exc)]}
        return blocked

    def read(self, task_id):
        _id(task_id)
        with self._locked():
            return self._snapshot(task_id, self._recover())

    def list(self):
        with self._locked():
            blocked = self._recover()
            directory = self.root / "tasks"
            self._safe(directory)
            ids = set(blocked)
            for path in directory.iterdir():
                if _ID.fullmatch(path.name):
                    ids.add(path.name)
            return [self._snapshot(task_id, blocked) for task_id in sorted(ids)]

    def history(self, task_id):
        _id(task_id)
        with self._locked():
            blocked = self._recover()
            history = self._history(task_id)
            if task_id in blocked:
                before = blocked[task_id]["before"]
                history = [state for state in history if before and state["revision"] <= before["revision"]]
            return history

    def recover(self, task_id, expected_digest):
        """Explicitly back up a raw edit and restore the last committed state.

        The caller must first inspect diagnostics and supply the observed byte digest.
        Pending multi-task journals and damaged history require separate review.
        """
        _id(task_id)
        if expected_digest is not None and (not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)):
            raise StateError("invalid_digest", "Recovery requires the observed content digest")
        with self._locked():
            blocked = self._recover()
            snapshot = self._snapshot(task_id, blocked)
            if task_id in blocked or snapshot["last_good"] is None:
                raise StateError("recovery_unavailable", "No unambiguous committed state can be restored", {"current": snapshot})
            raw = self._read_bytes(self._state_path(task_id), MAX_STATE_BYTES)
            digest = _hash(raw) if raw is not None else None
            if digest != expected_digest:
                raise StateError("revision_conflict", "Raw file changed since recovery was reviewed", {"current": snapshot})
            if snapshot["health"] == "ok":
                return {"task_id": task_id, "restored": False, "backup": None, "state": snapshot["state"]}
            recovery_id = uuid.uuid4().hex
            base = self.root / "recovery" / task_id
            backup = base / (recovery_id + ".raw")
            if raw is not None:
                self._atomic(backup, raw)
            self._atomic(base / (recovery_id + ".json"), _encode({
                "schema_version": 1, "task_id": task_id, "raw_digest": digest,
                "raw_missing": raw is None, "restored_revision": snapshot["last_good"]["revision"],
                "backup": str(backup.relative_to(self.root)) if raw is not None else None,
            }))
            self._atomic(self._state_path(task_id), _encode(snapshot["last_good"], MAX_STATE_BYTES))
            return {"task_id": task_id, "restored": True,
                    "backup": str(backup) if raw is not None else None, "state": snapshot["last_good"]}

    def transact(self, report_id, request, expected_revisions, transform):
        """Apply a short deterministic transform after CAS; never call a model here.

        ``request`` must include the complete domain payload. The store includes
        expected revisions in its digest, making reuse with changed intent an error.
        The transform receives copies of current states and returns changed states.
        Unchanged read dependencies are compared but are not written or incremented.
        """
        _id(report_id)
        if not isinstance(request, dict):
            raise StateError("invalid_request", "Transaction request must be a JSON object")
        if not isinstance(expected_revisions, dict) or not 1 <= len(expected_revisions) <= MAX_TRANSACTION_TASKS:
            raise StateError("invalid_request", "Transaction needs 1–32 task revisions")
        for task_id, revision in expected_revisions.items():
            _id(task_id)
            if revision is not None and (type(revision) is not int or revision < 1):
                raise StateError("invalid_revision", "Expected revision must be positive or null for a new task")
        digest = _hash(_encode({"request": request, "expected_revisions": expected_revisions}))
        with self._locked():
            blocked = self._recover()
            receipt_path = self._record_path("reports", report_id)
            receipt_bytes = self._read_bytes(receipt_path)
            if receipt_bytes is not None:
                receipt = _decode(receipt_bytes)
                if not isinstance(receipt, dict) or receipt.get("schema_version") != 1 or receipt.get("report_id") != report_id or not isinstance(receipt.get("tasks"), dict):
                    raise StateError("invalid_receipt", "Report receipt is invalid; original preserved")
                if receipt.get("request_digest") != digest:
                    raise StateError("report_id_reused", "This report ID already committed a different request")
                for task_id, value in receipt["tasks"].items():
                    _id(task_id)
                    self._validate(value, task_id)
                return {"report_id": report_id, "replayed": True, "tasks": receipt["tasks"]}
            pending_path = self._record_path(".transactions", report_id)
            pending_bytes = self._read_bytes(pending_path)
            if pending_bytes is not None:
                pending = _decode(pending_bytes)
                if pending["request_digest"] != digest:
                    raise StateError("report_id_reused", "This report ID belongs to an unfinished different request")
                raise StateError("state_unavailable", "This report has an unfinished commit requiring recovery", {"tasks": sorted(pending["after"])})
            snapshots = {task_id: self._snapshot(task_id, blocked) for task_id in expected_revisions}
            unhealthy = {key: value for key, value in snapshots.items() if value["health"] != "ok"}
            if unhealthy:
                raise StateError("state_unavailable", "One or more task files need review; original files preserved", {"current": snapshots})
            states = {key: value["state"] for key, value in snapshots.items()}
            if any((states[key]["revision"] if states[key] else None) != revision for key, revision in expected_revisions.items()):
                raise StateError("revision_conflict", "State changed since it was read; read and decide again", {"current": snapshots})
            after = transform(copy.deepcopy(states))
            if not isinstance(after, dict) or not after or not after.keys() <= states.keys():
                raise StateError("invalid_transaction", "Transform must return a nonempty subset of the declared task set")
            for task_id, value in after.items():
                self._validate(value, task_id)
                if value["revision"] != (states[task_id]["revision"] + 1 if states[task_id] else 1):
                    raise StateError("invalid_revision", "A commit must advance each target revision exactly once")
            journal = {"schema_version": 1, "report_id": report_id, "request_digest": digest,
                       "before": {key: states[key] for key in after}, "after": after}
            journal_data = _encode(journal)
            path = self._record_path(".transactions", report_id)
            self._atomic(path, journal_data)
            self._finish(journal, path)
            return {"report_id": report_id, "replayed": False, "tasks": after}
