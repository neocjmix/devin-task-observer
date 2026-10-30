"""Thin optional macOS menu shell; all state and collection logic is plain Python."""
import argparse
import copy
import json
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from .cooperative import CooperativeState
from .file_store import StateError
from .runtime import CollectorRuntime, collector_status, service_running

DEFAULT_ROOT = Path.home() / 'Library/Application Support/Devin Task Observer/state'


def _text(value, limit=180):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, allow_nan=False)
    value = ' '.join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + '…'


def _time(value):
    if value is None:
        return '없음'
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1e9).astimezone().strftime('%m-%d %H:%M:%S')
        return datetime.fromisoformat(value).astimezone().strftime('%m-%d %H:%M:%S')
    except (ValueError, TypeError, OverflowError, OSError):
        return _text(value, 60)


def _actor(actor):
    return actor.get('session_id') or ('이관 기록' if actor.get('kind') == 'legacy' else '사용자')


def snapshot(root, config, runtime=None, config_path=None):
    """Build a tolerant projection. Never present an external edit as valid current state."""
    result = {'root': str(Path(root).absolute()), 'tasks': [], 'errors': [],
              'runtime': runtime.snapshot() if runtime else {'running': service_running(config), 'collector': collector_status(config)},
              'observations': None, 'model_calls': 0}
    if config_path is not None:
        from .state_observations import observations
        from .app import load_config
        try:
            if load_config(config_path) != config:
                result['errors'].append({'code': 'configuration_changed', 'message': '설정이 변경됐습니다. Observer를 재시작하세요.'})
            else:
                result['observations'] = observations(config_path)
        except ValueError as error:
            result['errors'].append({'code': 'configuration_error', 'message': str(error)})
    try:
        from .integration import session_activity
        rows = CooperativeState(root, lock_timeout=.25).list()
        for row in rows:
            state = row['state'] if row['health'] == 'ok' else row.get('last_good')
            result['tasks'].append({'task_id': row['task_id'], 'health': row['health'],
                                    'errors': row['errors'], 'state': state,
                                    'session_activity': {c['session_id']: session_activity(root, c['session_id'])
                                                         for c in (state or {}).get('contributions', [])},
                                    'display_origin': 'current' if row['health'] == 'ok' else 'last_good' if state else 'unavailable'})
    except (StateError, OSError, ValueError) as error:
        result['errors'].append({'code': getattr(error, 'code', 'state_unavailable'), 'message': str(error)})
    return result


