"""P1 protocol fixtures; isolated temporary PDFs/SQLite, never calls a real model."""
import asyncio
import json
import tempfile
import threading
import types
import unittest
import uuid
from pathlib import Path
import pymupdf
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent.call_lifecycle import CallLifecycle, CallSpec, LLMCallArgs, RiskLevel, StreamCallError
from docker.backend.documents import DocumentRegistry, digest
from docker.backend.chat_store import ChatStore, BusyError
from docker.backend.v2 import ChatService, Chat, create_router


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.pdfs = self.root / 'pdf'; self.index = self.root / 'index'
        self.pdfs.mkdir(); self.index.mkdir()
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72,72), 'Revenue was 51.25 billion in 2022.')
            pdf.new_page().insert_text((72,72), 'Revenue was 23.10 billion in 2021.')
            pdf.save(self.pdfs / 'report.pdf')
        self.raw = dict(file_name='report.pdf', page_num=1, chunk_id='chunk-1', content='Revenue was 51.25 billion in 2022.', score=.9)
        data = [{'page_content': self.raw['content'], 'metadata': {'file_name':'report.pdf', 'page_num':1, 'chunk_id':'chunk-1'}}]
        (self.index / 'all_documents.json').write_text(json.dumps(data), encoding='utf8')
        for name in ('index.faiss','index.pkl'): (self.index / name).write_bytes(b'fixture index')
        self.registry = DocumentRegistry(self.pdfs, self.index)
        self.store = ChatStore(self.root / 'chat.sqlite3'); self.addCleanup(self.store.close)
        self.session = self.store.create()['id']
        self.histories = []
        async def prepare(query, session_id, history):
            self.histories.append(history)
            if query == 'missing': return {'intent':'rag_qa', 'retrieval_success':False, 'retrieved_docs':[]}
            if query == 'chat': return {'intent':'chat'}
            return {'intent':'rag_qa', 'retrieval_success':True, 'retrieved_docs':[self.raw]}
        self.agent = types.SimpleNamespace(prepare_v2=prepare, clear_history=lambda _: None)
        self.outputs = ['Revenue was 51.25 billion [1].']
        self.calls = 0
        def llm(**kwargs):
            number = min(self.calls, len(self.outputs)-1); self.calls += 1
            async def stream(messages):
                text = self.outputs[number]
                for part in (text[:5], text[5:12], text[12:]):
                    yield types.SimpleNamespace(content=part, usage_metadata=None)
            return types.SimpleNamespace(astream=stream)
        self.llm = llm

    def payload(self, **kwargs): return {'session_id':self.session, 'request_id':str(uuid.uuid4()), **kwargs}

    def client(self):
        app = FastAPI(); router = create_router(self.agent, store=self.store, registry=self.registry, llm_factory=self.llm)
        app.include_router(router)
        return TestClient(app)

    @staticmethod
    def events(response):
        return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith('data:')]


class RegistryTest(Fixture):
    def test_snapshot_provenance_and_only_used_citations(self):
        before = digest(self.index / 'all_documents.json')
        items = self.registry.snapshots([self.raw])
        self.assertTrue(items[0]['source_verified']); self.assertEqual(items[0]['snippet'], self.raw['content'])
        self.assertIsNone(self.registry.validate('Revenue [1].',items))
        items[0]['included_in_prompt'] = True
        self.assertEqual(self.registry.validate('Revenue [1].',items), items)
        self.assertIsNone(self.registry.validate('Revenue [99].',items))
        self.assertIsNone(self.registry.validate('`[1]`\n```\n[1]\n```',items))
        self.assertIsNone(self.registry.validate('```\n[1]',items))
        self.assertIsNone(self.registry.validate('    [1]',items))
        self.assertIsNone(self.registry.validate(r'escaped \[1]',items))
        self.assertIsNone(self.registry.validate('> ```\n> [1]\n> ```',items))
        self.assertIsNone(self.registry.validate('- ```\n  [1]\n  ```',items))
        self.assertEqual(digest(self.index / 'all_documents.json'), before)
        bad = self.registry.snapshots([{**self.raw,'page_num':99}])[0]
        self.assertFalse(bad['source_verified'])
        bad = self.registry.snapshots([{**self.raw,'content':'invented text'}])[0]
        self.assertFalse(bad['source_verified'])

    def test_original_id_path_version_and_missing_page_guards(self):
        client = self.client(); doc = self.registry.catalog()[0]
        url = f"/api/v2/documents/{doc['doc_id']}/pdf?version={doc['doc_version']}"
        self.assertEqual(client.get(url).status_code,200)
        self.assertEqual(client.get(url+'&page=99').status_code,416)
        self.assertEqual(client.get(url+'&path=/etc/passwd').status_code,400)
        self.assertEqual(client.get('/api/v2/documents/unknown/pdf?version=bad').status_code,404)
        self.assertEqual(client.get(url.replace(doc['doc_version'],'wrong')).status_code,409)
        (self.pdfs/'report.pdf').write_bytes(b'changed version')
        self.assertEqual(client.get(url).status_code,409)


