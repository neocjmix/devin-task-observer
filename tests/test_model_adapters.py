"""Protocol/process fault tests use executable synthetic peers, never live accounts."""
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from observer.model_adapters import CodexExecAdapter,DevinAcpAdapter,ModelError,Process,strict_json,observed_acp_usage


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.auth=self.root/'auth';self.auth.mkdir();(self.auth/'auth.json').write_text('{"synthetic":true}')
        self.bundle={'task_id':'synthetic','view_revision':0,'view_contract':{},'affected_items':[],'evidence_delta':[],
                     'limits':{'input_bytes':48000,'output_bytes':24000,'timeout_seconds':2}}
    def tearDown(self):self.tmp.cleanup()
    def executable(self,body):
        path=self.root/'synthetic-peer';path.write_text('#!'+sys.executable+'\n'+body);path.chmod(0o700);return str(path)
    def codex(self,mode='ok'):
        body='''import sys,json,time,os
if '--help' in sys.argv:
 print('--output-schema --json --ephemeral --ignore-user-config --ignore-rules --sandbox');sys.exit(0)
mode=__MODE__
text=sys.stdin.read();bundle=json.loads(text.split('INPUT JSON:\\n',1)[1])
assert '--sandbox' in sys.argv and sys.argv[sys.argv.index('--sandbox')+1]=='read-only'
assert os.getcwd().startswith('/private/var/') or 'observer-codex-' in os.getcwd()
assert '--ignore-user-config' in sys.argv and '--ignore-rules' in sys.argv and '--ephemeral' in sys.argv
print(json.dumps({'type':'thread.started','thread_id':'synthetic-thread'}),flush=True)
if mode=='timeout':time.sleep(20)
if mode=='nonzero':sys.exit(7)
if mode=='tool':
 print(json.dumps({'type':'item.started','item':{'type':'command_execution'}}),flush=True);time.sleep(20)
if mode=='overflow':print('x'*1100000,flush=True);time.sleep(20)
proposal={'version':1,'task_id':bundle['task_id'],'view_revision':bundle['view_revision'],'operations':[]}
path=sys.argv[sys.argv.index('--output-last-message')+1]
if mode!='missing':open(path,'w').write('{bad' if mode=='invalid' else json.dumps(proposal))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':77,'output_tokens':31}}),flush=True)
'''.replace('__MODE__',repr(mode))
        return self.executable(body)
    def test_codex_schema_usage_isolation_and_no_bundled_runtime(self):
        exe=self.codex()
        with patch('observer.model_adapters.sys.platform','linux'),patch.dict(os.environ,{'CODEX_HOME':str(self.auth)}):
            result=CodexExecAdapter(exe).analyze(self.bundle)
        self.assertEqual(result.proposal['operations'],[]);self.assertEqual(result.usage['input_tokens'],77);self.assertEqual(result.provider_run_id,'synthetic-thread')
        self.assertEqual(list(self.root.glob('observer-codex-*')),[])
    def test_codex_faults_fail_closed_and_bound_execution(self):
        for mode,expected in [('timeout','timeout'),('nonzero','nonzero_exit'),('tool','tool_use_denied'),('overflow','output_limit'),('invalid','invalid_json'),('missing','output_artifact')]:
            with self.subTest(mode=mode),patch('observer.model_adapters.sys.platform','linux'),patch.dict(os.environ,{'CODEX_HOME':str(self.auth)}):
                start=time.monotonic()
                with self.assertRaisesRegex(ModelError,expected) as caught:CodexExecAdapter(self.codex(mode)).analyze(self.bundle)
                if mode in ('invalid','missing'):self.assertEqual(caught.exception.usage['input_tokens'],77)
                self.assertLess(time.monotonic()-start,4)
    def test_codex_unsupported_flags_and_auth_fail_before_prompt(self):
        exe=self.executable('print("old unsupported CLI")')
        with self.assertRaisesRegex(ModelError,'unsupported_options'):CodexExecAdapter(exe).analyze(self.bundle)
        exe=self.codex()
        with patch.dict(os.environ,{'CODEX_HOME':str(self.root/'no-auth')}):
            with self.assertRaisesRegex(ModelError,'auth_required'):CodexExecAdapter(exe).analyze(self.bundle)
    def acp(self,mode='ok'):
        log=self.root/'methods.log'
        body='''import sys,json,time,os
mode=__MODE__
log=LOG
assert os.environ.get('DEVIN_PERMISSION_MODE')=='auto'
session='new-analysis-'+str(os.getpid())
def emit(value):print(json.dumps(value),flush=True)
for line in sys.stdin:
 e=json.loads(line);method=e.get('method');open(log,'a').write(str(method)+'\\n')
 if method=='initialize':
  assert e['params']['clientCapabilities']['terminal'] is False
  emit({'jsonrpc':'2.0','id':e['id'],'result':{'protocolVersion':2 if mode=='version' else 1,'agentCapabilities':{}}})
 elif method=='session/new':
  assert e['params']['mcpServers']==[] and 'observer-acp-' in e['params']['cwd']
  if mode=='auth':emit({'jsonrpc':'2.0','id':e['id'],'error':{'code':-32000,'message':'synthetic auth error'}})
  else:
   if mode=='initial':emit({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':session,'update':{'sessionUpdate':'session_info_update'}}})
   emit({'jsonrpc':'2.0','id':e['id'],'result':{'sessionId':session}})
 elif method=='session/prompt':
  assert e['params']['sessionId']==session
  if mode=='disconnect':sys.exit(0)
  if mode=='timeout':time.sleep(20)
  if mode=='permission':
   emit({'jsonrpc':'2.0','id':'denied','method':'session/request_permission','params':{'sessionId':session,'options':[]}});continue
  if mode=='filesystem':
   emit({'jsonrpc':'2.0','id':'denied','method':'fs/write_text_file','params':{'sessionId':session}});continue
  if mode=='tool':
   emit({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':session,'update':{'sessionUpdate':'tool_call','title':'synthetic'}}});continue
  text='not json' if mode=='invalid' else json.dumps({'version':1,'task_id':'synthetic','view_revision':0,'operations':[]})
  if mode=='overflow':text='x'*25000
  for chunk in [text[:20],text[20:]]:
   emit({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'wrong' if mode=='wrong' else session,'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':chunk}}}})
  emit({'jsonrpc':'2.0','id':e['id'],'result':{'stopReason':'end_turn'}})
 elif 'id' in e and 'method' not in e:
  open(log,'a').write('permission_response:'+json.dumps(e)+'\\n')
'''.replace('__MODE__',repr(mode)).replace('LOG',repr(str(log)))
        return self.executable(body),log
    def test_acp_new_session_streaming_unknown_usage_and_no_resume(self):
        exe,log=self.acp('initial');result=DevinAcpAdapter(exe).analyze(self.bundle)
        self.assertEqual(result.proposal['operations'],[]);self.assertIsNone(result.usage['input_tokens']);self.assertIsNone(result.usage['output_tokens'])
        methods=log.read_text();self.assertIn('session/new',methods);self.assertIn('session/prompt',methods);self.assertNotIn('session/load',methods);self.assertNotIn('session/resume',methods)
    def test_acp_protocol_permission_tool_auth_disconnect_and_json_failures(self):
        for mode,expected in [('version','unsupported_protocol'),('permission','permission_denied'),('filesystem','client_tool_denied'),('tool','tool_use_denied'),('auth','auth_or_request_failed'),('disconnect','disconnected'),('timeout','timeout'),('wrong','wrong_session'),('invalid','invalid_json'),('overflow','output_limit')]:
            with self.subTest(mode=mode):
                exe,_=self.acp(mode)
                with self.assertRaisesRegex(ModelError,expected):DevinAcpAdapter(exe).analyze(self.bundle)
    def test_cancellation_and_independent_concurrent_analysis_sessions(self):
        exe,_=self.acp('timeout');cancel=threading.Event();timer=threading.Timer(.15,cancel.set);timer.start()
        try:
            with self.assertRaisesRegex(ModelError,'cancelled'):DevinAcpAdapter(exe).analyze(self.bundle,cancel)
        finally:timer.cancel()
        exe,_=self.acp();results=[]
        ts=[threading.Thread(target=lambda:results.append(DevinAcpAdapter(exe).analyze(self.bundle).provider_run_id)) for _ in range(2)]
        for t in ts:t.start()
        for t in ts:t.join(timeout=5)
        self.assertEqual(len(results),2);self.assertEqual(len(set(results)),2)
    def test_guardian_retains_lock_and_stops_provider_after_observer_crash(self):
        import subprocess,fcntl
        lock=self.root/'crash.lock'
        code="""import sys,os,fcntl
sys.path.insert(0,sys.argv[1])
from observer.model_adapters import Process,ANALYSIS_LOCK
fd=os.open(sys.argv[2],os.O_CREAT|os.O_RDWR,0o600);fcntl.flock(fd,fcntl.LOCK_EX)
ANALYSIS_LOCK.set(fd)
p=Process([sys.executable,'-c','import time;time.sleep(30)'],sys.argv[3],os.environ.copy(),.8)
print(p.p.pid,flush=True)
os._exit(0)
"""
        parent=subprocess.run([sys.executable,'-c',code,str(Path(__file__).resolve().parent.parent),str(lock),str(self.root)],capture_output=True,text=True,timeout=3)
        self.assertEqual(parent.returncode,0);self.assertTrue(parent.stdout.strip().isdigit())
        fd=os.open(lock,os.O_RDWR)
        try:
            with self.assertRaises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:time.sleep(.05)
            else:self.fail('deadline guardian left provider running/lock held')
        finally:os.close(fd)

    def test_acp_observed_token_usage_and_unavailable_or_invalid_shapes(self):
        usage=observed_acp_usage({'usage':{'inputTokens':77,'outputTokens':31,'totalTokens':108,'cachedReadTokens':12}})
        self.assertEqual(usage['input_tokens'],77);self.assertEqual(usage['output_tokens'],31);self.assertEqual(usage['cached_read_tokens'],12)
        for raw in (None,{}, {'inputTokens':True,'outputTokens':2}, {'inputTokens':3,'outputTokens':-1}, {'inputTokens':3,'outputTokens':2,'totalTokens':4}):
            result=observed_acp_usage({'usage':raw});self.assertIsNone(result['input_tokens']);self.assertIsNone(result['output_tokens'])

    def test_strict_json_duplicate_nonfinite_and_input_limits(self):
        for text in ['{"version":1,"version":2}','{"value":NaN}','[]garbage']:
            with self.assertRaises(ModelError):strict_json(text)
        bundle={**self.bundle,'evidence_delta':['x'*50000]}
        with self.assertRaisesRegex(ModelError,'input_limit'):DevinAcpAdapter('/does/not/exist').analyze(bundle)

if __name__=='__main__':unittest.main()
