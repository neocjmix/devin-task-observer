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
from .paths import DEFAULT_ROOT


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


def _status(value):
    # Only familiar display labels are translated. Custom session states survive.
    return {'completed': '완료', 'passed': '통과', 'pending': '대기',
            'in_progress': '진행 중', 'blocked': '진행 막힘', 'failed': '실패'}.get(value, value)


def _status_icon(value):
    return {'completed': 'checkmark.circle', 'passed': 'checkmark.circle',
            'in_progress': 'circle.lefthalf.filled', 'blocked': 'exclamationmark.circle',
            'failed': 'exclamationmark.circle'}.get(value, 'circle')


def _origin(item):
    if item['updated_by'].get('kind') == 'human':
        return '사용자 변경'
    if item['verification'] == 'legacy_import':
        return '가져온 기록'
    return '세션 보고'


def _session_row(contribution, task, observed):
    sid = contribution['session_id']
    reporting = '자동 보고 켜짐' if contribution['reporting'] else '자동 보고 꺼짐'
    children = [{'title': reporting, 'style': 'heading'},
                {'title': sid, 'style': 'caption'},
                {'title': '최근 보고 ' + _time(contribution['last_report_at']), 'style': 'caption'}]
    activity = task.get('session_activity', {}).get(sid, {})
    if activity.get('health') == 'ok':
        if activity.get('needs_context'):
            children.append({'title': '⚠ 보고 지침 갱신 필요'})
        children += [{'title': '최근 활동 ' + _time(activity.get('last_seen')), 'style': 'caption'},
                     {'title': '연결 기록 · ' + _text(activity.get('last_event', 'unknown')), 'style': 'caption'}]
    elif activity.get('health') == 'attention':
        children.append({'title': '⚠ 세션 연결 확인 필요', 'caption': _text(activity.get('error'))})
    facts = observed.get(sid)
    if facts:
        source_status = {'current': '관측 중', 'stale': '확인 필요'}.get(facts['status'], facts['status'])
        children += [{'title': source_status + ' · 변경 ' + str(facts['observation_count']) + '건'},
                     {'title': _text(facts['workspace']), 'style': 'caption'}, {'separator': True}]
        for resource in facts['resource_mentions'][:15]:
            children.append({'title': '후보 · ' + _text(resource['value'], 90), 'caption': '후보 · 상태 미확인',
                             'kind': 'resource', 'icon': 'link', 'children': [
                {'title': '후보 · 상태 미확인', 'style': 'heading'},
                {'title': _text(resource['value'], 350)},
                {'title': '태스크 연결 미확인'},
                {'title': '근거 · ' + _text(resource['source_ref'])},
                {'title': '분류 · ' + resource['kind'] + ' / ' + resource['verification'], 'style': 'caption'}]})
        for index, plan in enumerate(facts['plan_snapshots'][:15], 1):
            children.append({'title': '계획 기록 ' + str(index), 'icon': 'list.bullet', 'children': [
                {'title': '세션 보고 · 순서·완료 미확인'},
                {'title': _text(plan['items'], 350)},
                {'title': '기록 ID · ' + str(plan['node_id']), 'style': 'caption'}]})
        if len(facts['resource_mentions']) > 15 or len(facts['plan_snapshots']) > 15 or facts['resources_truncated'] or facts['plans_truncated']:
            children.append({'title': '더 많은 기록은 CLI observations에서 확인', 'style': 'caption'})
    else:
        children.append({'title': '아직 관측되지 않은 세션', 'style': 'caption'})
    children += [{'separator': True}, {'title': '중요한 변화가 있을 때 세션이 보고합니다.',
                                        'caption': '보고를 켜도 쉬고 있는 세션을 깨우지는 않습니다.'}]
    return {'kind': 'session', 'session_id': sid, 'title': _text(sid, 65) + ' · ' + reporting,
            'icon': 'bubble.left' if contribution['reporting'] else 'pause.circle', 'children': children}