class StoreTest(Fixture):
    def test_regeneration_preserves_versions_without_duplicate_user_or_context(self):
        user, first = self.store.begin(self.session, str(uuid.uuid4()), 'question')
        self.store.finish(first['request_id'],'complete',{'content':'original','outcome':'answered'})
        user2, second = self.store.begin(self.session, str(uuid.uuid4()), regenerate=first['id'])
        self.assertEqual(user['id'],user2['id']); self.assertEqual(self.store.context(self.session),[])
        self.store.finish(second['request_id'],'complete',{'content':'replacement','outcome':'answered'})
        self.assertEqual(self.store.context(self.session),[('question','replacement')])
        self.assertEqual(len([m for m in self.store.messages(self.session) if m['role']=='user']),1)
        self.assertFalse(self.store.message(first['id'])['active'])
        persisted = ChatStore(self.root / 'chat.sqlite3')
        self.assertEqual(persisted.context(self.session), [('question','replacement')]); persisted.close()

    def test_pending_conflict_cancel_and_restart_recovery(self):
        _, message = self.store.begin(self.session,str(uuid.uuid4()),'question')
        with self.assertRaises(BusyError): self.store.clear(self.session)
        self.store.finish(message['request_id'],'cancelled',{'draft':'incomplete','outcome':'cancelled'})
        self.assertEqual(self.store.context(self.session),[])
        _, message = self.store.begin(self.session,str(uuid.uuid4()),'second')
        recovered = ChatStore(self.root/'chat.sqlite3')
        self.assertEqual(recovered.message(message['id'])['status'],'error')
        self.assertEqual(recovered.context(self.session),[]); recovered.close()


class ProtocolTest(Fixture):
    def test_stream_ids_order_single_terminal_and_final_used_citations(self):
        client = self.client(); data = self.payload(query='question')
        events = self.events(client.post('/api/v2/chat',json=data))
        self.assertEqual([e['seq'] for e in events],list(range(1,len(events)+1)))
        self.assertTrue(all(e['request_id']==data['request_id'] for e in events))
        self.assertEqual(sum(e['type'] in ('done','error') for e in events),1)
        self.assertGreater(sum(e['type']=='delta' for e in events),1)
        final = next(e for e in events if e['type']=='final')
        self.assertEqual(final['citations'][0]['id'],1)
        self.assertEqual(final['citation_integrity'],'passed')
        self.assertEqual(final['usage']['input_tokens'],None)
        self.assertEqual(client.post('/api/v2/chat',json=data).status_code,409)
        recovered = client.get('/api/v2/requests/'+data['request_id'])
        self.assertEqual(recovered.status_code,200)
        self.assertEqual(recovered.json()['message']['id'],final['message_id'])
        message = self.store.messages(self.session)[-1]
        self.assertEqual(client.post(f"/api/v2/messages/{message['id']}/feedback",json={'vote':-1,'reason':'fixture'}).json()['reason'],'fixture')
        self.assertEqual(client.post(f"/api/v2/messages/{message['id']}/copy").status_code,200)

    def test_repair_resets_draft_final_does_not_append_both_attempts(self):
        self.outputs = ['Invalid [99].','Correct [1].']
        events = self.events(self.client().post('/api/v2/chat',json=self.payload(query='question')))
        self.assertTrue(any(e.get('reset_draft') for e in events))
        final = next(e for e in events if e['type']=='final')
        self.assertEqual(final['content'],'Correct [1].'); self.assertEqual(self.calls,2)

    def test_bad_citations_fail_closed_no_evidence_and_chat_are_distinct(self):
        self.outputs = ['Invalid [99].']
        events = self.events(self.client().post('/api/v2/chat',json=self.payload(query='question')))
        final = next(e for e in events if e['type']=='final')
        self.assertEqual(final['outcome'],'citation_failed'); self.assertEqual(final['citations'],[])
        self.assertEqual(self.store.context(self.session),[])
        events = self.events(self.client().post('/api/v2/chat',json=self.payload(query='missing')))
        self.assertEqual(next(e for e in events if e['type']=='final')['outcome'],'no_evidence')
        self.outputs = ['hello']
        events = self.events(self.client().post('/api/v2/chat',json=self.payload(query='chat')))
        self.assertEqual(next(e for e in events if e['type']=='final')['outcome'],'chat')

    def test_no_evidence_marker_with_commentary_never_becomes_a_cited_answer(self):
        for draft in ['NO_EVIDENCE。所给片段没有这个指标[1]。', '无法确定[1]。\n\nNO_EVIDENCE', '**NO_EVIDENCE**']:
            self.outputs = [draft]
            events = self.events(self.client().post('/api/v2/chat',json=self.payload(query='question')))
            final = next(e for e in events if e['type']=='final')
            self.assertEqual(final['outcome'],'no_evidence')
            self.assertEqual(final['citations'],[])
            self.assertNotIn('NO_EVIDENCE',final['content'])

    def test_immediate_cancel_is_durable_and_late_finish_cannot_commit(self):
        async def check():
            service = ChatService(self.agent,self.store,self.registry,self.llm)
            run = service.begin(Chat(**self.payload(query='question')))
            await service.cancel(run.request_id)
            self.assertEqual(self.store.message(run.assistant['id'])['status'],'cancelled')
            self.assertFalse(self.store.finish(run.request_id,'complete',{'content':'late'}))
            self.assertEqual(self.store.context(self.session),[])
        asyncio.run(check())

    def test_cancel_after_real_adapter_delta_closes_iterator_and_never_commits_context(self):
        async def check():
            closed = []
            async def slow(messages):
                try:
                    yield types.SimpleNamespace(content='unfinished [1]',usage_metadata=None)
                    await asyncio.sleep(30)
                finally: closed.append(True)
            service = ChatService(self.agent,self.store,self.registry,lambda **_: types.SimpleNamespace(astream=slow))
            run = service.begin(Chat(**self.payload(query='question')))
            while (await run.queue.get())['type'] != 'delta': pass
            await service.cancel(run.request_id)
            self.assertEqual(closed,[True])
            self.assertEqual(self.store.context(self.session),[])
            self.assertEqual(self.store.message(run.assistant['id'])['status'],'cancelled')
            remaining=[]
            while not run.queue.empty(): remaining.append(run.queue.get_nowait())
            self.assertEqual([event['type'] for event in remaining],['done'])
        asyncio.run(check())