def menu_model(value):
    """Framework-independent rows are shared by tests, snapshots and the native menu."""
    runtime = value['runtime']
    collector = runtime.get('collector') or {}
    error = collector.get('last_error')
    stale_count = runtime.get('refresh', {}).get('stale_sessions', 0)
    troubled = bool(error or stale_count or value['errors'] or any(t['health'] != 'ok' for t in value['tasks']))
    status = '수집 중' if runtime.get('running') else '수집기 정지'
    if error:
        status += ' · 확인 필요'
    rows = [{'title': 'Observer · ' + status},
            {'title': '마지막 성공 수집: ' + _time(collector.get('last_success'))}]
    if error:
        rows.append({'title': '수집 오류: ' + _text(error)})
    if stale_count:
        rows.append({'title': '원본 구조 확인 필요: ' + str(stale_count) + '개 세션'})
    observation = value.get('observations') or {}
    for warning in observation.get('warnings', []):
        rows.append({'title': '관측 경고: ' + _text(warning)})
    observed = {session['id']: session for session in observation.get('sessions', [])}
    rows.append({'title': '태스크 ' + str(len(value['tasks'])) + ' · 보고는 세션 판단 · 조회 0토큰'})
    for problem in value['errors']:
        rows.append({'title': '상태 오류: ' + _text(problem['message'])})
    for task in value['tasks'][:50]:
        state = task['state']
        title = _text(state['title'] if state else task['task_id'], 75)
        title += ' [' + task['task_id'] + ']'
        children = []
        if task['health'] != 'ok':
            title = '⚠ ' + title
            children += [{'title': '파일 건강: ' + task['health']},
                         {'title': '마지막 정상 커밋 표시 (현재 정상 상태가 아님)' if state else '표시 가능한 정상 커밋 없음'}]
            children += [{'title': '오류: ' + _text(e)} for e in task['errors'][:5]]
        if state:
            children += [{'title': 'revision ' + str(state['revision']) + ' · ' + _time(state['updated_at'])}]
            reports = []
            for contribution in state['contributions']:
                session_rows = [
                    {'title': '능동보고 ' + ('ON' if contribution['reporting'] else 'OFF')},
                    {'title': '마지막 보고: ' + _time(contribution['last_report_at'])},
                    {'title': 'ON은 세션을 깨우거나 매 턴 보고를 보장하지 않음'}]
                activity = task.get('session_activity', {}).get(contribution['session_id'], {})
                if activity.get('health') == 'ok':
                    session_rows += [{'title': '스킬 연결 활동: ' + _time(activity.get('last_seen'))},
                                     {'title': '마지막 훅: ' + _text(activity.get('last_event', 'unknown'))},
                                     {'title': '보고 지침 재전달 필요' if activity.get('needs_context') else '보고 지침 전달 기록 있음 · 이행 보장 아님'}]
                elif activity.get('health') == 'attention':
                    session_rows.append({'title': '스킬 연결 기록 확인 필요 · ' + _text(activity.get('error'))})
                facts = observed.get(contribution['session_id'])
                if facts:
                    session_rows += [{'title': '원본 관측: ' + facts['status'] + ' · 변경 ' + str(facts['observation_count'])},
                                     {'title': '작업 공간: ' + _text(facts['workspace'])}]
                    for index, resource in enumerate(facts['resource_mentions'][:15], 1):
                        session_rows.append({'title': '후보 ' + str(index) + ': ' + resource['kind'] + ' · ' + _text(resource['value']), 'children': [
                            {'title': '검증: ' + resource['verification'] + ' · 외부 상태 unknown'},
                            {'title': '근거: ' + _text(resource['source_ref'])},
                            {'title': '태스크 소속을 자동 확정하지 않음'}]})
                    for index, plan in enumerate(facts['plan_snapshots'][:15], 1):
                        session_rows.append({'title': '계획 기록 ' + str(index) + ' · node ' + str(plan['node_id']), 'children': [
                            {'title': '에이전트 보고 · 순서/완료 독립 검증 없음'},
                            {'title': _text(plan['items'], 350)}]})
                    if len(facts['resource_mentions']) > 15 or len(facts['plan_snapshots']) > 15 or facts['resources_truncated'] or facts['plans_truncated']:
                        session_rows.append({'title': '추가 관측은 CLI observations로 조회'})
                else:
                    session_rows.append({'title': '허용 작업 공간에서 아직 관측되지 않은 세션'})
                reports.append({'title': contribution['session_id'], 'children': session_rows})
            children.append({'title': '참여 세션 · ' + str(len(reports)), 'children': reports or [{'title': '연결된 세션 없음'}]})
            active = [i for i in state['items'] if not i['archived']]
            for item in active[:50]:
                item_rows = [{'title': '값: ' + _text(item['value'], 240)},
                             {'title': '보고 상태: ' + _text(item['status'], 100)},
                             {'title': '업데이트: ' + _actor(item['updated_by'])},
                             {'title': '시각: ' + _time(item['updated_at'])},
                             {'title': '명시적 고정: ' + ('ON' if item['pinned'] else 'OFF')},
                             {'title': '검증: ' + ('이관 당시 기록' if item['verification'] == 'legacy_import' else '세션 보고 · 독립 검증 아님')}]
                for index, evidence in enumerate(item['evidence'][:10], 1):
                    item_rows.append({'title': '근거 ' + str(index) + ': ' + evidence['session_id'] + ' · ' + _text(evidence['reference'], 180)})
                if len(item['evidence']) > 10:
                    item_rows.append({'title': '나머지 근거는 상태 파일에서 확인'})
                children.append({'title': ('📌 ' if item['pinned'] else '') + _text(item['title'], 70) + ' [' + item['item_id'] + '] · ' + _text(item['status'], 40), 'children': item_rows})
            if not active:
                children.append({'title': '아직 보고된 추적 항목 없음'})
            if len(active) > 50:
                children.append({'title': f'추적 항목 {len(active) - 50}개 더 있음 · 상태 파일에서 확인'})
            archived = len(state['items']) - len(active)
            if archived:
                children.append({'title': '추적 종료 항목 ' + str(archived) + '개 · 이력 보존'})
            if state['notes']:
                children.append({'title': '사용자 메모', 'children': [
                    {'title': str(index) + '. ' + _text(n['text'], 240)} for index, n in enumerate(state['notes'][:30], 1)]})
            if state['view']['markdown']:
                children.append({'title': 'Persistent View: ' + _text(state['view']['markdown'], 240)})
            legacy = state.get('legacy')
            if legacy:
                imported = [{'title': '원래 설명: ' + _text(legacy.get('description') or '없음')},
                            {'title': '원래 상태: ' + _text(legacy.get('lifecycle') or 'unknown')}]
                view = legacy.get('view') or {}
                sections = view.get('sections') or []
                for section in sections[:30]:
                    section_rows = [{'title': ('📌 ' if item.get('pinned') else '') + str(index) + '. ' + _text(item.get('content', ''), 240)}
                                    for index, item in enumerate(section.get('items', [])[:50], 1)]
                    if len(section.get('items', [])) > 50:
                        section_rows.append({'title': '추가 항목은 상태 파일에 보존'})
                    imported.append({'title': _text(section.get('title', 'View')) + ' [' + str(section.get('id', '')) + ']', 'children': section_rows or [{'title': '빈 섹션'}]})
                for index, resource in enumerate(legacy.get('resources', [])[:30], 1):
                    imported.append({'title': '리소스 ' + str(index) + ': ' + _text(resource, 220)})
                imported.append({'title': '독립 확인 위치 ' + str(len(legacy.get('review_cursors', []))) + '개 보존'})
                imported.append({'title': '이관 당시 스냅샷 · 이후 원본 DB와 자동 동기화하지 않음'})
                children.append({'title': '기존 View·리소스 보존', 'children': imported})
        children.append({'title': '상태 파일을 Finder에서 보기', 'action': 'reveal', 'task_id': task['task_id']})
        rows.append({'title': title, 'children': children})
    if len(value['tasks']) > 50:
        rows.append({'title': '태스크 50개까지 표시 · 나머지는 CLI list로 조회'})
    if not value['tasks'] and not value['errors']:
        rows.append({'title': '아직 등록된 태스크 없음 · 보고 스킬로 태스크 연결'})
    return {'title': '◉!' if troubled else '◉', 'rows': rows}


