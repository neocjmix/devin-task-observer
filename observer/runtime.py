"""Zero-model, browser-free collection service for the menu-bar application."""
import argparse
import copy
import fcntl
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

from .store import mark_source_unavailable, open_store

REPOSITORY = Path(__file__).resolve().parent.parent


def service_running(config):
    """Probe the shared ownership lock without creating files or sending signals."""
    try:
        fd = os.open(str(Path(config['store']).resolve()) + '.service.lock', os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)


def collector_status(config):
    """Read persisted health without creating or migrating an observation database."""
    result = {'last_success': None, 'last_attempt': None, 'last_error': 'not_collected'}
    try:
        db = sqlite3.connect(Path(config['store']).as_uri() + '?mode=ro', uri=True, timeout=.5)
        try:
            db.execute('PRAGMA query_only=ON')
            row = db.execute('SELECT last_success,last_attempt,last_error FROM collector_status WHERE singleton=1').fetchone()
            if row:
                result.update(zip(('last_success', 'last_attempt', 'last_error'), row))
        finally:
            db.close()
    except (OSError, sqlite3.Error, ValueError):
        result['last_error'] = 'collector_unavailable_or_incompatible'
    return result


class CollectorRuntime:
    """Own one collector process at a time; source failures retry with bounded delay.

    The legacy web service uses the same service lock. Every child also takes the
    existing collection lock. No HTTP listener, semantic policy or model adapter
    is loaded here. Child termination rolls back any open SQLite transaction.
    """
    def __init__(self, config, interval=5, max_delay=60, timeout=30):
        from .app import validate_config
        self.config = validate_config(copy.deepcopy(config))
        if interval < .1 or max_delay < interval or timeout <= 0:
            raise ValueError('수집 주기·재시도 상한·timeout이 올바르지 않습니다.')
        self.interval, self.max_delay, self.timeout = interval, max_delay, timeout
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._guard = threading.Lock()
        self._state_guard = threading.Lock()
        self._thread = None
        self._process = None
        self._fd = None
        self._result = {'ok': False, 'stage': 'not_started'}
        self._delay = interval

    def acquire(self):
        if self._fd is not None:
            return
        store = Path(self.config['store']).resolve()
        store.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(store) + '.service.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise ValueError('이 저장소의 Observer가 이미 실행 중입니다. 기존 실행을 종료한 뒤 다시 실행하세요.') from None
        try:
            db = open_store(store, Path(self.config['source']))
            db.close()
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def start(self):
        if self._thread is not None:
            return
        self.acquire()
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, name='observer-collector', daemon=True)
        self._thread.start()

    def request_refresh(self):
        self._wake.set()

    def _record_failure(self, reason):
        try:
            db = open_store(Path(self.config['store']), Path(self.config['source']))
            try:
                mark_source_unavailable(db, [Path(w) for w in self.config['workspaces']], reason)
            finally:
                db.close()
        except (OSError, ValueError, sqlite3.Error):
            pass

    def refresh_once(self):
        if self._fd is None:
            raise ValueError('수집기 서비스 락을 먼저 확보해야 합니다.')
        if not self._guard.acquire(blocking=False):
            return {'ok': False, 'stage': 'busy'}
        try:
            if self._stop.is_set():
                return {'ok': False, 'stage': 'stopped'}
            command = [sys.executable, '-m', 'observer', '--source', self.config['source'], '--store', self.config['store']]
            for workspace in self.config['workspaces']:
                command += ['--workspace', workspace]
            environment = os.environ.copy()
            if self.config.get('git'):
                environment['DEVIN_OBSERVER_GIT'] = self.config['git']
            result = None
            try:
                process = subprocess.Popen(command, cwd=REPOSITORY, env=environment,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                with self._state_guard:
                    self._process = process
                    if self._stop.is_set():
                        process.terminate()
                try:
                    stdout, _ = process.communicate(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    result = {'ok': False, 'stage': 'refresh_failed', 'reason': 'refresh_timeout'}
                if result is None:
                    if len(stdout) > 65536:
                        raise ValueError('collector output exceeded limit')
                    result = json.loads(stdout)
                    if not isinstance(result, dict) or type(result.get('ok')) is not bool:
                        raise ValueError('invalid collector result')
                    if process.returncode != 0 and result['ok']:
                        raise ValueError('collector exit code conflicts with success')
            except (OSError, ValueError, UnicodeError):
                result = {'ok': False, 'stage': 'refresh_failed', 'reason': 'refresh_process_failed'}
            finally:
                with self._state_guard:
                    self._process = None
            if self._stop.is_set():
                result = {'ok': False, 'stage': 'stopped'}
            elif result.get('stage') == 'refresh_failed':
                self._record_failure(result['reason'])
            with self._state_guard:
                self._result = result
            return copy.deepcopy(result)
        finally:
            self._guard.release()

    def _poll(self):
        while not self._stop.is_set():
            # Clear before work so a refresh requested during collection is kept.
            self._wake.clear()
            result = self.refresh_once()
            self._delay = self.interval if result.get('ok') else min(self.max_delay, self._delay * 2)
            self._wake.wait(self._delay)

    def snapshot(self):
        with self._state_guard:
            result = copy.deepcopy(self._result)
        return {'running': self._fd is not None and not self._stop.is_set(),
                'refresh': result, 'next_retry_seconds': self._delay,
                'collector': collector_status(self.config)}

    def stop(self):
        self._stop.set()
        self._wake.set()
        with self._state_guard:
            if self._process is not None and self._process.poll() is None:
                try:
                    self._process.terminate()
                except ProcessLookupError:
                    pass
        if self._thread is not None:
            self._thread.join(timeout=self.timeout + 2)
            if self._thread.is_alive():
                raise RuntimeError('수집기 종료를 확인하지 못했습니다. 서비스 락을 유지합니다.')
            self._thread = None
        # Also wait for a direct refresh_once invocation before releasing ownership.
        with self._guard:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None


def main(argv=None):
    from .app import DEFAULT_CONFIG, load_config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--once', action='store_true', help='한 번 수집하고 JSON 결과 출력')
    args = parser.parse_args(argv)
    runtime = None
    old_handlers = {}
    try:
        runtime = CollectorRuntime(load_config(args.config))
        if args.once:
            runtime.acquire()
            result = runtime.refresh_once()
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['ok'] else 2
        stopped = threading.Event()
        for name in (signal.SIGTERM, signal.SIGINT):
            old_handlers[name] = signal.signal(name, lambda *_: stopped.set())
        runtime.start()
        print('Observer collector running (no browser, no model calls).', flush=True)
        stopped.wait()
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print('Observer 수집 안내: ' + str(error), file=sys.stderr)
        return 2
    finally:
        if runtime is not None:
            runtime.stop()
        for name, handler in old_handlers.items():
            signal.signal(name, handler)


if __name__ == '__main__':
    raise SystemExit(main())
