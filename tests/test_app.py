import json
import os
import select
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
from observer.app import load_config,save_config,validate_config,launch_agent,status,main,LABEL
from observer.store import open_store
from observer.registry import create_task
from observer.views import ensure_view,add_item


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.work=self.root/'personal';self.work.mkdir();(self.root/'source').mkdir()
        self.source=self.root/'source/source.db';self.store=self.root/'observer.db';self.config=self.root/'config.json'
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        self.value={'version':1,'workspaces':[str(self.work)],'source':str(self.source),'store':str(self.store),'port':port}
    def tearDown(self):self.tmp.cleanup()
    def test_configuration_is_private_validated_and_never_uses_source_as_store(self):
        save_config(self.config,self.value);self.assertEqual(load_config(self.config),self.value);self.assertEqual(self.config.stat().st_mode&0o777,0o600)
        for bad in ({**self.value,'workspaces':[]},{**self.value,'port':False},{**self.value,'store':str(self.source)}):
            with self.assertRaises(ValueError):save_config(self.config,bad)
        self.assertEqual(load_config(self.config),self.value)
        self.config.write_text('{')
        with self.assertRaisesRegex(ValueError,'configure'):load_config(self.config)
    def test_autostart_is_an_explicit_separate_action_with_fixed_local_entry(self):
        save_config(self.config,self.value);definition=launch_agent(self.config)
        self.assertEqual(definition['ProgramArguments'][-1],'run');self.assertEqual(definition['ProgramArguments'][-2],str(self.config))
        self.assertTrue(definition['KeepAlive']);self.assertTrue(definition['RunAtLoad'])
        self.assertEqual(list(self.root.glob('*.plist')),[])
        self.assertEqual(Path(definition['StandardOutPath']).stat().st_mode&0o777,0o600)
    def test_autostart_can_be_removed_even_when_configuration_is_missing(self):
        agent=self.root/'Library/LaunchAgents'/f'{LABEL}.plist';agent.parent.mkdir(parents=True);agent.write_text('synthetic fixture')
        with patch('observer.app.Path.home',return_value=self.root),patch('observer.app.sys.platform','darwin'),patch('observer.app.subprocess.run',return_value=SimpleNamespace(returncode=3)):
            self.assertEqual(main(['--config',str(self.config),'autostart','remove']),0)
        self.assertFalse(agent.exists())
    def test_process_restart_preserves_tasks_views_and_checkpoint_and_terminates_safely(self):
        d=sqlite3.connect(self.source);d.executescript('CREATE TABLE refinery_schema_history(version INTEGER);INSERT INTO refinery_schema_history VALUES(17);CREATE TABLE sessions(id TEXT,title TEXT,working_directory TEXT,main_chain_id INTEGER);CREATE TABLE message_nodes(session_id TEXT,node_id INTEGER,parent_node_id INTEGER,chat_message TEXT);')
        d.execute('INSERT INTO sessions VALUES (?,?,?,?)',('synthetic','Synthetic',str(self.work),0));d.execute('INSERT INTO message_nodes VALUES (?,?,?,?)',('synthetic',0,None,json.dumps({'message_id':'m0','role':'user','content':'synthetic only'})));d.commit();d.close()
        save_config(self.config,self.value)
        for iteration in range(2):
            process=subprocess.Popen([sys.executable,'-m','observer.app','--config',str(self.config),'run'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.assertTrue(select.select([process.stdout],[],[],8)[0]);self.assertIn('Dashboard listening',process.stdout.readline())
                deadline=time.monotonic()+8
                while time.monotonic()<deadline and status(self.value)['last_success'] is None:time.sleep(.05)
                self.assertIsNotNone(status(self.value)['last_success']);self.assertTrue(status(self.value)['running'])
                if iteration==0:
                    duplicate=subprocess.run([sys.executable,'-m','observer.app','--config',str(self.config),'run'],capture_output=True,text=True,timeout=5)
                    self.assertEqual(duplicate.returncode,2);self.assertIn('이미 실행 중',duplicate.stderr)
                db=open_store(self.store,self.source)
                if iteration==0:
                    self.task=create_task(db,'Retained task');view=ensure_view(db,self.task)
                    section=db.execute('SELECT id FROM view_sections WHERE view_id=?',(view,)).fetchone()[0];add_item(db,view,section,1,'Retained human note',True)
                else:
                    self.assertEqual(db.execute('SELECT content,pinned FROM view_items').fetchone(),('Retained human note',1))
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],1)
                db.close()
            finally:
                process.send_signal(signal.SIGTERM);out,err=process.communicate(timeout=8)
                self.assertEqual(process.returncode,0,err)
                self.assertNotIn('Traceback',err);self.assertFalse(status(self.value)['running'])
        d=sqlite3.connect(self.store);self.assertEqual(d.execute('PRAGMA integrity_check').fetchone()[0],'ok');d.close()
