"""Opt-in real personal Codex/Devin ACP integration; synthetic source and labels only."""
import json
import os
import unittest
from pathlib import Path
import test_reconciliation as fixtures
from observer.reconciliation import refresh, projection
from observer.views import get_view


@unittest.skipUnless(os.environ.get('OBSERVER_LIVE_CODEX'),'explicit personal live-model opt-in required')
class CodexLiveTests(unittest.TestCase):
    adapter_kind='codex_exec'
    executable_env='OBSERVER_LIVE_CODEX'
    report_prefix='codex'
    def test_real_two_session_semantics_usage_and_zero_call_replay(self):
        f=fixtures.ReconciliationTests();f.setUp()
        try:
            f.f.writer.execute('UPDATE message_nodes SET chat_message=? WHERE session_id=? AND node_id=1',
                (fixtures.message('b','구현을 완료했습니다. 테스트는 진행 중이며 아직 결과는 없습니다.','assistant'),'one'))
            f.f.writer.execute('UPDATE message_nodes SET chat_message=? WHERE session_id=?',
                (fixtures.message('b0','인터페이스가 v2로 변경되어 기존 테스트가 실패했습니다. 호환성 불일치가 블로커입니다. 구현을 수정한 뒤 테스트를 다시 실행해야 합니다.','assistant'),'two'))
            f.f.writer.commit()
            # Rebuild this disposable observer before first semantics; no superseded source bodies.
            f.db.close();f.store.unlink()
            from observer.store import open_store
            from observer.registry import create_task,link_session
            from observer.views import ensure_view,add_item
            f.f.db=f.db=open_store(f.store,f.src);f.f.collect()
            f.task=create_task(f.db,'M2 합성 기능 구현','구현과 테스트 상태를 세션 A와 B에서 추적합니다.')
            vid=ensure_view(f.db,f.task);section=f.db.execute('SELECT id FROM view_sections WHERE view_id=?',(vid,)).fetchone()[0]
            add_item(f.db,vid,section,1,'인간 결정: 기존 인터페이스 호환성을 유지한다.',True);human=get_view(f.db,f.task)
            link_session(f.db,f.task,'one');link_session(f.db,f.task,'two')
            policy={**f.policy,'format_repair':True,'adapter':self.adapter_kind,'executable':os.environ[self.executable_env]}
            if self.adapter_kind=='devin_acp':policy['acp_approved']=True
            try:result=refresh(f.db,f.src,f.store,f.task,[f.work],policy)
            except Exception:
                run=f.db.execute('SELECT proposal,error,input_tokens,output_tokens FROM tracking_runs ORDER BY created_at DESC LIMIT 1').fetchone()
                if run:Path('/tmp/observer-m2-'+self.report_prefix+'-live-failure.json').write_text(json.dumps({'synthetic_only':True,'proposal':json.loads(run[0]) if run[0] else None,'error':run[1],'input_tokens':run[2],'output_tokens':run[3]},ensure_ascii=False,indent=2))
                raise
            state=projection(f.db,f.task,[f.work],policy)
            self.assertTrue(result['model_called']);self.assertEqual(result['status'],'applied')
            text='\n'.join(i['content'] for i in state['items'])
            for label in ('구현','완료','테스트','실패','인터페이스','수정'):self.assertIn(label,text)
            self.assertTrue({'progress','blocker','next_action'} <= {i['category'] for i in state['items']})
            self.assertEqual({e['session_id'] for i in state['items'] for e in i['evidence']},{'one','two'})
            self.assertEqual(get_view(f.db,f.task),human);self.assertEqual(state['freshness'],'current')
            if self.adapter_kind=='codex_exec' or result['usage']['source']=='devin_acp_observed_v1':
                self.assertGreater(result['usage']['input_tokens'],0);self.assertGreater(result['usage']['output_tokens'],0)
            else:
                self.assertIsNone(result['usage']['input_tokens']);self.assertIsNone(result['usage']['output_tokens'])
            attempts=f.db.execute('SELECT COUNT(*) FROM tracking_runs').fetchone()[0]
            self.assertTrue(1<=attempts<=2)
            self.assertEqual(refresh(f.db,f.src,f.store,f.task,[f.work],policy),{'status':'unchanged','model_called':False})
            self.assertEqual(f.db.execute('SELECT COUNT(*) FROM tracking_runs').fetchone()[0],attempts)
            report={'scenario':'real '+self.adapter_kind+', synthetic two-session SQLite fixture','model_called':True,
                    'usage':result['usage'],'diagnostics':json.loads(f.db.execute('SELECT diagnostics FROM tracking_runs ORDER BY created_at DESC LIMIT 1').fetchone()[0]),'items':len(state['items']),'categories':sorted({i['category'] for i in state['items']}),
                    'human_preserved':True,'analysis_attempts':attempts,'unchanged_replay_calls':0,'unsupported_assertions':'requires human review; schema does not prove semantic truth'}
            Path('/tmp/observer-m2-'+self.report_prefix+'-live-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(report,ensure_ascii=False))
        finally:f.tearDown()

class DevinAcpLiveTests(CodexLiveTests):
    __unittest_skip__=not bool(os.environ.get('OBSERVER_LIVE_DEVIN_ACP'))
    __unittest_skip_why__='explicit personal Devin ACP live opt-in required'
    adapter_kind='devin_acp'
    executable_env='OBSERVER_LIVE_DEVIN_ACP'
    report_prefix='acp'


if __name__=='__main__':unittest.main()
