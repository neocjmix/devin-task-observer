import sqlite3
import tempfile
import unittest
from pathlib import Path
from observer.registry import add_resource, task_resources, create_task, update_task, link_session, list_tasks, task_sessions, session_tasks, RevisionConflict
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


if __name__ == '__main__': unittest.main()
