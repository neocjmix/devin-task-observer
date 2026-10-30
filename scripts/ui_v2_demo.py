#!/usr/bin/env python3
"""Create an isolated synthetic UI acceptance workspace; refuse an existing target."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from observer.app import save_config
from observer.cooperative import CooperativeState


def create(directory):
    root = Path(directory).expanduser().absolute()
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / 'workspace'
    workspace.mkdir()
    (workspace / 'report.md').write_text('# Synthetic acceptance deliverable\nNo personal session data.\n')
    source = root / 'source/sessions.db'
    source.parent.mkdir()
    with closing(sqlite3.connect(source)) as db:
        db.executescript('CREATE TABLE refinery_schema_history(version INTEGER); INSERT INTO refinery_schema_history VALUES(17);'
                        'CREATE TABLE sessions(id TEXT,title TEXT,working_directory TEXT,main_chain_id INTEGER,last_activity_at INTEGER);'
                        'CREATE TABLE message_nodes(session_id TEXT,node_id INTEGER,parent_node_id INTEGER,chat_message TEXT);')
        for n in range(1, 9):
            sid = f'ui-session-{n}'
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (sid, f'테스트 세션 {n}', str(workspace), 1, int(time.time())))
            message = {'message_id': f'message-{n}', 'role': 'assistant', 'content': 'Synthetic PR https://github.com/example/demo/pull/42 and LAB-42.',
                       'metadata': {'metrics': {'input_tokens': 100 + n, 'cache_read_tokens': 1000, 'output_tokens': 40 + n}}}
            db.execute('INSERT INTO message_nodes VALUES(?,?,?,?)', (sid, 1, None, json.dumps(message)))
        db.commit()
    config = {'version': 1, 'workspaces': [str(workspace)], 'source': str(source),
              'store': str(root / 'collector/observer.sqlite3'), 'port': 8765,
              'git': '/Library/Developer/CommandLineTools/usr/bin/git'}
    save_config(root / 'config.json', config)
    core = CooperativeState(root / 'state')
    titles = ['인증 갱신', '태스크 자동추적', 'Figma Bridge', '데이터 이관', '이전 파서 검증', '마지막 정상 기록', '정상 기록 없음']
    for n, title in enumerate(titles, 1):
        tid, sid = f'task-{n}', f'ui-session-{n}'
        core.create(tid, title, sid)
        rev = lambda: core.read(tid)['state']['revision']
        core.reporting(tid, sid, True, rev())
        if n == 1:
            core.link(tid, 'ui-session-8', rev())
            core.reporting(tid, 'ui-session-8', True, rev())
        if n == 2:
            core.link(tid, 'ui-session-1', rev())
        items = [{'op': 'define', 'item_id': 'situation', 'title': '현재 상황', 'role': 'situation',
                  'value': '구현을 마치고 통합 테스트를 진행 중입니다.', 'status': 'reported', 'evidence': []},
                 {'op': 'define', 'item_id': 'next', 'title': '다음 행동', 'role': 'next_action',
                  'value': '실패한 테스트를 수정하고 다시 검증합니다.', 'status': 'pending', 'evidence': []}]
        if n == 1:
            items.append({'op': 'define', 'item_id': 'decision', 'title': '세션 만료 정책', 'role': 'intervention',
                          'attention_required': True, 'value': '정책 A와 B 중 선택이 필요합니다.', 'status': 'waiting',
                          'evidence': [{'session_id': sid, 'reference': 'synthetic:policy-choice', 'kind': 'session_event'}]})
        core.report({'schema_version': 1, 'report_id': f'seed-{n}', 'session_id': sid,
                     'expected_revisions': {tid: rev()}, 'changes': {tid: items}})
        if n == 1:
            core.note(tid, '기존 사용자 메모를 보존합니다.', rev())
            core.pin(tid, 'next', True, rev())
    def revision(task):
        return core.read(task)['state']['revision']
    core.observation('task-4', False, revision('task-4'))
    core.observation('task-5', False, revision('task-5'))
    core.lifecycle('task-5', True, revision('task-5'))
    core.resource_put('task-1', {'resource_id': 'deliverable', 'kind': 'deliverable',
        'label': '검증 기록', 'target': str(workspace / 'report.md'), 'open_mode': 'file'}, revision('task-1'))
    from observer.ui_reads import ReadCursors
    cursors = ReadCursors(root / 'state')
    for task in ('task-2', 'task-3'):
        state = core.read(task)['state']
        cursors.mark(task, state['revision'], state['last_report_revision'])
    print(json.dumps({'config': str(root / 'config.json'), 'root': str(root / 'state'), 'synthetic': True}, ensure_ascii=False))
    return root


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    create(parser.parse_args().directory)
