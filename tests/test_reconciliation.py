"""Synthetic labeled M2 scenarios. No model or network is used in this file."""
import copy
import json
import sqlite3
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import test_observer as fixture
message = fixture.message
from observer.registry import create_task, link_session, set_link_active
from observer.views import ensure_view, add_item, get_view
from observer.reconciliation import refresh, projection, prepare, correct_item, resolve_pending, validate_policy
from observer.model_adapters import ModelResult, ModelError
from observer.store import open_store


def operation(name, content, evidence, category='progress', **changes):
    return {'id': name, 'op': 'add', 'key': name, 'target_id': None, 'category': category,
            'content': content, 'status': 'open', 'epistemic': 'reported',
            'evidence_ids': [evidence], 'conflicts_with': [], **changes}


class FakeModel:
    identity = 'synthetic:1'
    def __init__(self, callback=None): self.calls=[]; self.callback=callback or (lambda b: [])
    def analyze(self, bundle, cancel=None):
        self.calls.append(copy.deepcopy(bundle))
        ops=self.callback(bundle)
        return ModelResult({'version':1,'task_id':bundle['task_id'],'view_revision':bundle['view_revision'],'operations':ops},
                           {'input_tokens':120,'output_tokens':80,'source':'synthetic'},'synthetic-run',{})


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CollectorTests(); self.f.setUp(); self.db=self.f.db; self.work=self.f.work; self.src=self.f.src
        self.store=self.f.root/'observer/observer.db'
        self.f.writer.execute('UPDATE message_nodes SET chat_message=? WHERE node_id=0',(message('a','Synthetic system context','system'),))
        self.f.writer.execute('UPDATE message_nodes SET chat_message=? WHERE node_id=1',(message('b','Implementation complete; tests running.','assistant'),))
        self.f.writer.execute('INSERT INTO sessions VALUES (?,?,?,?)',('two','Session B',str(self.work),0))
        self.f.writer.execute('INSERT INTO message_nodes VALUES (?,?,?,?,?)',(100,'two',0,None,message('b0','Tests fail after the interface changed. Blocked by interface mismatch; fix implementation and rerun tests.','assistant')))
        self.f.writer.commit(); self.f.collect()
        self.task=create_task(self.db,'Synthetic implementation task','Track implementation and testing')
        self.vid=ensure_view(self.db,self.task)
        self.section=self.db.execute('SELECT id FROM view_sections WHERE view_id=?',(self.vid,)).fetchone()[0]
        add_item(self.db,self.vid,self.section,1,'Human decision: retain compatibility and do not delete this note.',True)
        self.human=get_view(self.db,self.task)
        self.a=link_session(self.db,self.task,'one'); self.b=link_session(self.db,self.task,'two')
        self.policy={'enabled':True,'environment':'personal','adapter':'codex_exec','executable':'/synthetic/codex','format_repair':False}
    def tearDown(self): self.f.tearDown()
    def run_refresh(self,model=None,**kw):
        return refresh(self.db,self.src,self.store,self.task,[self.work],self.policy,adapter=model or FakeModel(),**kw)
    def state(self): return projection(self.db,self.task,[self.work],self.policy)
    def append(self,text,sid='one',mid=None):
        node=self.f.writer.execute('SELECT MAX(node_id) FROM message_nodes WHERE session_id=?',(sid,)).fetchone()[0]+1
        row=self.f.writer.execute('SELECT MAX(row_id) FROM message_nodes').fetchone()[0]+1
        self.f.writer.execute('INSERT INTO message_nodes VALUES (?,?,?,?,?)',(row,sid,node,node-1,message(mid or sid+str(node),text,'assistant')))
        self.f.writer.execute('UPDATE sessions SET main_chain_id=? WHERE id=?',(node,sid));self.f.writer.commit();self.f.collect()
    def scenario(self,b):
        a=next(e['id'] for e in b['evidence_delta'] if e['session_id']=='one'); c=next(e['id'] for e in b['evidence_delta'] if e['session_id']=='two')
        return [operation('implementation','구현 완료 보고',a),operation('testing','테스트 진행 보고',a),
                operation('failed','테스트 실패 보고',c),operation('interface','인터페이스 변경으로 테스트 차단',c,'blocker'),
                operation('fix','구현 수정 및 재검증',c,'next_action')]
    def test_two_session_slice_auto_applies_with_provenance_human_preservation_and_no_replay(self):
        model=FakeModel(self.scenario);result=self.run_refresh(model)
        self.assertEqual(result['status'],'applied');self.assertEqual(len(self.state()['items']),5)
        self.assertEqual(get_view(self.db,self.task),self.human)
        self.assertEqual({e['session_id'] for i in self.state()['items'] for e in i['evidence']},{'one','two'})
        self.assertTrue(all(i['epistemic']=='reported' for i in self.state()['items']))
        self.assertEqual(self.state()['freshness'],'current')
        self.assertEqual(self.run_refresh(model),{'status':'unchanged','model_called':False});self.assertEqual(len(model.calls),1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tracking_cursors').fetchone()[0],2)
        self.db.close();self.f.db=self.db=open_store(self.store,self.src)
        self.assertEqual(len(self.state()['items']),5);self.assertEqual(get_view(self.db,self.task),self.human)
    def test_only_delta_and_affected_context_are_sent(self):
        self.run_refresh(FakeModel(self.scenario));self.append('Implementation fix is now in progress.')
        model=FakeModel();self.run_refresh(model);bundle=model.calls[0]
        self.assertEqual(len(bundle['evidence_delta']),1)
        self.assertNotIn('Tests fail after the interface changed.',json.dumps(bundle['evidence_delta']))
        self.assertNotIn('Human decision:',json.dumps(bundle['view_contract']))
        self.assertTrue(bundle['affected_items'])
    def test_conflict_operation_can_reference_new_keys_in_same_atomic_patch(self):
        def both(b):
            a=next(e['id'] for e in b['evidence_delta'] if e['session_id']=='one');c=next(e['id'] for e in b['evidence_delta'] if e['session_id']=='two')
            return [operation('one-claim','Tests are running.',a),operation('other-claim','Tests failed.',c),
                    operation('flag','Conflicting test reports',c,op='flag_conflict',key='one-claim',target_id='one-claim',conflicts_with=['other-claim'])]
        self.run_refresh(FakeModel(both));items=self.state()['items']
        self.assertEqual(len(items),2);self.assertTrue(all(i['conflicts'] for i in items));self.assertEqual(self.state()['proposals'],[])

    def test_conflicting_sessions_coexist_without_latest_winner(self):
        self.run_refresh(FakeModel(lambda b:[operation('compatible','The interface is compatible.',b['evidence_delta'][0]['id'])]))
        first=self.state()['items'][0];self.append('The interface is incompatible; earlier compatibility claim is contradicted.','two')
        def conflict(b):
            e=b['evidence_delta'][0]['id']
            return [operation('incompatible','The interface is incompatible.',e,conflicts_with=[first['id']])]
        self.run_refresh(FakeModel(conflict));items=self.state()['items']
        self.assertEqual(len(items),2);self.assertTrue(all(i['conflicts'] and i['status']=='open' for i in items))
    def test_hypothesis_revision_preserves_both_and_requires_supersede_approval(self):
        self.run_refresh(FakeModel(lambda b:[operation('hypothesis','Hypothesis: cache invalidation causes failures.',b['evidence_delta'][0]['id'],'hypothesis',epistemic='hypothesis')]))
        old=self.state()['items'][0];self.append('New analysis: failure is caused by interface mismatch, not cache invalidation.','two')
        def revised(b):
            e=b['evidence_delta'][0]['id']
            return [operation('replacement','Hypothesis: interface mismatch causes failures.',e,'hypothesis',epistemic='hypothesis'),
                    operation('supersede-old','Request replacing previous hypothesis',e,'hypothesis',op='supersede',key=old['key'],target_id=old['id'],status='superseded')]
        self.run_refresh(FakeModel(revised));state=self.state()
        self.assertEqual(len(state['items']),2);self.assertEqual(state['items'][0]['status'],'open');self.assertEqual(state['proposals'][0]['status'],'pending')
        resolve_pending(self.db,self.task,state['proposals'][0]['id'],state['revision'],True,self.src,[self.work])
        self.assertEqual(next(i for i in self.state()['items'] if i['id']==old['id'])['status'],'superseded')
    def test_same_session_revision_safely_supersedes_machine_claim_without_approval(self):
        self.run_refresh(FakeModel(lambda b:[operation('hypothesis','Hypothesis: cache issue.',b['evidence_delta'][0]['id'],'hypothesis',epistemic='hypothesis')]))
        old=self.state()['items'][0];self.append('Analysis revision: the interface is the cause.')
        def revise(b):
            e=b['evidence_delta'][0]['id']
            return [operation('replacement','Hypothesis: interface issue.',e,'hypothesis',epistemic='hypothesis'),operation('replace-old','Prior hypothesis superseded',e,'hypothesis',op='supersede',key=old['key'],target_id=old['id'],status='superseded')]
        self.run_refresh(FakeModel(revise));state=self.state()
        self.assertEqual(len(state['items']),2);self.assertEqual(next(i for i in state['items'] if i['id']==old['id'])['status'],'superseded')
        self.assertEqual(state['proposals'],[]);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tracking_history WHERE item_id=?',(old['id'],)).fetchone()[0],2)

    def test_design_decision_change_never_deletes_prior_decision(self):
        self.run_refresh(FakeModel(lambda b:[operation('decision','Decision: use polling.',b['evidence_delta'][0]['id'],'decision')]))
        self.append('Design decision changed: keep polling as fallback and use file notifications for wakeup.')
        self.run_refresh(FakeModel(lambda b:[operation('new-decision','Decision: notifications with polling fallback.',b['evidence_delta'][0]['id'],'decision')]))
        self.assertEqual(len(self.state()['items']),2);self.assertEqual(len([i for i in self.state()['items'] if i['category']=='decision']),2)
        self.assertEqual(get_view(self.db,self.task),self.human)
    def test_question_resolution_is_reported_status_with_history(self):
        self.run_refresh(FakeModel(lambda b:[operation('question','Which interface version should tests use?',b['evidence_delta'][0]['id'],'question')]))
        old=self.state()['items'][0];self.append('Answer: tests should use interface v2.')
        self.run_refresh(FakeModel(lambda b:[operation('answer','Reported resolved',b['evidence_delta'][0]['id'],'question',op='update_status',key=old['key'],target_id=old['id'],status='resolved')]))
        item=self.state()['items'][0];self.assertEqual(item['status'],'resolved');self.assertEqual(len(item['evidence']),2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tracking_history').fetchone()[0],2)
    def test_human_correction_and_pins_are_authoritative(self):
        self.run_refresh(FakeModel(self.scenario));state=self.state();item=state['items'][0]
        correct_item(self.db,self.task,item['id'],state['revision'],content='Human correction',pinned=True)
        self.append('Implementation complete again.')
        model=FakeModel(lambda b:[operation('change','Unwanted update',b['evidence_delta'][0]['id'],op='update_status',key=item['key'],target_id=item['id'],status='resolved')])
        with self.assertRaisesRegex(ModelError,'protected_item'):self.run_refresh(model)
        kept=next(i for i in self.state()['items'] if i['id']==item['id']);self.assertEqual(kept['content'],'Human correction');self.assertTrue(kept['pinned'])
        self.assertGreater(self.state()['pending_observations'],0)
    def test_new_key_alias_cannot_bypass_human_ownership_in_conflict_operation(self):
        self.run_refresh(FakeModel(lambda b:[operation('protected','Original report',b['evidence_delta'][0]['id'])]))
        state=self.state();item=state['items'][0];correct_item(self.db,self.task,item['id'],state['revision'],content='Human authority',pinned=True)
        self.append('A conflicting new report')
        def attack(b):
            e=b['evidence_delta'][0]['id']
            return [operation('protected','Overwrite human by key alias',e),operation('other','Other report',e),operation('flag','Conflict',e,op='flag_conflict',key='protected',target_id='protected',conflicts_with=['other'])]
        with self.assertRaisesRegex(ModelError,'protected_item'):self.run_refresh(FakeModel(attack))
        self.assertEqual(len(self.state()['items']),1);self.assertEqual(self.state()['items'][0]['content'],'Human authority');self.assertTrue(self.state()['items'][0]['pinned'])

    def test_bad_evidence_target_version_and_verified_claims_reject_entire_patch(self):
        for mutation in ('evidence','target','epistemic','version'):
            with self.subTest(mutation=mutation):
                def bad(b):
                    op=operation('bad',mutation,b['evidence_delta'][0]['id'])
                    if mutation=='evidence':op['evidence_ids']=['obs:foreign']
                    if mutation=='target':op.update(op='update_status',target_id='foreign')
                    if mutation=='epistemic':op['epistemic']='verified'
                    if mutation=='version':op['extra']='not in schema'
                    return [op]
                # Different adapter identity makes distinct rejected requests, not retries.
                model=FakeModel(bad);model.identity=mutation
                with self.assertRaises(ModelError):self.run_refresh(model)
        self.assertEqual(self.state()['items'],[]);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tracking_cursors').fetchone()[0],0)
        self.assertTrue(all(r[0] for r in self.db.execute('SELECT proposal FROM tracking_runs')))
    def test_failed_model_keeps_collector_and_semantic_checkpoints_and_requires_explicit_retry(self):
        before=self.db.execute('SELECT * FROM checkpoints ORDER BY session_id').fetchall()
        class Failing(FakeModel):
            def analyze(self,b,cancel=None):self.calls.append(b);raise ModelError('timeout')
        model=Failing()
        with self.assertRaisesRegex(ModelError,'timeout'):self.run_refresh(model)
        self.assertEqual(self.db.execute('SELECT * FROM checkpoints ORDER BY session_id').fetchall(),before)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tracking_cursors').fetchone()[0],0)
        with self.assertRaisesRegex(ModelError,'explicit_retry'):self.run_refresh(model)
        self.assertEqual(len(model.calls),1)
        with self.assertRaisesRegex(ModelError,'timeout'):self.run_refresh(model,retry=True)
        self.assertEqual(len(model.calls),2)
        self.assertTrue(all(r[0]>0 for r in self.db.execute('SELECT reserved_tokens FROM tracking_runs')))
    def test_one_budgeted_format_repair_applies_valid_patch_and_accounts_for_both_calls(self):
        self.policy['format_repair']=True
        model=FakeModel()
        def repair(b):
            if 'repair_feedback' not in b:return [operation('bad','Wrong target',b['evidence_delta'][0]['id'],op='update_status',target_id='human-or-invented')]
            return [operation('fixed','Implementation report',b['evidence_delta'][0]['id'])]
        model.callback=repair;result=self.run_refresh(model)
        self.assertTrue(result['format_repair_attempted']);self.assertEqual(len(model.calls),2);self.assertEqual(result['usage']['input_tokens'],240)
        self.assertEqual(self.db.execute('SELECT status FROM tracking_runs ORDER BY created_at').fetchall(),[('failed',),('applied',)])
        self.assertEqual(len(self.state()['items']),1);self.assertEqual(self.run_refresh(model)['status'],'unchanged');self.assertEqual(len(model.calls),2)

    def test_format_repair_is_bounded_and_cannot_bypass_budget(self):
        self.policy['format_repair']=True;self.policy['daily_calls']=1
        model=FakeModel(lambda b:[operation('bad','Wrong target',b['evidence_delta'][0]['id'],op='update_status',target_id='invented')])
        with self.assertRaisesRegex(ModelError,'budget_exhausted'):self.run_refresh(model)
        self.assertEqual(len(model.calls),1);self.assertEqual(self.state()['items'],[]);self.assertGreater(self.state()['pending_observations'],0)
        self.policy['daily_calls']=8
        with self.assertRaisesRegex(ModelError,'invalid_patch_target'):self.run_refresh(model,retry=True)
        self.assertEqual(len(model.calls),3)
        with self.assertRaisesRegex(ModelError,'retry_limit'):self.run_refresh(model,retry=True)
        self.assertEqual(len(model.calls),3)

    def test_budget_exhaustion_does_not_call_or_advance_and_default_off_never_calls(self):
        self.policy['daily_calls']=1;self.run_refresh();self.append('New progress after first analysis.')
        model=FakeModel()
        with self.assertRaisesRegex(ModelError,'budget_exhausted'):self.run_refresh(model)
        self.assertEqual(len(model.calls),0);self.assertGreater(self.state()['pending_observations'],0)
        self.assertEqual(self.state()['last_error'],'semantic_budget_exhausted')
        with self.assertRaisesRegex(ModelError,'semantic_disabled'):refresh(self.db,self.src,self.store,self.task,[self.work],None,adapter=model)
    def test_repeated_metadata_or_identical_message_does_not_call_model(self):
        self.run_refresh();self.append('Implementation complete; tests running.',mid='b')
        model=FakeModel();result=self.run_refresh(model)
        self.assertFalse(result['model_called']);self.assertEqual(len(model.calls),0);self.assertEqual(self.state()['pending_observations'],0)
    def test_two_tasks_have_independent_semantic_cursors_and_shared_scope(self):
        other=create_task(self.db,'Other task');ensure_view(self.db,other);link_session(self.db,other,'one',scope='compatibility only')
        model=FakeModel();self.run_refresh(model)
        self.assertTrue(next(e for e in model.calls[0]['evidence_delta'] if e['session_id']=='one')['shared_session'])
        state=projection(self.db,other,[self.work],self.policy);self.assertGreater(state['pending_observations'],0)
        m=FakeModel();refresh(self.db,self.src,self.store,other,[self.work],self.policy,adapter=m)
        self.assertEqual({e['session_id'] for e in m.calls[0]['evidence_delta']},{'one'});self.assertEqual(m.calls[0]['evidence_delta'][0]['scope'],'compatibility only')
    def test_revision_race_rejects_result_and_preserves_pending_delta(self):
        def race(b):
            add_item(self.db,self.vid,self.section,2,'Concurrent human edit')
            return [operation('new','Progress',b['evidence_delta'][0]['id'])]
        with self.assertRaisesRegex(ModelError,'revision_conflict'):self.run_refresh(FakeModel(race))
        self.assertEqual(self.state()['items'],[]);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tracking_cursors').fetchone()[0],0)
    def test_source_change_during_model_and_missing_old_evidence_fail_closed(self):
        def race(b):
            self.f.writer.execute('UPDATE message_nodes SET chat_message=? WHERE session_id=? AND node_id=1',(message('b','changed','assistant'),'one'));self.f.writer.commit()
            return []
        with self.assertRaisesRegex(ModelError,'evidence_changed'):self.run_refresh(FakeModel(race))
        self.f.collect()
        with self.assertRaisesRegex(ModelError,'source_gap'):self.run_refresh(FakeModel())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tracking_cursors').fetchone()[0],0)
    def test_atomic_failure_rolls_back_items_history_and_cursors(self):
        self.db.execute("CREATE TRIGGER fail_tracking BEFORE INSERT ON tracking_history BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END");self.db.commit()
        with self.assertRaisesRegex(ModelError,'execution_failed'):self.run_refresh(FakeModel(self.scenario))
        self.assertEqual(self.state()['items'],[]);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tracking_cursors').fetchone()[0],0)
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
    def test_persistent_source_staleness_and_scope_never_expose_outside_workspace(self):
        self.run_refresh(FakeModel(self.scenario));self.append('changed')
        self.f.writer.execute('UPDATE message_nodes SET chat_message=? WHERE session_id=? AND node_id=1',(message('b','revised source','assistant'),'one'));self.f.writer.commit();self.f.collect()
        self.assertFalse(next(i for i in self.state()['items'] if i['key']=='implementation')['source_current'])
        outside=self.work.parent/'outside';outside.mkdir()
        hidden=projection(self.db,self.task,[outside],self.policy);self.assertEqual(hidden['items'],[])
    def test_structural_proposal_requires_approval_and_rechecks_source(self):
        self.run_refresh(FakeModel(lambda b:[operation('section','Investigation findings',b['evidence_delta'][0]['id'],op='propose_section')]))
        self.assertEqual(get_view(self.db,self.task),self.human);state=self.state();p=state['proposals'][0]
        resolve_pending(self.db,self.task,p['id'],state['revision'],True,self.src,[self.work])
        self.assertEqual([s['title'] for s in get_view(self.db,self.task)['sections']],['Inbox','Investigation findings'])
    def test_observed_user_text_blocks_exclude_injected_resources(self):
        raw={'message_id':'b','role':'user','content':'DO NOT SEND injected context from unrelated sessions','metadata':{'is_user_input':True,'extensions':{'chisel/acp-content-blocks':[{'type':'resource','resource':{'text':'private injected context'}},{'type':'text','text':'Explicit synthetic user question only'}]}}}
        self.f.writer.execute('UPDATE message_nodes SET chat_message=? WHERE session_id=? AND node_id=1',(json.dumps(raw),'one'));self.f.writer.commit()
        # This fixture has not had a semantic refresh: reset only its disposable observer to avoid historical revision gaps.
        self.db.execute('DELETE FROM observations WHERE session_id=?',('one',));self.db.execute('DELETE FROM source_nodes WHERE session_id=?',('one',));self.db.execute('DELETE FROM checkpoints WHERE session_id=?',('one',));self.db.commit();self.f.collect()
        model=FakeModel();self.run_refresh(model);text=json.dumps(model.calls[0]['evidence_delta'])
        self.assertIn('Explicit synthetic user question only',text);self.assertNotIn('private injected context',text);self.assertNotIn('DO NOT SEND',text)

    def test_interrupted_reservation_is_retained_and_explicit_retry_recovers(self):
        model=FakeModel();self.run_refresh(model)
        # Simulate atomic ledger reservation surviving a process crash before effects commit.
        self.db.execute("DELETE FROM tracking_cursors");self.db.execute("DELETE FROM tracking_processed")
        self.db.execute("UPDATE tracking_state SET revision=0,last_success=NULL")
        self.db.execute("UPDATE tracking_runs SET status='running',completed_at=NULL");self.db.commit()
        with self.assertRaisesRegex(ModelError,'explicit_retry'):self.run_refresh(model)
        self.assertEqual(self.db.execute('SELECT status FROM tracking_runs').fetchone()[0],'interrupted')
        result=self.run_refresh(model,retry=True);self.assertEqual(result['status'],'no_change')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tracking_runs WHERE reserved_tokens>0').fetchone()[0],2)

    def test_cross_session_status_without_provable_continuation_is_deferred(self):
        self.run_refresh(FakeModel(lambda b:[operation('blocked','Interface is blocked.',next(e['id'] for e in b['evidence_delta'] if e['session_id']=='one'),'blocker')]))
        item=self.state()['items'][0];self.append('Interface is resolved according to separate session.','two')
        self.run_refresh(FakeModel(lambda b:[operation('resolve','Resolution report',b['evidence_delta'][0]['id'],'blocker',op='update_status',key=item['key'],target_id=item['id'],status='resolved')]))
        self.assertEqual(self.state()['items'][0]['status'],'open');self.assertEqual(self.state()['proposals'][0]['status'],'pending')

    def test_approval_rechecks_evidence_and_does_not_apply_stale_proposal(self):
        self.run_refresh(FakeModel(lambda b:[operation('section','Proposed investigation section',b['evidence_delta'][0]['id'],op='propose_section')]))
        state=self.state();self.f.writer.execute('UPDATE message_nodes SET chat_message=? WHERE session_id=? AND node_id=1',(message('b','Changed','assistant'),'one'));self.f.writer.commit()
        with self.assertRaisesRegex(ModelError,'approval_evidence_unavailable'):resolve_pending(self.db,self.task,state['proposals'][0]['id'],state['revision'],True,self.src,[self.work])
        self.assertEqual(get_view(self.db,self.task),self.human)

    def test_conflict_with_human_decision_attaches_only_to_machine_claim(self):
        human_id=self.human['sections'][0]['items'][0]['id']
        model=FakeModel(lambda b:[operation('conflicting-decision','Decision: remove compatibility contrary to human decision.',b['evidence_delta'][0]['id'],'decision',conflicts_with=[human_id])])
        self.run_refresh(model)
        self.assertEqual(self.state()['items'][0]['conflicts'],[human_id]);self.assertEqual(get_view(self.db,self.task),self.human)

    def test_unsupported_natural_language_kind_is_durable_partial_coverage(self):
        self.f.writer.execute('INSERT INTO message_nodes VALUES (?,?,?,?,?)',(101,'one',2,1,message('unclassified','Unknown user body','user')))
        self.f.writer.execute('UPDATE sessions SET main_chain_id=2 WHERE id=?',('one',));self.f.writer.commit();self.f.collect()
        self.run_refresh();self.assertEqual(self.state()['freshness'],'partial');self.assertEqual(self.state()['unsupported_observations'],1)
        self.assertEqual(self.run_refresh()['status'],'unchanged');self.assertEqual(self.state()['freshness'],'partial')

    def test_company_policy_fails_closed_and_no_provider_fallback(self):
        with self.assertRaisesRegex(ValueError,'internal_requires'):validate_policy({**self.policy,'environment':'internal'})
        with self.assertRaisesRegex(ValueError,'approval_required'):validate_policy({**self.policy,'adapter':'devin_acp'})
        self.assertEqual(validate_policy({**self.policy,'environment':'internal','adapter':'devin_acp','acp_approved':True})['adapter'],'devin_acp')
    def test_concurrent_refresh_uses_one_lock_and_does_not_double_reserve(self):
        import fcntl,os
        fd=os.open(str(self.store.resolve())+'.tracking.lock',os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ModelError,'semantic_busy'):self.run_refresh()
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tracking_runs').fetchone()[0],0)
        finally:os.close(fd)

if __name__=='__main__':unittest.main()