class MenuController:
    """Keep filesystem locks and collection waits away from the Cocoa main thread."""
    def __init__(self, root, config, runtime, interval=2, config_path=None):
        self.root, self.config, self.runtime, self.interval = root, config, runtime, interval
        self.config_path = config_path
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._guard = threading.Lock()
        self._value = None
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._poll, name='observer-menu-state', daemon=True)
        self._thread.start()

    def _poll(self):
        while not self._stop.is_set():
            self._wake.clear()
            value = menu_model(snapshot(self.root, self.config, self.runtime, self.config_path))
            with self._guard:
                self._value = value
            self._wake.wait(self.interval)

    def latest(self):
        with self._guard:
            return copy.deepcopy(self._value)

    def refresh(self):
        self.runtime.request_refresh()
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)


def run_native(root, config, config_path=None):
    if sys.platform != 'darwin':
        raise ValueError('메뉴 막대 UI는 macOS에서만 실행합니다. collector와 CLI는 별도로 사용할 수 있습니다.')
    try:
        import rumps
    except ImportError:
        raise ValueError('메뉴 막대 의존성이 없습니다. scripts/setup_menu.py로 전용 환경을 설치한 뒤 그 환경의 Python으로 menu를 실행하세요.') from None
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

    runtime = CollectorRuntime(config)
    controller = MenuController(root, config, runtime, config_path=config_path)
    app = rumps.App('Devin Task Observer', title='◉', quit_button=None)
    previous = None
    stopped = threading.Event()

    def quit_app(_=None):
        stopped.set()
        # NSApplication.terminate_ exits the native process rather than unwinding
        # Python's run loop. Complete child shutdown and release ownership first.
        controller.stop()
        runtime.stop()
        rumps.quit_application()

    def make_item(row):
        item = rumps.MenuItem(row['title'])
        if row.get('action') == 'reveal':
            path = Path(root).absolute() / 'tasks' / row['task_id'] / 'state.json'
            item.set_callback(lambda _: subprocess.Popen(['/usr/bin/open', '-R', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for child in row.get('children', []):
            item.add(make_item(child))
        return item

    def update(_):
        nonlocal previous
        if stopped.is_set():
            quit_app()
            return
        value = controller.latest()
        if value is None or value == previous:
            return
        previous = value
        app.title = value['title']
        app.menu.clear()
        for row in value['rows']:
            app.menu.add(make_item(row))
        app.menu.add(None)
        app.menu.add(rumps.MenuItem('지금 수집·상태 새로고침', callback=lambda _: controller.refresh()))
        app.menu.add(rumps.MenuItem('상태 폴더 열기', callback=lambda _: subprocess.Popen(['/usr/bin/open', str(Path(root).absolute())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)))
        app.menu.add(rumps.MenuItem('Observer 종료', callback=quit_app))

    timer = rumps.Timer(update, 1)
    handlers = {}
    try:
        runtime.start()
        controller.start()
        for name in (signal.SIGTERM, signal.SIGINT):
            handlers[name] = signal.signal(name, lambda *_: stopped.set())
        timer.start()
        # Accessory policy keeps the thin shell out of the Dock. The menu itself
        # is a real NSStatusItem; no web view/server or Swift runtime is involved.
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        print('Observer menu running (no browser, no model calls).', flush=True)
        app.run()
    finally:
        timer.stop()
        controller.stop()
        runtime.stop()
        for name, handler in handlers.items():
            signal.signal(name, handler)
    return 0


def main(argv=None):
    from .app import DEFAULT_CONFIG, load_config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--snapshot', action='store_true', help='GUI·수집 없이 같은 메뉴 표현을 JSON으로 확인')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        root = args.root.expanduser().absolute()
        source = Path(config['source']).resolve()
        if root.resolve().is_relative_to(source.parent):
            raise ValueError('보고 상태 폴더는 Devin 원본 폴더 밖에 있어야 합니다.')
        if args.snapshot:
            value = snapshot(root, config, config_path=args.config)
            print(json.dumps({'snapshot': value, 'menu': menu_model(value)}, ensure_ascii=False))
            return 0
        return run_native(root, config, config_path=args.config)
    except (ValueError, OSError, StateError) as error:
        print('Observer 메뉴 안내: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
