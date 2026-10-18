"""One local knowledge base: no login, roles, resource ownership or ACL gates.

Keep existing document/index/message identifiers and storage for compatibility.
The public catalog includes every source PDF and its indexing status.
"""
import json
import os
import re
import shutil
import threading
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from docker.backend.storage import WorkspaceStore, KB, Scope, Strict
from pydantic import Field
from docker.backend.chat_store import uid,now
from docker.backend.documents import digest


class ToolInvocation(Strict):
    arguments: dict = Field(default_factory=dict)
    scope: Scope = Field(default_factory=Scope)


class SingleWorkspace(WorkspaceStore):
    shared_workspace = True

    def __init__(self, store, embeddings, root, *, source_dir=None):
        super().__init__(store, embeddings, root)
        with self.lock,self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            configured=self.db.execute("SELECT value FROM app_settings WHERE key='single_kb_id'").fetchone()
            bases=self.db.execute('SELECT * FROM knowledge_bases WHERE deleted=0 ORDER BY rowid').fetchall()
            self.kb_id=configured['value'] if configured else bases[0]['id'] if bases else 'knowledge-base'
            self.db.execute("INSERT OR IGNORE INTO knowledge_bases(id,name,owner_id) VALUES(?,?,'local')",(self.kb_id,'研报知识库'))
            self.db.execute('UPDATE knowledge_bases SET deleted=0 WHERE id=?',(self.kb_id,))
            # Consolidation only changes catalog membership; immutable source IDs,
            # PDF hashes, index files, conversation contents and versions survive.
            self.db.execute('UPDATE kb_documents SET kb_id=?',(self.kb_id,))
            self.db.execute('UPDATE knowledge_bases SET deleted=1 WHERE id<>?',(self.kb_id,))
            self.db.execute("INSERT OR REPLACE INTO app_settings VALUES('single_kb_id',?)",(self.kb_id,))
            self.db.execute('CREATE TABLE IF NOT EXISTS single_source_files(source TEXT PRIMARY KEY,doc_id TEXT,version TEXT,present INTEGER NOT NULL DEFAULT 1)')
        self.source_dir=Path(source_dir).resolve() if source_dir else None
        self.sync_lock=threading.RLock()
        self.sync_sources()

    def sync_sources(self):
        """Mirror every local PDF into immutable managed versions; never write
        the source folder/index. Unchanged files do not enqueue repeated work.
        """
        if not self.source_dir or not self.source_dir.is_dir():return
        with self.sync_lock:
            sources=[]
            for source in sorted(self.source_dir.rglob('*')):
                if not source.is_file() or source.suffix.lower()!='.pdf' or source.is_symlink():continue
                if self.source_dir not in source.resolve().parents:continue
                name=source.relative_to(self.source_dir).as_posix();sources.append(name)
                version=digest(source)
                tracked=self.one('SELECT * FROM single_source_files WHERE source=?',(name,))
                if tracked and tracked['version']==version:
                    current=self.one('SELECT id FROM kb_documents WHERE id=? AND deleted=0',(tracked['doc_id'],))
                    if current:
                        self.execute('UPDATE single_source_files SET present=1 WHERE source=?',(name,));continue
                doc=self.one('SELECT * FROM kb_documents WHERE id=? AND deleted=0',(tracked['doc_id'],)) if tracked else None
                if not doc:doc=self.one('SELECT * FROM kb_documents WHERE kb_id=? AND name=? AND version=? AND deleted=0',(self.kb_id,name,version))
                if not doc:
                    identifier=uid();filename=f'{identifier}-{version}.pdf'
                    shutil.copyfile(source,self.knowledge.root/filename)
                    self.execute('INSERT INTO kb_documents(id,kb_id,name,version,file,owner_id,tags,created_at,state) VALUES(?,?,?,?,?,?,?,?,?)',
                        (identifier,self.kb_id,name,version,filename,'local','[]',now(),'queued'))
                    doc=self.one('SELECT * FROM kb_documents WHERE id=?',(identifier,));self.knowledge.enqueue(doc)
                elif doc['version']!=version:
                    pending=self.one("SELECT id FROM kb_jobs WHERE doc_id=? AND state IN ('queued','parsing','indexing')",(doc['id'],))
                    if pending:continue # keep tracking old hash; sync after current publish
                    filename=f'{doc["id"]}-{version}.pdf';shutil.copyfile(source,self.knowledge.root/filename)
                    self.knowledge.enqueue(doc,{'version':version,'file':filename})
                self.execute('INSERT OR REPLACE INTO single_source_files VALUES(?,?,?,1)',(name,doc['id'],version))
            for row in self.rows('SELECT source FROM single_source_files'):
                if row['source'] not in sources:self.execute('UPDATE single_source_files SET present=0 WHERE source=?',(row['source'],))

    def user(self):
        # Internal trace/storage adapter, not an authenticated user or admin role.
        return dict(id='local',username='local',role='local',permission_version=0)

    def live_user(self, identifier):return self.user()
    def role(self,*roles):return self.user()
    def accessible(self,user):return {self.kb_id}
    def can_read(self,user,kb_id):return kb_id==self.kb_id

    def kb(self,identifier,write=False):
        row=self.one('SELECT * FROM knowledge_bases WHERE id=? AND deleted=0',(identifier,))
        if not row or identifier!=self.kb_id:raise HTTPException(404,'知识库不存在')
        return row

    def documents(self,user=None):
        return self.rows('''SELECT d.* FROM kb_documents d WHERE kb_id=? AND deleted=0 AND active_index IS NOT NULL
            AND NOT EXISTS(SELECT 1 FROM single_source_files s WHERE s.doc_id=d.id AND s.present=0) ORDER BY created_at DESC''',(self.kb_id,))

    @staticmethod
    def public_doc(doc):return {k:v for k,v in doc.items() if k not in ('file','owner_id','workspace_id','generation')}

    def check_session(self,identifier):
        if not self.one('SELECT id FROM sessions WHERE id=?',(identifier,)):raise HTTPException(404,'会话不存在')

    def sanitize(self,message):
        result=super().sanitize(message)
        if result.get('unavailable'):result['content']='关联原文未登记或已移除，历史回答暂不可核验。'
        return result

    async def middleware(self,request,call_next):
        if not request.url.path.startswith('/api/'):return await call_next(request)
        try:
            if request.method not in ('GET','HEAD','OPTIONS'):
                origin=request.headers.get('origin')
                if origin and origin not in os.getenv('APP_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(','):
                    raise HTTPException(403,'请求来源不允许')
                if request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403,'禁止跨站写入')
            path=request.url.path
            if not path.startswith('/api/v2/'):raise HTTPException(404,'接口不存在，请使用 /api/v2')
            match=re.match(r'/api/v2/(sessions|messages|requests)/([^/]+)',path)
            if match:
                kind,identifier=match.groups()
                if kind=='sessions':self.check_session(identifier)
                elif kind=='messages':self.check_message(identifier)
                else:
                    row=self.one('SELECT session_id FROM requests WHERE id=?',(identifier,))
                    if not row:raise HTTPException(404,'请求不存在')
                    self.check_session(row['session_id'])
            response=await call_next(request)
            response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
            return response
        except HTTPException as error:return JSONResponse({'detail':error.detail},status_code=error.status_code)

    def catalog(self):
        self.sync_sources()
        base=self.kb(self.kb_id)
        all_docs=self.catalog_documents()
        pending={j['doc_id'] for j in self.rows("SELECT doc_id FROM kb_jobs WHERE state IN ('queued','parsing','indexing')")}
        return dict(id=self.kb_id,name=base['name'],document_count=len(all_docs),ready_count=len(self.documents()),
            processing=any(d['id'] in pending for d in all_docs),mode='single')

    def catalog_documents(self):
        return self.rows('''SELECT d.* FROM kb_documents d WHERE kb_id=? AND deleted=0
            AND NOT EXISTS(SELECT 1 FROM single_source_files s WHERE s.doc_id=d.id AND s.present=0) ORDER BY created_at DESC''',(self.kb_id,))

    def pdfs(self,offset,limit):
        self.sync_sources()
        items=[]
        for doc in self.catalog_documents():
            index=self.one('SELECT created_at FROM kb_indexes WHERE id=?',(doc['active_index'],))
            available=(self.knowledge.root/doc['file']).is_file()
            items.append(dict(id=doc['id'],name=doc['name'],pages=doc['pages'],doc_version=doc['version'],
                index_version=doc['active_index'],indexed_at=index['created_at'] if index else None,
                original_available=available,participates_in_rag=bool(doc['active_index']),state=doc['state'],error=doc['error']))
        return dict(total=len(items),items=items[offset:offset+limit])

    def router(self):
        router=APIRouter(prefix='/api/v2')
        router.routes.extend(super().router().routes)

        @router.get('/tools')
        def list_tools():
            from agent.call_lifecycle import call_lifecycle
            names={'report_search','context_expand','report_catalog','metric_calculate'}
            return [{**tool,'ready':True} for tool in call_lifecycle.list_registered_tools() if tool['name'] in names]

        @router.post('/tools/{name}')
        async def invoke_tool(name:str,data:ToolInvocation):
            import asyncio
            if name not in {'report_search','context_expand','report_catalog','metric_calculate'}:raise HTTPException(404,'工具不存在')
            snapshot=self.knowledge.pin(self.user(),data.scope.model_dump())
            result=await asyncio.to_thread(snapshot.create_agent().invoke_tool,name,data.arguments)
            snapshot.assert_access()
            return result

        @router.get('/knowledge-base')
        def catalog():return self.catalog()

        @router.patch('/knowledge-base')
        def rename(data:KB):
            name=data.name.strip()
            if not name:raise HTTPException(422,'知识库名称不能为空')
            self.execute('UPDATE knowledge_bases SET name=? WHERE id=?',(name,self.kb_id))
            return self.catalog()

        @router.get('/knowledge-base/documents')
        def documents(offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):return self.pdfs(offset,limit)

        @router.get('/knowledge-bases')
        def compatibility_catalog():return [self.catalog()]

        @router.get('/managed-documents')
        def compatibility_documents(offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
            docs=[self.public_doc(d) for d in self.documents()];return dict(total=len(docs),items=docs[offset:offset+limit])
        return router

    def traces(self,outcome=''):
        result=[]
        for row in self.rows('SELECT t.*,m.status,m.payload,m.vote,m.reason FROM qa_traces t JOIN messages m ON m.id=t.message_id ORDER BY t.created_at DESC'):
            payload=json.loads(row.pop('payload'));sources=payload.get('candidates',[])
            if any(not self.source_allowed(None,c['doc_id']) for c in sources):continue
            for key in ('user_id','permission_version'):row.pop(key,None)
            row.update(outcome=payload.get('outcome'),usage=payload.get('usage'),citation_count=len(payload.get('citations',[])),
                candidate_count=len(sources),attempt_id=row['message_id'],configuration=json.loads(row['configuration']))
            if not outcome or row['outcome']==outcome:result.append(row)
        return result

    def pin_run(self,data):
        self.check_session(data.session_id)
        if data.regenerate_message_id:
            message=self.store.message(data.regenerate_message_id)
            if message['session_id']!=data.session_id:raise HTTPException(400,'消息不属于当前会话')
            trace=self.one('SELECT scope FROM qa_traces WHERE message_id=?',(message['id'],))
            scope=json.loads(trace['scope']) if trace else Scope().model_dump()
        else:scope=data.scope.model_dump() if data.scope else Scope().model_dump()
        if scope.get('kb_ids')!=[]:scope['kb_ids']=[self.kb_id]
        snapshot=self.knowledge.pin(self.user(),scope)
        return snapshot,self.user(),scope
