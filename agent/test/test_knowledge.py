"""Real parser/index/version regression for the current single workspace."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pymupdf
from fastapi import FastAPI
from fastapi.testclient import TestClient
from docker.backend.chat_store import ChatStore
from docker.backend.single_workspace import SingleWorkspace
from docker.backend.knowledge import parse_pdf
from docker.backend.v2 import create_router
from agent.test.fixtures import Embeddings, Model, pdf_bytes


class KnowledgeTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.sources=self.root/'raw';self.sources.mkdir()
        self.store=ChatStore(self.root/'chat.sqlite3')
        self.p=SingleWorkspace(self.store,Embeddings(),self.root,source_dir=self.sources)
        self.p.knowledge.rerank=False
        app=FastAPI();app.middleware('http')(self.p.middleware);app.include_router(self.p.router())
        app.include_router(create_router(type('Agent',(),{'clear_history':lambda *a:None})(),store=self.store,platform=self.p,llm_factory=lambda **kw:Model()))
        self.client=TestClient(app)

    def tearDown(self):
        self.client.close();self.store.close();self.temp.cleanup()

    def add_pdf(self,content,name):
        (self.sources/name).write_bytes(content);self.p.sync_sources()
        doc=self.p.one('SELECT * FROM kb_documents WHERE name=? AND deleted=0',(name,))
        for job in self.p.rows("SELECT id FROM kb_jobs WHERE doc_id=? AND state='queued'",(doc['id'],)):self.p.knowledge.process(job['id'])
        return self.p.one('SELECT * FROM kb_documents WHERE id=?',(doc['id'],))

    def upload(self,value=125,table=False):
        return self.add_pdf(pdf_bytes(value,table=table),'fixture-'+str(len(self.p.catalog_documents()))+'.pdf')

    def pin(self,scope=None):return self.p.knowledge.pin(self.p.user(),scope or {})

    def chat(self,scope=None):
        import uuid
        session=self.client.post('/api/v2/sessions').json()
        response=self.client.post('/api/v2/chat',json={'session_id':session['id'],'request_id':str(uuid.uuid4()),'query':'Revenue?','scope':scope or {}})
        self.assertEqual(response.status_code,200,response.text)
        return session,[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]

    def test_failed_candidate_preserves_published_and_pinned_pdf_versions(self):
        old=self.upload();pinned=self.pin()
        (self.sources/old['name']).write_bytes(pdf_bytes(275));self.p.sync_sources()
        job=self.p.one("SELECT id FROM kb_jobs WHERE doc_id=? AND state='queued'",(old['id'],))
        with patch.object(self.p.knowledge.embeddings,'embed_documents',side_effect=RuntimeError('private fixture detail')):
            self.p.knowledge.process(job['id'])
        current=self.p.document(old['id'])
        self.assertEqual(current['version'],old['version']);self.assertEqual(current['active_index'],old['active_index'])
        self.assertNotIn('private fixture detail',current['error'])
        self.assertIn('125',pinned.chunks[0]['content']);self.assertIn('125',self.pin().chunks[0]['content'])
        self.assertEqual(self.client.get(f"/api/v2/documents/{old['id']}/pdf?version={old['version']}").content,pdf_bytes(125))

    def test_worker_resumes_interrupted_job_without_duplicate_publication(self):
        import time
        (self.sources/'restart.pdf').write_bytes(pdf_bytes());self.p.sync_sources()
        job=self.p.one("SELECT id FROM kb_jobs WHERE state='queued'")
        self.p.execute("UPDATE kb_jobs SET state='parsing' WHERE id=?",(job['id'],))
        self.p.knowledge.start()
        try:
            for _ in range(100):
                if self.p.one('SELECT state FROM kb_jobs WHERE id=?',(job['id'],))['state']=='ready':break
                time.sleep(.02)
        finally:
            self.p.knowledge.stop.set();self.p.knowledge.wake.set();self.p.knowledge.thread.join(timeout=3)
        self.assertEqual(self.p.catalog()['ready_count'],1)
        self.p.sync_sources();self.assertEqual(self.p.one('SELECT count(*) n FROM kb_jobs')['n'],1)

    def test_delete_during_embedding_never_resurrects_or_leaves_bm25(self):
        d=self.upload();job=self.p.knowledge.enqueue(d)
        original=self.p.knowledge.embeddings.embed_documents
        def deleting(texts):
            self.p.execute("UPDATE kb_documents SET deleted=1,generation=generation+1 WHERE id=?",(d['id'],));return original(texts)
        with patch.object(self.p.knowledge.embeddings,'embed_documents',side_effect=deleting):self.p.knowledge.process(job)
        self.assertEqual(self.p.one('SELECT state FROM kb_jobs WHERE id=?',(job,))['state'],'cancelled')
        self.assertEqual(self.pin().search('125'),[])


    def test_real_parser_table_units_geometry_rotated_and_invalid_pdf(self):
        d=self.upload(table=True);snap=self.pin();tables=[c for c in snap.chunks if c['table']]
        self.assertTrue(tables);self.assertIn('125',json.dumps(tables[0]['table']['rows']));self.assertIn('USD m',json.dumps(tables[0]['table']['headers']))
        for c in snap.chunks:
            g=c['geometry'];self.assertEqual(g['origin'],'top-left');self.assertEqual(g['unit'],'pt')
            for x0,y0,x1,y1 in c['regions']:self.assertTrue(0<=x0<x1<=g['width'] and 0<=y0<y1<=g['height'])
        path=self.root/'rotated.pdf';path.write_bytes(pdf_bytes(rotated=True));items=parse_pdf(path,{'id':'rot','version':'fixture','name':'rot.pdf'})
        self.assertEqual(items[0]['geometry']['rotation'],90)
        cropped=pymupdf.open(stream=pdf_bytes(),filetype='pdf');cropped[0].set_cropbox(pymupdf.Rect(20,30,480,520));cropped[0].set_rotation(90)
        cropped.save(self.root/'crop.pdf');cropped.close()
        crop_items=parse_pdf(self.root/'crop.pdf',{'id':'crop','version':'fixture','name':'crop.pdf'})
        geometry=crop_items[0]['geometry'];self.assertEqual(geometry['rotation'],90);self.assertEqual(geometry['cropbox'],[20,30,480,520])
        matrix=pymupdf.Matrix(*geometry['transformation_matrix']);inverse=~matrix
        point=pymupdf.Point(*crop_items[0]['regions'][0][:2]);self.assertLess(abs(((point*inverse)*matrix).x-point.x),.001)
        bad=self.root/'bad.pdf';bad.write_bytes(b'%PDF-corrupt')
        with self.assertRaises(Exception):parse_pdf(bad,{'id':'bad','version':'fixture','name':'bad.pdf'})


    def test_borderless_year_table_keeps_units_headers_and_right_column_separate(self):
        from docker.backend.knowledge import aligned_year_tables
        pdf=pymupdf.open();page=pdf.new_page(width=600,height=600)
        for x,text in [(40,'Forecast'),(200,'2021A'),(280,'2022A'),(360,'2023E')]:page.insert_text((x,60),text)
        for y,label,values in [(85,'Revenue (USD m)',['125','275','400']),(110,'Profit (USD m)',['10','20','30'])]:
            page.insert_text((40,y),label)
            for x,v in zip((200,280,360),values):page.insert_text((x,y),v)
        page.insert_text((470,95),'Independent analyst column')
        tables=aligned_year_tables(page);self.assertEqual(len(tables),1)
        rows=tables[0][2]['rows'];self.assertEqual(rows[0][1:],['2021A','2022A','2023E']);self.assertEqual(rows[1],['Revenue (USD m)','125','275','400'])
        self.assertNotIn('analyst',tables[0][0]);self.assertEqual(len(tables[0][2]['cells']),12)
        uploaded=self.add_pdf(pdf.tobytes(no_new_id=True),'borderless.pdf')

        snapshot=self.pin({'doc_ids':[uploaded['id']]});found=snapshot.search('Revenue 2021')
        self.assertTrue(any(c['table'] and '125' in c['content'] and '2021A' in c['content'] for c in found))
        self.assertFalse(any(not c['table'] and '125' in c['content'] for c in found),'numeric child rows must expand to their authorized parent table')
        parent_index=next(i for i,c in enumerate(snapshot.chunks) if c['table'] and '125' in c['content'])
        child_index=next(i for i,c in enumerate(snapshot.chunks) if not c['table'] and '125' in c['content'])
        # Real PDF blocks can extend beyond the last glyph by trailing whitespace.
        snapshot.chunks[child_index]['regions'][0][2]=snapshot.chunks[parent_index]['regions'][0][2]+2.1
        self.assertIn(parent_index,snapshot.expand_tables([child_index],{child_index:.02}))
        self.upload(275)
        (self.sources/'ACME_2024.pdf').write_bytes(pdf.tobytes(no_new_id=True));self.p.sync_sources()
        for job in self.p.rows("SELECT id FROM kb_jobs WHERE state='queued'"):self.p.knowledge.process(job['id'])
        across_reports=self.pin().search('ACME 2021 Revenue')
        self.assertTrue(any(c['file_name']=='ACME_2024.pdf' and c['table'] and '2021A' in c['content'] and '125' in c['content'] for c in across_reports),
            'A table without company text must remain discoverable by filename and normalized actual-year headers')
        pdf.close()


    def test_feedback_stats_trace_export_minimizes_data_and_regeneration_scope(self):
        d=self.upload();s,e=self.chat({'doc_ids':[d['id']]});message=e[-2]['message_id']
        self.client.post(f'/api/v2/messages/{message}/feedback',json={'vote':-1,'reason':'引用错误'})
        self.client.post(f'/api/v2/messages/{message}/feedback',json={'vote':1,'reason':''})
        dash=self.client.get('/api/v2/dashboard').json();self.assertEqual((dash['denominator_attempts'],dash['valid_feedback'],dash['positive'],dash['negative']),(1,1,1,0))
        exported=self.client.get('/api/v2/trace-export').json();self.assertNotIn('Revenue',json.dumps(exported));self.assertNotIn('password',json.dumps(exported))
        import uuid
        r=self.client.post('/api/v2/chat',json={'session_id':s['id'],'request_id':str(uuid.uuid4()),'regenerate_message_id':message,'scope':{'kb_ids':[]}})
        events=[json.loads(line[6:]) for line in r.text.splitlines() if line.startswith('data: ')]
        self.assertEqual(events[-2]['scope']['doc_ids'],[d['id']])
        self.assertEqual(self.client.get('/api/v2/traces').json()['total'],2)