def menu_model(value):
    """Read-only, framework-independent presentation; no stored text is rewritten."""
    runtime = value['runtime']
    collector = runtime.get('collector') or {}
    error = collector.get('last_error')
    stale_count = runtime.get('refresh', {}).get('stale_sessions', 0)
    observation = value.get('observations') or {}
    troubled = bool(error or stale_count or value['errors'] or observation.get('warnings')
                    or any(t['health'] != 'ok' for t in value['tasks']))
    status = '관측 중' if runtime.get('running') else '관측 일시 중지'
    rows = [{'title': ('⚠ ' + status + ' · 확인 필요' if troubled else '◉ ' + status), 'style': 'heading',
             'caption': '최근 수집 ' + _time(collector.get('last_success'))}]
    if error:
        rows.append({'title': '수집을 다시 시도하고 있습니다.' if runtime.get('running') else '수집 확인 필요',
                     'caption': _text(error)})
    if stale_count:
        rows.append({'title': '⚠ 확인이 필요한 세션 ' + str(stale_count) + '개'})
    for warning in observation.get('warnings', []):
        rows.append({'title': '⚠ 관측 기록을 확인해 주세요.', 'caption': _text(warning)})
    for problem in value['errors']:
        rows.append({'title': '⚠ ' + _text(problem['message'])})
    rows += [{'separator': True}, {'title': '태스크 ' + str(len(value['tasks'])) + '개', 'style': 'section'}]
    observed = {session['id']: session for session in observation.get('sessions', [])}
    for task in value['tasks'][:50]:
        state = task['state']
        title = _text(state['title'] if state else task['task_id'], 65)
        children = []
        if task['health'] != 'ok':
            title = '⚠ ' + title
            children += [{'title': '파일 확인 필요', 'style': 'heading',
                          'caption': '마지막 정상 기록 표시' if state else '표시할 정상 기록이 없습니다.'}]
            children += [{'title': _text(e), 'style': 'caption'} for e in task['errors'][:5]]
            children.append({'separator': True})
        if state:
            active = [i for i in state['items'] if not i['archived']]
            children.append({'title': '추적 항목 ' + str(len(active)) + '개', 'style': 'section'})
            for item in active[:50]:
                detail = [{'title': _text(item['value'], 500)},
                          {'title': _origin(item) + ' · ' + _actor(item['updated_by']),
                           'caption': _time(item['updated_at'])}]
                if item['pinned']:
                    detail.append({'title': '📌 고정됨 · 자동 변경되지 않습니다.'})
                if item['evidence']:
                    detail += [{'separator': True}, {'title': '근거', 'style': 'section'}]
                    for evidence in item['evidence'][:10]:
                        detail.append({'title': _text(evidence['reference'], 240), 'caption': evidence['session_id']})
                    if len(item['evidence']) > 10:
                        detail.append({'title': '추가 근거는 상태 파일에서 확인', 'style': 'caption'})
                else:
                    detail.append({'title': '첨부된 근거 없음', 'style': 'caption'})
                detail += [{'separator': True}, {'title': '별도 검증되지 않은 보고입니다.', 'style': 'caption'},
                           {'title': '항목 ID · ' + item['item_id'], 'style': 'caption'}]
                children.append({'kind': 'item', 'item_id': item['item_id'],
                                 'title': ('📌 ' if item['pinned'] else '') + _text(item['title'], 60) + ' · ' + _text(_status(item['status']), 40),
                                 'icon': None if item['pinned'] else _status_icon(item['status']), 'children': detail})
                children.append({'kind': 'item_summary', 'item_id': item['item_id'],
                                 'title': _text(item['value'], 170),
                                 'caption': _actor(item['updated_by']) + ' · ' + _time(item['updated_at']) + ' · ' + _origin(item)})
            if not active:
                children.append({'title': '아직 보고된 항목이 없습니다.', 'style': 'caption'})
            if len(active) > 50:
                children.append({'title': f'항목 {len(active) - 50}개 더 있음 · 상태 파일에서 확인', 'style': 'caption'})
            archived = len(state['items']) - len(active)
            if archived:
                children.append({'title': '보관된 항목 ' + str(archived) + '개', 'style': 'caption'})
            if state['notes'] or state['view']['markdown']:
                children += [{'separator': True}, {'title': '메모', 'style': 'section'}]
                children += [{'title': _text(n['text'], 240)} for n in state['notes'][:30]]
                if len(state['notes']) > 30:
                    children.append({'title': '추가 메모는 상태 파일에서 확인', 'style': 'caption'})
                if state['view']['markdown']:
                    children.append({'title': _text(state['view']['markdown'], 240)})
            children += [{'separator': True}, {'title': '연결된 세션 ' + str(len(state['contributions'])) + '개', 'style': 'section'}]
            children += [_session_row(c, task, observed) for c in state['contributions']]
            if not state['contributions']:
                children.append({'title': '연결된 세션이 없습니다.', 'style': 'caption'})
            legacy = state.get('legacy')
            if legacy:
                imported = [{'title': '가져온 기록 · 원본과 자동 동기화되지 않습니다.'},
                            {'title': _text(legacy.get('description') or '설명 없음')},
                            {'title': '당시 상태 · ' + _text(legacy.get('lifecycle') or '미확인')}]
                for section in (legacy.get('view') or {}).get('sections', [])[:30]:
                    section_rows = [{'title': ('📌 ' if i.get('pinned') else '') + _text(i.get('content', ''), 240)}
                                    for i in section.get('items', [])[:50]]
                    if len(section.get('items', [])) > 50:
                        section_rows.append({'title': '추가 항목은 상태 파일에 보존', 'style': 'caption'})
                    imported.append({'title': _text(section.get('title', '메모')), 'children': section_rows or [{'title': '내용 없음'}]})
                imported += [{'title': _text(r, 220)} for r in legacy.get('resources', [])[:30]]
                imported.append({'title': '확인 위치 ' + str(len(legacy.get('review_cursors', []))) + '개 보존', 'style': 'caption'})
                children.append({'title': '이전 기록', 'icon': 'archivebox', 'children': imported})
        children += [{'separator': True}, {'title': '상태 파일 보기', 'action': 'reveal', 'task_id': task['task_id'], 'icon': 'doc.text'},
                     {'title': '상세 정보', 'icon': 'info.circle', 'children': [
                         {'title': '태스크 ID · ' + task['task_id']},
                         {'title': '파일 상태 · ' + task['health']},
                         {'title': '버전 ' + str(state['revision']) + ' · ' + _time(state['updated_at'])} if state else {'title': '상태를 읽을 수 없습니다.'},
                         {'title': '세션 보고는 별도로 검증되지 않습니다.',
                          'caption': '작성 세션과 근거를 함께 확인하세요.'}]}]
        rows.append({'kind': 'task', 'task_id': task['task_id'], 'title': title, 'icon': 'square.stack', 'children': children})
    if len(value['tasks']) > 50:
        rows.append({'title': '태스크 50개 표시 · 나머지는 CLI list에서 확인', 'style': 'caption'})
    if not value['tasks'] and not value['errors']:
        rows.append({'title': '아직 태스크가 없습니다.', 'caption': 'Devin 세션에서 보고 스킬로 태스크를 연결하세요.'})
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


