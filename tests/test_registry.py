import sqlite3
import tempfile
import unittest
from pathlib import Path
from observer.registry import add_resource, task_resources, create_task, update_task, link_session, list_tasks, task_sessions, session_tasks, RevisionConflict, set_link_active, update_resource, update_contribution
from observer.store import open_store


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = open_store(root / 'observer.db', root / 'source' / 'source.db')
        self.db.executemany('INSERT INTO sessions VALUES(?,?,?,?,?,?)', [('s1','synthetic 1','/personal',1,'current',1),('s2','synthetic 2','/personal',2,'stale',1)])
        self.db.commit()

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def test_many_to_many_links_and_idempotence(self):
        first = create_task(self.db,'First','human note')
        second = create_task(self.db,'Second')
        link = link_session(self.db,first,'s1',role='research')
        self.assertEqual(link,link_session(self.db,first,'s1',role='research'))
        link_session(self.db,first,'s2')
        link_session(self.db,second,'s1')
        self.assertEqual({x['session_id'] for x in task_sessions(self.db,first)}, {'s1','s2'})
        self.assertEqual({x['task_id'] for x in session_tasks(self.db,'s1')}, {first,second})
        self.assertEqual(next(x['status'] for x in task_sessions(self.db,first) if x['session_id']=='s2'), 'stale')
        with self.assertRaises(ValueError): link_session(self.db,first,'s1',role='different')
        self.assertEqual(next(x for x in task_sessions(self.db,first) if x['session_id']=='s1')['role'],'research')

    def test_unknown_task_or_session_cannot_be_linked(self):
        task = create_task(self.db,'Personal')
        with self.assertRaises(sqlite3.IntegrityError): link_session(self.db,task,'missing')
        with self.assertRaises(sqlite3.IntegrityError): link_session(self.db,'missing','s1')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM contributions').fetchone()[0],0)

    def test_revision_protects_user_edits(self):
        task = create_task(self.db,'First','original')
        self.assertEqual(update_task(self.db,task,1,description='edited by human'),2)
        with self.assertRaises(RevisionConflict): update_task(self.db,task,1,title='stale overwrite')
        item = list_tasks(self.db)[0]
        self.assertEqual((item['title'],item['description'],item['revision']),('First','edited by human',2))
        self.assertEqual(update_task(self.db,task,2,lifecycle='completed'),3)
        self.assertEqual(update_task(self.db,task,3,lifecycle='completed'),3)

    def test_invalid_input_is_rejected_without_write(self):
        with self.assertRaises(ValueError): create_task(self.db,'   ')
        task = create_task(self.db,'Task')
        with self.assertRaises(ValueError): link_session(self.db,task,'s1',confidence=2)
        with self.assertRaises(ValueError): update_task(self.db,task,1,lifecycle='invented')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM contributions').fetchone()[0],0)

    def test_manual_resources_are_unverified_and_reject_unsafe_uri(self):
        task=create_task(self.db,'Task')
        add_resource(self.db,task,'pr','https://example.com/pull/1')
        add_resource(self.db,task,'repo','/tmp/synthetic')
        resources=task_resources(self.db,task)
        self.assertEqual(len(resources),2)
        self.assertTrue(all(x['external_status']=='unknown' and x['verified_at'] is None for x in resources))
        for uri in ('javascript:alert(1)','https://user:secret@example.com/','//example.com'):
            with self.assertRaises(ValueError):add_resource(self.db,task,'other',uri)

    def test_unlink_and_restore_keep_other_links_and_history(self):
        task=create_task(self.db,'Task');other=create_task(self.db,'Other')
        link=link_session(self.db,task,'s1',role='research');link_session(self.db,other,'s1')
        self.assertEqual(set_link_active(self.db,task,link,1,False),2)
        self.assertEqual(task_sessions(self.db,task),[])
        self.assertEqual([x['task_id'] for x in session_tasks(self.db,'s1')],[other])
        self.assertEqual(task_sessions(self.db,task,True)[0]['active'],0)
        with self.assertRaises(RevisionConflict):set_link_active(self.db,task,link,1,True)
        with self.assertRaises(ValueError):link_session(self.db,task,'s1',role='research')
        self.assertEqual(set_link_active(self.db,task,link,2,True),3)
        self.assertEqual(task_sessions(self.db,task)[0]['role'],'research')
        self.assertEqual(self.db.execute('SELECT active,revision FROM contribution_history WHERE contribution_id=? ORDER BY id',(link,)).fetchall(),[(1,1),(0,2),(1,3)])

    def test_contribution_metadata_edit_preserves_history_and_rejects_stale(self):
        task=create_task(self.db,'Task');link=link_session(self.db,task,'s1',role='research',confidence=.5)
        self.assertEqual(update_contribution(self.db,task,link,1,role='implementation',scope='prototype'),2)
        with self.assertRaises(RevisionConflict):update_contribution(self.db,task,link,1,role='stale')
        with self.assertRaises(ValueError):update_contribution(self.db,task,link,2,confidence=2)
        self.assertEqual(update_contribution(self.db,task,link,2,confidence=None),3)
        saved=task_sessions(self.db,task)[0]
        self.assertEqual((saved['role'],saved['scope'],saved['confidence']),('implementation','prototype',None))
        history=self.db.execute('SELECT role,scope,confidence,revision FROM contribution_metadata_history WHERE contribution_id=? ORDER BY id',(link,)).fetchall()
        self.assertEqual(history,[('research','',.5,1),('implementation','prototype',.5,2),('implementation','prototype',None,3)])
        set_link_active(self.db,task,link,3,False)
        with self.assertRaises(ValueError):update_contribution(self.db,task,link,4,scope='not allowed')

    def test_resource_edit_archive_restore_conflict_and_history(self):
        task=create_task(self.db,'Task');rid=add_resource(self.db,task,'pr','https://example.com/old')
        self.assertEqual(update_resource(self.db,task,rid,1,kind='ticket',uri='https://example.com/new'),2)
        with self.assertRaises(RevisionConflict):update_resource(self.db,task,rid,1,uri='https://example.com/stale')
        with self.assertRaises(ValueError):update_resource(self.db,task,rid,2,uri='javascript:alert(1)')
        self.assertEqual(update_resource(self.db,task,rid,2,active=False),3)
        self.assertEqual(task_resources(self.db,task),[])
        self.assertEqual(update_resource(self.db,task,rid,3,active=True),4)
        self.assertEqual(task_resources(self.db,task)[0]['uri'],'https://example.com/new')
        self.assertEqual(self.db.execute('SELECT uri,active,revision FROM resource_history WHERE resource_id=? ORDER BY id',(rid,)).fetchall(),[('https://example.com/old',1,1),('https://example.com/new',1,2),('https://example.com/new',0,3),('https://example.com/new',1,4)])

    def test_preexisting_records_receive_history_when_changed(self):
        task=create_task(self.db,'Legacy')
        with self.db:
            self.db.execute('INSERT INTO contributions VALUES (?,?,?,?,?,?,?,?)',('old-link',task,'s1','','','human',None,1))
            self.db.execute('INSERT INTO resources VALUES (?,?,?,?,?,?,?)',('old-resource',task,'pr','https://example.com/old','unknown',None,1))
        self.assertEqual(set_link_active(self.db,task,'old-link',1,False),2)
        self.assertEqual(update_resource(self.db,task,'old-resource',1,uri='https://example.com/new'),2)
        self.assertEqual(self.db.execute('SELECT active,revision FROM contribution_history WHERE contribution_id=? ORDER BY id',('old-link',)).fetchall(),[(1,1),(0,2)])
        self.assertEqual(self.db.execute('SELECT uri,revision FROM resource_history WHERE resource_id=? ORDER BY id',('old-resource',)).fetchall(),[('https://example.com/old',1),('https://example.com/new',2)])


if __name__ == '__main__': unittest.main()
