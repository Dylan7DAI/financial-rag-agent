"""SQLite storage and diagnostics for one local knowledge base.

Retain existing columns for in-place upgrades; no account or ACL features.
"""
import json
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from docker.backend.chat_store import now, uid
from docker.backend.knowledge import Knowledge


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class KB(Strict):
    name: str = Field(min_length=1, max_length=80)


class Scope(Strict):
    kb_ids: list[str] | None = Field(None, max_length=100)
    doc_ids: list[str] | None = Field(None, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    time_field: str = 'published_on'
    from_date: str | None = None
    to_date: str | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.time_field not in ('published_on', 'created_at'): raise ValueError('时间字段无效')
        for value in (self.from_date, self.to_date):
            if value: date.fromisoformat(value)
        if self.from_date and self.to_date and self.from_date > self.to_date: raise ValueError('起止日期颠倒')
        return self


class Preview(Strict):
    query: str = Field(min_length=1, max_length=2000)
    scope: Scope = Field(default_factory=Scope)


class WorkspaceStore:
    def __init__(self, store, embeddings, root):
        self.store, self.db, self.lock = store, store.db, store.lock
        self.root = Path(root).resolve(); self.root.mkdir(parents=True, exist_ok=True)
        backup = self.root/'pre-single-workspace.sqlite3'
        if not backup.exists():
            with sqlite3.connect(backup) as target: self.db.backup(target)
        with self.lock, self.db:
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS knowledge_bases(id TEXT PRIMARY KEY,name TEXT,owner_id TEXT,deleted INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS kb_documents(id TEXT PRIMARY KEY,kb_id TEXT,name TEXT,version TEXT,file TEXT,owner_id TEXT,tags TEXT,
              published_on TEXT,created_at TEXT,state TEXT,error TEXT,pages INTEGER DEFAULT 0,active_index TEXT,generation INTEGER DEFAULT 1,deleted INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS kb_jobs(id TEXT PRIMARY KEY,doc_id TEXT,generation INTEGER,state TEXT,created_at TEXT,updated_at TEXT,owner_id TEXT,error TEXT,index_version TEXT);
            CREATE TABLE IF NOT EXISTS kb_indexes(id TEXT PRIMARY KEY,doc_id TEXT,doc_version TEXT,manifest TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS kb_versions(doc_id TEXT,version TEXT,file TEXT,pages INTEGER,PRIMARY KEY(doc_id,version));
            CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,actor TEXT,action TEXT,resource TEXT,created_at TEXT,detail TEXT);
            CREATE TABLE IF NOT EXISTS qa_traces(request_id TEXT PRIMARY KEY,message_id TEXT,user_id TEXT,scope TEXT,index_version TEXT,permission_version INTEGER,created_at TEXT,configuration TEXT);
            ''')
            columns = {r['name'] for r in self.db.execute('PRAGMA table_info(kb_jobs)')}
            if 'candidate' not in columns: self.db.execute('ALTER TABLE kb_jobs ADD COLUMN candidate TEXT')
            if 'kind' not in columns: self.db.execute("ALTER TABLE kb_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'index'")
        self.knowledge = Knowledge(self, embeddings, self.root/'knowledge')

    def one(self, sql, args=()):
        with self.lock:
            row=self.db.execute(sql,args).fetchone(); return dict(row) if row else None

    def rows(self, sql, args=()):
        with self.lock: return [dict(row) for row in self.db.execute(sql,args)]

    def execute(self, sql, args=()):
        with self.lock,self.db: return self.db.execute(sql,args)

    def audit(self, actor, action, resource='', detail=None):
        self.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(uid(),actor,action,resource,now(),json.dumps(detail or {},ensure_ascii=False)))

    def document(self, identifier, write=False):
        row=self.one('SELECT * FROM kb_documents WHERE id=? AND deleted=0',(identifier,))
        if not row: raise HTTPException(404,'文档已删除或不存在')
        self.kb(row['kb_id']); return row

    def check_message(self, identifier):
        row=self.one('SELECT session_id FROM messages WHERE id=?',(identifier,))
        if not row: raise HTTPException(404,'消息不存在')
        self.check_session(row['session_id'])

    def source_allowed(self, user, doc_id):
        doc=self.one('SELECT kb_id FROM kb_documents WHERE id=? AND deleted=0',(doc_id,))
        return bool(doc and doc['kb_id']==self.kb_id)

    def sanitize(self, message):
        if any(not self.source_allowed(None,c.get('doc_id')) for c in message.get('citations',[])+message.get('candidates',[])):
            return {**message,'content':'关联原文未登记或已移除，历史回答暂不可核验。','draft':'','unverified_draft':'',
                'citations':[],'candidates':[],'observations':[],'unavailable':True}
        return message

    def record_run(self, run, snapshot, user, scope):
        from configs import model_config
        configuration={'pipeline':'shared-agent-graph-v1','embedding':model_config.EMBEDDING_MODEL_NAME,
            'reranker':model_config.RERANKER_MODEL_NAME,'model':model_config.LLM_MODEL_NAME,
            'index_versions':snapshot.versions,'parser':'pymupdf-elements+aligned-year-grid-v2',
            'tools':['report_search','context_expand','report_catalog','metric_calculate']}
        self.execute('INSERT INTO qa_traces VALUES(?,?,?,?,?,?,?,?)',(run.request_id,run.assistant['id'],'local',json.dumps(scope),
            snapshot.index_version,0,now(),json.dumps(configuration)))

    def router(self):
        import asyncio
        router=APIRouter(prefix='/api/v2')

        @router.post('/search-preview')
        async def search_preview(data:Preview):
            snapshot=self.knowledge.pin(self.user(),data.scope.model_dump())
            docs=await asyncio.to_thread(snapshot.search,data.query)
            return {'index_version':snapshot.index_version,'scope':data.scope.model_dump(),
                'candidate_domain':len(snapshot.chunks),'candidates':snapshot.snapshots(docs)}

        @router.get('/traces')
        def traces(offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100),outcome:str=''):
            items=self.traces(outcome); return {'total':len(items),'items':items[offset:offset+limit]}

        @router.get('/traces/{identifier}')
        def trace_detail(identifier:str):
            if not any(t['message_id']==identifier for t in self.traces()): raise HTTPException(404,'记录不存在或来源已移除')
            trace=self.one('SELECT * FROM qa_traces WHERE message_id=?',(identifier,))
            for key in ('user_id','permission_version'): trace.pop(key,None)
            return {'trace':trace,'result':self.sanitize(self.store.message(identifier)),
                'replay_note':'查看当时结果；重新生成会创建新请求，并使用当前已发布的文档版本。'}

        @router.get('/trace-export')
        def export():
            return JSONResponse({'schema':1,'generated_at':now(),'items':self.traces()},
                headers={'Content-Disposition':'attachment; filename="qa-traces.json"'})

        @router.get('/dashboard')
        def dashboard(days:int=Query(30,ge=1,le=365)):
            items=[i for i in self.traces() if datetime.fromisoformat(i['created_at']).timestamp()>=time.time()-days*86400]
            reasons={}
            for item in items:
                if item['vote']:
                    key=item['reason'] or ('点赞' if item['vote']==1 else '未注明原因'); reasons[key]=reasons.get(key,0)+1
            return {'window_days':days,'denominator_attempts':len(items),'valid_feedback':sum(bool(i['vote']) for i in items),
                'positive':sum(i['vote']==1 for i in items),'negative':sum(i['vote']==-1 for i in items),'reasons':reasons,
                'failures':[i for i in items if i['status'] in ('error','cancelled') or i['outcome']=='no_evidence'][:20]}
        return router