class StreamingLifecycleTest(unittest.TestCase):
    def test_sync_tools_and_streams_share_the_same_concurrency_permit(self):
        async def check():
            life=CallLifecycle(specs=[CallSpec('llm.answer',LLMCallArgs,RiskLevel.MODEL,1,1)],max_concurrency=1)
            entered, release=threading.Event(),threading.Event()
            def sync(messages): entered.set(); release.wait(.5); return 'sync'
            sync_task=asyncio.create_task(asyncio.to_thread(life.execute,'llm.answer',sync,{'messages':['q']}))
            await asyncio.to_thread(entered.wait,.5)
            seen=[]
            async def stream(messages): seen.append(True); yield types.SimpleNamespace(content='delta')
            async def consume(): return [x async for x in life.stream('llm.answer',stream,{'messages':['q']})]
            stream_task=asyncio.create_task(consume()); await asyncio.sleep(.03)
            self.assertEqual(seen,[])
            release.set(); await sync_task; await stream_task
            self.assertEqual(seen,[True])
        asyncio.run(check())

    def test_retry_before_first_token_but_never_after_visible_draft(self):
        async def check():
            life = CallLifecycle(specs=[CallSpec('llm.answer',LLMCallArgs,RiskLevel.MODEL,.2,2)], backoff_base_seconds=0)
            calls = 0
            async def generate(messages):
                nonlocal calls; calls += 1
                if calls == 1: raise ConnectionError('fixture unavailable')
                yield types.SimpleNamespace(content='real adapter chunk')
            chunks = [x async for x in life.stream('llm.answer',generate,{'messages':['q']})]
            self.assertEqual(calls,2); self.assertEqual(chunks[0].content,'real adapter chunk')
            calls = 0
            async def broken(messages):
                nonlocal calls; calls += 1
                yield types.SimpleNamespace(content='draft')
                raise ConnectionError('disconnected')
            with self.assertRaises(StreamCallError):
                async for _ in life.stream('llm.answer',broken,{'messages':['q']}): pass
            self.assertEqual(calls,1)
        asyncio.run(check())

    def test_timeout_and_cancellation_close_upstream_iterator(self):
        async def check():
            life = CallLifecycle(specs=[CallSpec('llm.answer',LLMCallArgs,RiskLevel.MODEL,.02,1)])
            closed = []
            async def slow(messages):
                try:
                    yield types.SimpleNamespace(content='draft')
                    await asyncio.sleep(1)
                finally: closed.append(True)
            with self.assertRaises(StreamCallError):
                async for _ in life.stream('llm.answer',slow,{'messages':['q']}): pass
            self.assertEqual(closed,[True])
        asyncio.run(check())


if __name__ == '__main__': unittest.main()
