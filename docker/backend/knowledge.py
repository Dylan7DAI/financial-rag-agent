"""Managed, immutable PDF snapshots. The original dataset is never written.

Single durable worker per server process (deploy uvicorn with one worker).
No result cache: revocation is checked again before every result and PDF read.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
import threading
import time
from pathlib import Path
import numpy as np
import faiss
import pymupdf
from rank_bm25 import BM25Okapi
from fastapi import HTTPException
from docker.backend.chat_store import uid, now
from docker.backend.documents import DocumentRegistry, digest


def tokens(text):
    # Financial tables use 2021A / 2024E while questions normally use 2021 / 2024.
    lexical=re.sub(r'\b((?:19|20)\d{2})[ae]\b',r'\1',text.lower())
    return re.findall(r'[a-z0-9]+|[\u3400-\u9fff]', lexical) or ['_']


def aligned_year_tables(page):
    """Conservative borderless financial grids from actual word coordinates.
    Require >=3 year columns and >=2 labelled numeric rows aligned with them.
    No inferred values/units; each cell retains its real word union rectangle.
    """
    lines=[]
    for word in sorted(page.get_text('words'),key=lambda w:((w[1]+w[3])/2,w[0])):
        center=(word[1]+word[3])/2
        target=next((line for y,line in reversed(lines[-4:]) if abs(y-center)<3),None)
        if target is None: target=[];lines.append((center,target))
        target.append(word)
    result=[]
    for pos,(y,header) in enumerate(lines):
        years=sorted([w for w in header if re.fullmatch(r'(?:19|20)\d{2}[AEae]?',w[4].replace(',',''))],key=lambda w:w[0])
        if len(years)<3: continue
        centers=[(w[0]+w[2])/2 for w in years]
        if min(b-a for a,b in zip(centers,centers[1:]))<20: continue
        label_words=[w for w in header if w[2]<years[0][0]-2]
        if not label_words: continue
        label=' '.join(w[4] for w in sorted(label_words,key=lambda w:w[0]))
        bounds=[years[0][0]-6]+[(a+b)/2 for a,b in zip(centers,centers[1:])]+[years[-1][2]+12]
        rows=[[label]+[w[4] for w in years]]; used=label_words+years; cells=[list(pymupdf.Rect(label_words[0][:4]))]+[list(w[:4]) for w in years]
        for row_y,words in lines[pos+1:pos+14]:
            if row_y-y>170: break
            words=[w for w in words if w[0]<bounds[-1]]
            if not words: continue # independent right-hand column, not a table row
            groups=[[w for w in words if bounds[i]<=((w[0]+w[2])/2)<bounds[i+1]] for i in range(len(years))]
            values=[' '.join(w[4] for w in sorted(group,key=lambda w:w[0])) for group in groups]
            left=[w for w in words if w[2]<bounds[0]]
            name=' '.join(w[4] for w in sorted(left,key=lambda w:w[0]))
            if sum(bool(re.fullmatch(r'[\d,.%+()\-]+',v)) for v in values)<3 or not re.search(r'[A-Za-z\u3400-\u9fff]',name):
                if len(rows)>1: break
                continue
            rows.append([name]+values);used.extend(left)
            for group in [left]+groups:
                if not group: cells.append(None);continue
                rect=pymupdf.Rect(group[0][:4])
                for w in group[1:]:rect|=pymupdf.Rect(w[:4])
                cells.append(list(rect));used.extend(group)
        if len(rows)<3: continue
        rect=pymupdf.Rect(used[0][:4])
        for w in used[1:]:rect|=pymupdf.Rect(w[:4])
        result.append(('\n'.join(' | '.join(row) for row in rows),list(rect),dict(rows=rows,headers=rows[0],cells=cells,
            extraction='aligned_year_word_coordinates',units_note='单位来自原行标签；未推断缺失单位或数字。')))
    return result


def parse_pdf(path, doc):
    chunks = []
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass or len(pdf) > 1000: raise ValueError('加密 PDF 或超过 1000 页')
        for number, page in enumerate(pdf, 1):
            original_rotation=page.rotation
            # Parse in unrotated page space. PyMuPDF's rotated-page matrix can
            # omit crop offsets; deriving it at rotation=0 preserves PDF mapping.
            if original_rotation:page.set_rotation(0)
            geometry = dict(width=page.mediabox.width, height=page.mediabox.height,
                rotation=original_rotation, cropbox=list(page.cropbox), origin='top-left', unit='pt',
                coordinate_space='pymupdf-unrotated', transformation_matrix=list(page.transformation_matrix))
            tables = []
            try: tables = list(page.find_tables().tables)
            except (ValueError, RuntimeError): pass
            elements = []
            for table in tables:
                rows = table.extract()
                text = '\n'.join(' | '.join('' if cell is None else str(cell) for cell in row) for row in rows)
                elements.append((text, list(table.bbox), dict(rows=rows, headers=table.header.names,
                    units_note='单位与脚注保留在原表单元格及相邻文本；未推断缺失单位。', cells=[list(c) if c else None for c in table.cells])))
            for text,bbox,table in aligned_year_tables(page):
                if not any(pymupdf.Rect(t.bbox).intersects(pymupdf.Rect(bbox)) for t in tables): elements.append((text,bbox,table))
            for block in page.get_text('blocks', sort=True):
                if block[6] != 0 or not block[4].strip(): continue
                rect = pymupdf.Rect(block[:4])
                if any(pymupdf.Rect(t.bbox).contains(rect) for t in tables): continue
                elements.append((block[4].strip(), list(rect), None))
            for ordinal, (text, bbox, table) in enumerate(elements):
                # Long elements keep their real block bbox; never assert sentence precision.
                for offset in range(0, len(text), 1800):
                    content = text[offset:offset + 1800]
                    chunks.append(dict(chunk_id=f'{doc["id"]}:{number}:{ordinal}:{offset}', doc_id=doc['id'],
                        doc_version=doc['version'], file_name=doc['name'], page_num=number, content=content,
                        page_label=page.get_label() or None, regions=[bbox], geometry=geometry,
                        table=table if offset == 0 else None, element_type='table' if table else 'text_block'))
            if original_rotation:page.set_rotation(original_rotation)
    if not chunks: raise ValueError('未提取到文字；扫描件需要 OCR，本期未配置')
    return chunks


class Knowledge:
    def __init__(self, platform, embeddings, root):
        self.p, self.embeddings = platform, embeddings
        self.root = Path(root).resolve(); self.root.mkdir(parents=True, exist_ok=True)
        self.wake, self.stop = threading.Event(), threading.Event()
        self.reranker, self.model_lock = None, threading.RLock()

    def start(self):
        with self.p.lock, self.p.db:
            self.p.db.execute("UPDATE kb_jobs SET state='queued',error=NULL WHERE state IN ('parsing','indexing','deleting')")
        self.thread = threading.Thread(target=self.worker, daemon=True, name='knowledge-worker'); self.thread.start()

    def worker(self):
        while not self.stop.is_set():
            row = self.p.one("SELECT * FROM kb_jobs WHERE state='queued' ORDER BY created_at LIMIT 1")
            if not row: self.wake.wait(1); self.wake.clear(); continue
            self.process(row['id'])

    def process(self, job_id):
        job = self.p.one('SELECT * FROM kb_jobs WHERE id=?', (job_id,))
        if job.get('kind')=='delete': return self.process_delete(job)
        doc = self.p.one('SELECT * FROM kb_documents WHERE id=?', (job['doc_id'],))
        if doc and job.get('candidate'): doc.update(json.loads(job['candidate']))
        try:
            if not doc or doc['deleted']: raise InterruptedError()
            self.stage(job_id, 'parsing')
            chunks = parse_pdf(self.root / doc['file'], doc)
            self.stage(job_id, 'indexing')
            with self.model_lock:
                vectors = np.asarray(self.embeddings.embed_documents([c['content'] for c in chunks]), dtype='float32')
            if vectors.ndim != 2 or len(vectors) != len(chunks) or not np.isfinite(vectors).all():
                raise ValueError('向量维度或数值校验失败')
            version = uid(); folder = self.root / version; folder.mkdir()
            index = faiss.IndexFlatL2(vectors.shape[1]); index.add(vectors)
            faiss.write_index(index, str(folder / 'index.faiss'))
            np.save(folder / 'vectors.npy', vectors, allow_pickle=False)
            data = dict(schema=1, version=version, document_version=doc['version'], chunks=chunks,
                bm25_tokens=[tokens(c['content']) for c in chunks])
            (folder / 'snapshot.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
            manifest = {name: digest(folder / name) for name in ('index.faiss', 'vectors.npy', 'snapshot.json')}
            (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
            loaded = faiss.read_index(str(folder / 'index.faiss'))
            if loaded.ntotal != len(chunks): raise ValueError('候选索引数量校验失败')
            # Atomic commit is the only publication point. Recheck tombstone/generation.
            with self.p.lock, self.p.db:
                live = self.p.db.execute('SELECT * FROM kb_documents WHERE id=?', (doc['id'],)).fetchone()
                current = self.p.db.execute('SELECT state FROM kb_jobs WHERE id=?', (job_id,)).fetchone()
                if not live or live['deleted'] or live['generation'] != job['generation'] or current['state'] == 'cancelled':
                    raise InterruptedError()
                self.p.db.execute('INSERT INTO kb_indexes VALUES(?,?,?,?,?)', (version, doc['id'], doc['version'], json.dumps(manifest), now()))
                pages=max(c['page_num'] for c in chunks)
                self.p.db.execute('INSERT OR IGNORE INTO kb_versions VALUES(?,?,?,?)',(doc['id'],doc['version'],doc['file'],pages))
                self.p.db.execute("UPDATE kb_documents SET active_index=?,state='ready',error=NULL,pages=?,version=?,file=? WHERE id=?",
                    (version,pages,doc['version'],doc['file'],doc['id']))
                self.p.db.execute("UPDATE kb_jobs SET state='ready',index_version=?,updated_at=? WHERE id=?", (version, now(), job_id))
        except InterruptedError:
            self.stage(job_id, 'cancelled')
        except Exception as error:
            # Internal error types only in persisted public status, never secrets/paths.
            reason = str(error) if isinstance(error, ValueError) else f'处理失败（{type(error).__name__}），可重试；旧索引仍有效'
            with self.p.lock, self.p.db:
                self.p.db.execute("UPDATE kb_jobs SET state='failed',error=?,updated_at=? WHERE id=?", (reason, now(), job_id))
                self.p.db.execute("UPDATE kb_documents SET state=CASE WHEN active_index IS NULL THEN 'failed' ELSE 'ready' END,error=? WHERE id=? AND deleted=0", (reason, doc['id']))

    def stage(self, identifier, stage):
        with self.p.lock, self.p.db:
            self.p.db.execute('UPDATE kb_jobs SET state=?,updated_at=? WHERE id=?', (stage, now(), identifier))

    def process_delete(self, job):
        """Serialized after any in-flight build; only tombstoned managed resources.
        Dataset paths never appear in this database/storage root.
        """
        import shutil
        try:
            self.stage(job['id'],'deleting')
            doc=self.p.one('SELECT * FROM kb_documents WHERE id=? AND deleted=1',(job['doc_id'],))
            if not doc: raise ValueError('清理目标没有删除墓碑')
            names={doc['file']}|{v['file'] for v in self.p.rows('SELECT file FROM kb_versions WHERE doc_id=?',(doc['id'],))}
            for row in self.p.rows('SELECT candidate FROM kb_jobs WHERE doc_id=?',(doc['id'],)):
                if row['candidate']: names.add(json.loads(row['candidate'])['file'])
            paths=[self.root/name for name in names]
            # Include unpublished candidates left by a process interruption. Their
            # immutable snapshot must contain only this tombstoned document.
            for folder in self.root.iterdir():
                if folder.is_dir() and not folder.is_symlink() and (folder/'snapshot.json').is_file():
                    snapshot=json.loads((folder/'snapshot.json').read_text())
                    if snapshot['chunks'] and {c['doc_id'] for c in snapshot['chunks']}=={doc['id']}: paths.append(folder)
            for path in paths:
                resolved=path.resolve()
                if resolved.parent!=self.root or path.is_symlink(): raise ValueError('清理路径超出管理库')
            for path in paths:
                if path.is_dir():shutil.rmtree(path)
                elif path.exists():path.unlink()
            self.stage(job['id'],'deleted')
            self.p.audit(job['owner_id'],'purge_deleted_files',doc['id'],{'files':len(paths)})
        except Exception:
            with self.p.lock,self.p.db:
                self.p.db.execute("UPDATE kb_jobs SET state='failed',error='后台物理清理失败；访问已撤销，可由管理员重试',updated_at=? WHERE id=?",(now(),job['id']))

    def enqueue(self, doc, candidate=None):
        with self.p.lock, self.p.db:
            existing = self.p.db.execute("SELECT id FROM kb_jobs WHERE doc_id=? AND state IN ('queued','parsing','indexing')", (doc['id'],)).fetchone()
            if existing: return existing['id']
            identifier = uid()
            self.p.db.execute('INSERT INTO kb_jobs(id,doc_id,generation,state,created_at,updated_at,owner_id,candidate) VALUES(?,?,?,?,?,?,?,?)',
                (identifier, doc['id'], doc['generation'], 'queued', now(), now(), doc['owner_id'],json.dumps(candidate) if candidate else None))
        self.wake.set(); return identifier

    def pin(self, user, scope):
        """Scope selection occurs before vectors/BM25 are loaded or scored."""
        self.p.live_user(user['id'])
        accessible = self.p.accessible(user)
        requested = scope.get('kb_ids')
        selected = set(accessible) if requested is None else set(requested)
        if not selected.issubset(accessible): raise HTTPException(403, '范围包含无权访问或不存在的知识库')
        docs = [d for d in self.p.documents(user) if d['kb_id'] in selected and d['active_index']]
        ids = scope.get('doc_ids')
        if ids is not None:
            known = {d['id'] for d in self.p.documents(user) if d['kb_id'] in selected}
            if not set(ids).issubset(known): raise HTTPException(403, '文档不属于当前授权范围')
            docs = [d for d in docs if d['id'] in ids]
        tags = set(scope.get('tags') or [])
        docs = [d for d in docs if tags.issubset(set(json.loads(d['tags'])))]
        field = scope.get('time_field', 'published_on')
        start, end = scope.get('from_date'), scope.get('to_date')
        if start or end:
            docs = [d for d in docs if d.get(field) and (not start or d[field][:10] >= start) and (not end or d[field][:10] <= end)]
        return ScopedSnapshot(self, user, scope, docs)


class ScopedSnapshot:
    def __init__(self, knowledge, user, scope, docs):
        self.k, self.user, self.scope, self.docs = knowledge, dict(user), dict(scope), docs
        self.chunks, vectors = [], []
        self.versions = {d['id']: d['active_index'] for d in docs}
        self.index_version = hashlib.sha256(json.dumps(self.versions, sort_keys=True).encode()).hexdigest()
        self.documents = {d['id']: dict(pages=d['pages']) for d in docs}
        for doc in docs:
            folder = self.k.root / doc['active_index']
            manifest = json.loads((folder / 'manifest.json').read_text())
            if any(digest(folder / name) != checksum for name, checksum in manifest.items()): raise ValueError('已发布快照校验失败')
            snapshot = json.loads((folder / 'snapshot.json').read_text())
            self.chunks.extend(snapshot['chunks']); vectors.append(np.load(folder / 'vectors.npy', allow_pickle=False))
        self.vectors = np.concatenate(vectors) if vectors else None
        # One immutable index/token corpus per request, reused by subqueries and
        # recovery instead of rebuilding both for every tool invocation.
        self.vector_index = None
        if self.vectors is not None:
            self.vector_index = faiss.IndexFlatL2(self.vectors.shape[1]); self.vector_index.add(self.vectors)
        self.lexical_index = BM25Okapi([tokens(c['file_name']+'\n'+c['content']) for c in self.chunks]) if self.chunks else None

    def allowed(self, doc_id):
        current = self.k.p.live_user(self.user['id'])
        doc = self.k.p.one('SELECT * FROM kb_documents WHERE id=? AND deleted=0', (doc_id,))
        return bool(doc and self.k.p.can_read(current, doc['kb_id']))

    def assert_access(self):
        if any(not self.allowed(d['id']) for d in self.docs): raise PermissionError('回答期间资料权限已变化')

    async def prepare_v2(self, query, memory_id, history):
        self.workflow_agent = self.create_agent()
        prepared = await self.workflow_agent.prepare_v2(query, memory_id, history)
        for item in prepared.get('observations', []):
            item.update(index_version=self.index_version, candidate_domain=len(self.chunks))
        return prepared

    def create_agent(self):
        from agent.orchestrator import FinancialRAGAgent
        from agent.llm.base_llm import get_llm
        from langchain_core.documents import Document
        def document(chunk):
            return Document(page_content=chunk['content'],metadata={k:v for k,v in {**chunk,'score_kind':'rrf'}.items() if k!='content'})
        def search_fn(query, mode='hybrid', use_reranker=True):
            return [document(c) for c in self.search(query,mode=mode,use_reranker=use_reranker)]
        return FinancialRAGAgent.from_retriever(search_fn,[document(c) for c in self.chunks],
            llm_factory=getattr(self,'llm_factory',get_llm))

    def clear_history(self, identifier):
        if hasattr(self,'workflow_agent'):self.workflow_agent.clear_history(identifier)

    def expand_tables(self, selected, scores):
        # A recalled numeric row must carry its registered table headers/units.
        # Expansion only visits this request's already-authorized immutable chunks.
        result=[]
        tables=[(i,c) for i,c in enumerate(self.chunks) if c.get('table')]
        for i in selected:
            child=self.chunks[i];parents=[]
            if not child.get('table') and child.get('regions'):
                for j,parent in tables:
                    if any(parent[key]!=child[key] for key in ('doc_id','doc_version','page_num')):continue
                    # Text blocks include trailing glyph whitespace (observed 2.1pt
                    # in the real report). Association tolerates 3pt; the displayed
                    # source rectangle remains the exact parser table rectangle.
                    if all(any((pymupdf.Rect(box)+(-3,-3,3,3)).contains(pymupdf.Rect(region)) for box in parent['regions']) for region in child['regions']):parents.append(j)
            for j in parents or [i]:
                scores[j]=max(scores.get(j,0),scores.get(i,0))
                if j not in result:result.append(j)
        return result

    def search(self, query, *, mode='hybrid', use_reranker=True):
        self.assert_access()
        if not self.chunks: return []
        count = min(20, len(self.chunks)); rankings=[]
        if mode in ('vector','hybrid'):
            with self.k.model_lock:
                q = np.asarray([self.k.embeddings.embed_query(query)], dtype='float32')
            _, found = self.vector_index.search(q,count); rankings.append(found[0])
        if mode in ('bm25','hybrid'):
            scores=self.lexical_index.get_scores(tokens(query)); rankings.append(np.argsort(-scores)[:count])
        rrf = {}
        for ranking in rankings:
            for rank, i in enumerate(ranking): rrf[int(i)] = rrf.get(int(i), 0) + 1/(60+rank+1)
        selected = sorted(rrf, key=rrf.get, reverse=True)[:20]
        selected=self.expand_tables(selected,rrf)
        # Financial grids can rank below narrative snippets because they carry
        # mostly numbers. Reserve a small recall lane for real table headers
        # matching an explicitly requested year, then let the same reranker
        # judge them. This uses no inferred cells, company values or answers.
        years=set(re.findall(r'(?:19|20)\d{2}',query))
        if years and mode in ('bm25','hybrid'):
            table_indices=[]
            for i,c in enumerate(self.chunks):
                if not c.get('table'):continue
                headers=c['table'].get('headers') or next(iter(c['table'].get('rows',[])),[])
                header_years=set()
                for cell in headers:
                    match=re.fullmatch(r'((?:19|20)\d{2})(?:[AEae]|年)?',re.sub(r'\s+','',str(cell)).replace(',',''))
                    if match:header_years.add(match[1])
                # Chart captions and axis strings are not year-column tables.
                if len(header_years)>=2 and years & header_years:table_indices.append(i)
            lexical_order=np.argsort(-scores).tolist()
            for i in sorted(table_indices,key=lambda i:scores[i],reverse=True)[:5]:
                if i not in selected:selected.append(i)
                rrf.setdefault(i,1/(60+lexical_order.index(i)+1))
        # Same-version chunks only, scores from one reranker rather than cross-library scales.
        if use_reranker and getattr(self.k, 'rerank', True):
            from rag.retrievers.reranker import LangchainReranker
            from langchain_core.documents import Document
            with self.k.model_lock:
                if self.k.reranker is None: self.k.reranker = LangchainReranker()
                # File identity is retrieval context, not inserted into the PDF
                # excerpt or its coordinates. Tables often omit the company name.
                ranked = self.k.reranker.compress_documents([Document(page_content=self.chunks[i]['file_name']+'\n'+self.chunks[i]['content'], metadata={'i':i}) for i in selected], query)
            selected = [d.metadata['i'] for d in ranked]
        self.assert_access()
        return [{**self.chunks[i], 'score': rrf[i]} for i in selected[:12]]

    def snapshots(self, docs):
        self.assert_access(); result = []
        registered = {c['chunk_id']: c for c in self.chunks}
        for i, raw in enumerate(docs, 1):
            c = registered.get(raw['chunk_id'])
            if not c or c['content'] != raw['content']: continue
            result.append(dict(id=i, doc_id=c['doc_id'], doc_version=c['doc_version'], index_version=self.index_version,
                doc_name=c['file_name'], page=c['page_num'], page_label=c['page_label'], chunk_id=c['chunk_id'],
                snippet=c['content'], original_length=len(c['content']), truncated=False, bbox=c['regions'][0] if c['regions'] else None,
                regions=c['regions'], geometry=c['geometry'], table=c['table'], element_type=c['element_type'],
                source_verified=True, included_in_prompt=False, unavailable_reason=None, score=raw.get('score')))
        return result

    def resolve(self, doc_id, version):
        if not self.allowed(doc_id): raise FileNotFoundError('原文已删除或无权限')
        doc = next((d for d in self.docs if d['id']==doc_id and d['version']==version), None)
        if not doc: raise FileNotFoundError('快照不包含此文档版本')
        path = self.k.root / doc['file']
        if digest(path) != version: raise ValueError('原文哈希不一致')
        return path

    def validate(self, text, candidates):
        # Reuse the P1 conservative Markdown citation parser, replacing only the
        # page text check with exact trusted parser-element provenance (tables too).
        self.assert_access()
        proxy = DocumentRegistry.__new__(DocumentRegistry)
        proxy.index_version = self.index_version
        from docker.backend.documents import compact
        proxy.documents = {d['id']: dict(pages=d['pages'], texts=['']*d['pages']) for d in self.docs}
        for c in self.chunks: proxy.documents[c['doc_id']]['texts'][c['page_num']-1] += compact(c['content'])
        proxy.resolve = self.resolve
        return proxy.validate(text, candidates)
