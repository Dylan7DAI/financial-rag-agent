"""Single local KB: real temporary PDFs/indexes, deterministic fixture models."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent.test.fixtures import Embeddings, Model, pdf_bytes
from docker.backend.chat_store import ChatStore, now
from docker.backend.documents import DocumentRegistry, digest
from docker.backend.single_workspace import SingleWorkspace
from docker.backend.v2 import create_router


class SingleWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.sources=self.root/'raw';self.sources.mkdir()
        for i,value in enumerate((125,275,375)):(self.sources/f'report-{i}.pdf').write_bytes(pdf_bytes(value))
        self.hashes={p.name:digest(p) for p in self.sources.iterdir()}
        self.store=ChatStore(self.root/'chat.sqlite3')
        self.p=SingleWorkspace(self.store,Embeddings(),self.root,source_dir=self.sources)
        self.p.knowledge.rerank=False
        app=FastAPI();app.middleware('http')(self.p.middleware);app.include_router(self.p.router())
        app.include_router(create_router(type('Agent',(),{'clear_history':lambda *a:None})(),store=self.store,
            registry=DocumentRegistry(self.root,self.root),llm_factory=lambda **kw:Model(),platform=self.p))
        self.client=TestClient(app)

    def tearDown(self):
        self.client.close();self.store.close();self.temp.cleanup()

    def publish(self):
        for job in self.p.rows("SELECT id FROM kb_jobs WHERE state='queued'"):self.p.knowledge.process(job['id'])

    def test_every_source_is_indexed_searchable_and_sync_is_idempotent(self):
        self.assertEqual(self.client.get('/api/v2/knowledge-base').json()['document_count'],3)
        self.publish()
        docs=self.client.get('/api/v2/knowledge-base/documents').json()['items']
        self.assertEqual({d['name'] for d in docs},set(self.hashes))
        self.assertTrue(all(d['participates_in_rag'] and d['original_available'] for d in docs))
        self.assertEqual(self.p.catalog()['ready_count'],3)
        snapshot=self.p.knowledge.pin(self.p.user(),{})
        self.assertEqual(set(snapshot.versions),{d['id'] for d in docs})
        for d in docs:
            candidates=self.p.knowledge.pin(self.p.user(),{'doc_ids':[d['id']]}).search('Revenue')
            self.assertTrue(candidates);self.assertEqual({c['doc_id'] for c in candidates},{d['id']})
            response=self.client.get(f"/api/v2/documents/{d['id']}/pdf?version={d['doc_version']}")
            self.assertEqual(response.status_code,200);self.assertEqual(response.content,(self.sources/d['name']).read_bytes())
        self.p.sync_sources();self.p.sync_sources()
        self.assertEqual(self.p.one('SELECT count(*) n FROM kb_jobs')['n'],3)
        self.assertEqual({p.name:digest(p) for p in self.sources.iterdir()},self.hashes)

    def test_no_login_or_management_routes_and_rename_survives_restart(self):
        self.assertFalse((self.root/'setup.token').exists())
        self.assertIsNone(self.p.one("SELECT name FROM sqlite_master WHERE name='users'"))
        for path in ('auth/me','auth/status','users','audit','jobs','migration/claim-local-sessions'):
            self.assertEqual(self.client.get('/api/v2/'+path).status_code,404,path)
        self.assertEqual(self.client.post('/api/v2/knowledge-bases',json={'name':'second'}).status_code,405)
        self.assertEqual(self.client.post('/api/v2/sessions').status_code,200)
        self.assertEqual(self.client.patch('/api/v2/knowledge-base',json={'name':'  全部研报  '}).json()['name'],'全部研报')
        self.assertEqual(self.client.patch('/api/v2/knowledge-base',json={'name':'   '}).status_code,422)
        self.assertEqual(self.client.patch('/api/v2/knowledge-base',json={'name':'x'*81}).status_code,422)
        restarted=SingleWorkspace(self.store,Embeddings(),self.root,source_dir=self.sources)
        self.assertEqual(restarted.catalog()['name'],'全部研报')
        self.assertTrue((self.root/'pre-single-workspace.sqlite3').is_file())
        self.assertEqual(self.client.post('/api/v2/sessions',headers={'Origin':'https://evil.example'}).status_code,403)

    def test_add_update_remove_keep_immutable_history_and_stable_ids(self):
        self.publish();old=self.p.knowledge.pin(self.p.user(),{})
        doc=next(d for d in self.p.documents() if d['name']=='report-0.pdf')
        (self.sources/'report-0.pdf').write_bytes(pdf_bytes(475));self.p.sync_sources()
        self.assertTrue(self.p.catalog()['processing'])
        self.assertEqual(self.p.one('SELECT version FROM kb_documents WHERE id=?',(doc['id'],))['version'],doc['version'])
        self.publish();current=self.p.one('SELECT * FROM kb_documents WHERE id=?',(doc['id'],))
        self.assertNotEqual(current['version'],doc['version'])
        self.assertIn(doc['version'],{c['doc_version'] for c in old.chunks})
        self.assertEqual(self.client.get(f"/api/v2/documents/{doc['id']}/pdf?version={doc['version']}").status_code,200)
        (self.sources/'report-3.PDF').write_bytes(pdf_bytes(575));self.p.sync_sources();self.publish()
        self.assertEqual(self.p.catalog()['ready_count'],4)
        (self.sources/'report-0.pdf').unlink();self.p.sync_sources()
        self.assertEqual(self.p.catalog()['ready_count'],3)
        self.assertNotIn(doc['id'],self.p.knowledge.pin(self.p.user(),{}).versions)
        self.assertTrue((self.p.knowledge.root/doc['file']).is_file())
        self.assertEqual(self.client.get(f"/api/v2/documents/{doc['id']}/pdf?version={doc['version']}").status_code,200)

    def test_shared_existing_sessions_and_full_chat_without_identity(self):
        self.publish();session=self.store.create()
        self.p.execute("UPDATE sessions SET owner_id='previous-owner' WHERE id=?",(session['id'],))
        self.assertIn(session['id'],{s['id'] for s in self.client.get('/api/v2/sessions').json()})
        response=self.client.post('/api/v2/chat',json={'session_id':session['id'],'request_id':str(uuid.uuid4()),'query':'Revenue?'})
        self.assertEqual(response.status_code,200,response.text)
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        final=next(e for e in events if e['type']=='final')
        self.assertEqual(final['outcome'],'answered');self.assertTrue(final['citations'])
        self.assertEqual(len(self.client.get('/api/v2/traces').json()['items']),1)
        self.assertEqual(self.client.get('/api/v2/dashboard').json()['denominator_attempts'],1)

    def test_catalog_pagination_exceeds_one_hundred(self):
        for i in range(102):
            self.p.execute('INSERT INTO kb_documents(id,kb_id,name,version,file,owner_id,tags,created_at,state) VALUES(?,?,?,?,?,?,?,?,?)',
                (f'pagination-{i}',self.p.kb_id,f'fixture-{i}.pdf','fixture','fixture.pdf','local','[]',now(),'queued'))
        first=self.client.get('/api/v2/knowledge-base/documents?offset=0&limit=100').json()
        rest=self.client.get('/api/v2/knowledge-base/documents?offset=100&limit=100').json()
        self.assertEqual(first['total'],105);self.assertEqual(len(first['items']),100);self.assertEqual(len(rest['items']),5)
        self.assertEqual(len({d['id'] for d in first['items']+rest['items']}),105)

    def test_tool_scope_is_fixed_before_recall_and_bm25_does_not_embed_query(self):
        from unittest.mock import patch
        self.publish();doc=self.p.documents()[0];scope={'doc_ids':[doc['id']]}
        catalog=self.client.post('/api/v2/tools/report_catalog',json={'arguments':{},'scope':scope}).json()
        self.assertEqual([d['doc_id'] for d in catalog['result']['reports']],[doc['id']])
        with patch.object(self.p.knowledge.embeddings,'embed_query',side_effect=AssertionError('BM25 does not need embeddings')):
            found=self.client.post('/api/v2/tools/report_search',json={'arguments':{'query':'Revenue','mode':'bm25','use_reranker':False},'scope':scope}).json()
        self.assertEqual({d['doc_id'] for d in found['result']['docs']},{doc['id']})
        empty=self.client.post('/api/v2/tools/report_search',json={'arguments':{'query':'Revenue'},'scope':{'doc_ids':[]}}).json()
        self.assertEqual(empty['result']['docs'],[])


if __name__=='__main__':unittest.main()