def _native_label(row):
    """Readable, non-interactive labels instead of disabled gray menu commands."""
    from AppKit import (NSView, NSTextField, NSFont, NSColor, NSMakeRect,
                        NSViewWidthSizable, NSLineBreakByWordWrapping)
    width, inset = row.get('width', 430), 20
    style = row.get('style')
    fields = []
    specs = [(row['title'], 11 if style in ('caption', 'section') else 12,
              style in ('heading', 'section'), style in ('caption', 'section'),
              2 if row.get('kind') == 'item_summary' else 4)]
    if row.get('caption'):
        specs.append((row['caption'], 10.5, False, True, 2))
    for text, size, bold, secondary, lines in specs:
        field = NSTextField.wrappingLabelWithString_(text)
        field.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
        field.setTextColor_(NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
        field.setMaximumNumberOfLines_(lines)
        field.setLineBreakMode_(NSLineBreakByWordWrapping)
        field.cell().setTruncatesLastVisibleLine_(True)
        field.setToolTip_(text)
        field.setAccessibilityLabel_(text)
        field.setAutoresizingMask_(NSViewWidthSizable)
        height = min(lines * 16, max(16, field.cell().cellSizeForBounds_(NSMakeRect(0, 0, width - 2 * inset, 1000)).height))
        fields.append((field, height))
    padding = 4 if style == 'section' else 6
    height = sum(h for _, h in fields) + padding * 2 + (len(fields) - 1) * 3
    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
    view.setAutoresizingMask_(NSViewWidthSizable)
    y = height - padding
    for field, field_height in fields:
        y -= field_height
        field.setFrame_(NSMakeRect(inset, y, width - 2 * inset, field_height))
        view.addSubview_(field)
        y -= 3
    return view


def _native_icon(item, name):
    from AppKit import NSImage, NSMakeSize
    if name:
        icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        if icon:
            icon = icon.copy()
            icon.setSize_(NSMakeSize(14, 14))
            icon.setTemplate_(True)
            item._menuitem.setImage_(icon)


def _clear_native_menu(menu):
    # rumps 0.4 retains every MenuItem in a callback registry, including labels.
    # Release retired views/callbacks before rebuilding a closed menu.
    from rumps.rumps import NSApp
    for item in list(menu.values()):
        if hasattr(item, 'values') and item:
            _clear_native_menu(item)
        NSApp._ns_to_py_and_callback.pop(item._menuitem, None)
    menu.clear()


def run_native(root, config, config_path=None):
    if sys.platform != 'darwin':
        raise ValueError('메뉴 막대 UI는 macOS에서만 실행합니다. collector와 CLI는 별도로 사용할 수 있습니다.')
    try:
        import rumps
    except ImportError:
        raise ValueError('메뉴 막대 의존성이 없습니다. scripts/setup_menu.py로 전용 환경을 설치한 뒤 그 환경의 Python으로 menu를 실행하세요.') from None
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSObject

    runtime = CollectorRuntime(config)
    controller = MenuController(root, config, runtime, config_path=config_path)
    app = rumps.App('Devin Task Observer', title='◉', quit_button=None)
    previous = None
    stopped = threading.Event()

    class ObserverMenuDelegate(NSObject):
        def menuWillOpen_(self, menu):
            self.opened = True

        def menuDidClose_(self, menu):
            self.opened = False

    delegate = ObserverMenuDelegate.alloc().init()
    delegate.opened = False
    app.menu._menu.setDelegate_(delegate)

    def quit_app(_=None):
        stopped.set()
        # NSApplication.terminate_ exits the native process rather than unwinding
        # Python's run loop. Complete child shutdown and release ownership first.
        controller.stop()
        runtime.stop()
        rumps.quit_application()

    def make_item(row, depth=0):
        if row.get('separator'):
            return None
        item = rumps.MenuItem(row['title'])
        if row.get('action') == 'reveal':
            path = Path(root).absolute() / 'tasks' / row['task_id'] / 'state.json'
            item.set_callback(lambda _: subprocess.Popen(['/usr/bin/open', '-R', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for index, child in enumerate(row.get('children', [])):
            # The display title is not an identity: tasks/items can share titles.
            item[index] = make_item(child, depth + 1)
        if not row.get('children') and not row.get('action'):
            item._menuitem.setView_(_native_label({**row, 'width': 320 if depth == 0 else 430}))
        _native_icon(item, row.get('icon'))
        return item

    def update(_):
        nonlocal previous
        if stopped.is_set():
            quit_app()
            return
        # Keep the selected path stable while reading. Apply the latest snapshot
        # on a later timer tick after close, never from the NSMenu delegate.
        if delegate.opened:
            return
        value = controller.latest()
        if value is None or value == previous:
            return
        previous = value
        app.title = value['title']
        _clear_native_menu(app.menu)
        for index, row in enumerate(value['rows']):
            app.menu[index] = make_item(row)
        app.menu.add(None)
        refresh = rumps.MenuItem('지금 새로고침', callback=lambda _: controller.refresh())
        _native_icon(refresh, 'arrow.clockwise')
        app.menu.add(refresh)
        folder = rumps.MenuItem('상태 폴더 열기', callback=lambda _: subprocess.Popen(['/usr/bin/open', str(Path(root).absolute())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        _native_icon(folder, 'folder')
        app.menu.add(folder)
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
