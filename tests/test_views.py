import tempfile
import unittest
from pathlib import Path
from observer.registry import create_task, link_session, RevisionConflict
from observer.store import open_store
from observer.views import ensure_view, get_view, add_section, rename_section, reorder_sections, add_item, edit_item, attach_evidence, move_item


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); root=Path(self.tmp.name)
        self.db=open_store(root/'observer.db',root/'source'/'sessions.db')
        self.db.execute("INSERT INTO sessions VALUES ('s1','synthetic','/test',1,'current',1)")
        self.db.execute("INSERT INTO event_evidence VALUES ('s1','m1','user','',1,'hash')")
        self.db.commit(); self.task=create_task(self.db,'Synthetic task');self.vid=ensure_view(self.db,self.task)

    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def test_custom_sections_pinned_content_and_history_survive_edits(self):
        view=get_view(self.db,self.task); inbox=view['sections'][0]['id']
        self.assertEqual(ensure_view(self.db,self.task),self.vid)
        section,rev=add_section(self.db,self.vid,1,'Decisions')
        item,rev=add_item(self.db,self.vid,section,rev,'Human decision',True)
        rev=edit_item(self.db,self.vid,item,rev,content='Human revised decision')
        rev=rename_section(self.db,self.vid,section,rev,'Notes')
        view=get_view(self.db,self.task)
        self.assertEqual([x['title'] for x in view['sections']], ['Inbox','Notes'])
        self.assertEqual(view['sections'][0]['id'],inbox)
        self.assertEqual(view['sections'][1]['items'][0]['id'],item)
        self.assertTrue(view['sections'][1]['items'][0]['pinned'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM view_item_history WHERE item_id=?',(item,)).fetchone()[0],2)
        with self.assertRaises(RevisionConflict): edit_item(self.db,self.vid,item,1,content='overwrite')
        self.assertEqual(get_view(self.db,self.task)['sections'][1]['items'][0]['content'],'Human revised decision')

    def test_only_linked_known_evidence_can_be_attached(self):
        inbox=get_view(self.db,self.task)['sections'][0]['id']
        item,rev=add_item(self.db,self.vid,inbox,1,'Finding')
        with self.assertRaises(ValueError): attach_evidence(self.db,self.vid,item,rev,'s1',1,'hash')
        self.assertEqual(get_view(self.db,self.task)['revision'],rev)
        link_session(self.db,self.task,'s1')
        with self.assertRaises(ValueError): attach_evidence(self.db,self.vid,item,rev,'s1',1,'wrong')
        rev=attach_evidence(self.db,self.vid,item,rev,'s1',1,'hash')
        self.assertEqual(get_view(self.db,self.task)['sections'][0]['items'][0]['evidence'][0]['content_hash'],'hash')
        self.assertEqual(get_view(self.db,self.task)['revision'],rev)

    def test_failed_write_rolls_back_revision(self):
        with self.assertRaises(KeyError): add_item(self.db,self.vid,'missing',1,'text')
        self.assertEqual(get_view(self.db,self.task)['revision'],1)
        with self.assertRaises(ValueError): add_section(self.db,self.vid,1,'   ')
        self.assertEqual(get_view(self.db,self.task)['revision'],1)

    def test_reorder_sections_requires_complete_order_and_preserves_ids(self):
        first=get_view(self.db,self.task)['sections'][0]['id']
        second,rev=add_section(self.db,self.vid,1,'Second')
        with self.assertRaises(ValueError): reorder_sections(self.db,self.vid,rev,[first,first])
        self.assertEqual(get_view(self.db,self.task)['revision'],rev)
        rev=reorder_sections(self.db,self.vid,rev,[second,first])
        self.assertEqual([s['id'] for s in get_view(self.db,self.task)['sections']],[second,first])
        self.assertEqual(get_view(self.db,self.task)['revision'],rev)

    def test_move_and_reorder_items_preserves_evidence_and_pins(self):
        inbox=get_view(self.db,self.task)['sections'][0]['id'];second,rev=add_section(self.db,self.vid,1,'Decisions')
        first,rev=add_item(self.db,self.vid,inbox,rev,'First',True);other,rev=add_item(self.db,self.vid,inbox,rev,'Second')
        target,rev=add_item(self.db,self.vid,second,rev,'Existing')
        link_session(self.db,self.task,'s1');rev=attach_evidence(self.db,self.vid,first,rev,'s1',1,'hash')
        with self.assertRaises(ValueError):move_item(self.db,self.vid,first,second,9,rev)
        with self.assertRaises(RevisionConflict):move_item(self.db,self.vid,first,second,0,rev-1)
        rev=move_item(self.db,self.vid,first,second,0,rev)
        view=get_view(self.db,self.task);self.assertEqual([x['id'] for x in view['sections'][0]['items']],[other])
        self.assertEqual([x['id'] for x in view['sections'][1]['items']],[first,target])
        moved=view['sections'][1]['items'][0];self.assertTrue(moved['pinned']);self.assertEqual(moved['evidence'][0]['content_hash'],'hash')
        rev=move_item(self.db,self.vid,first,second,1,rev)
        self.assertEqual([x['id'] for x in get_view(self.db,self.task)['sections'][1]['items']],[target,first])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM view_item_history WHERE item_id=?',(first,)).fetchone()[0],1)


if __name__=='__main__': unittest.main()
