"""Read-only registration of the current corpus; never rewrites an existing index."""
from __future__ import annotations
import hashlib
import json
import math
import re
from pathlib import Path
import pymupdf


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def compact(text: str) -> str:
    return re.sub(r'\s+', '', text)


class DocumentRegistry:
    def __init__(self, root: str, index: str):
        self.root = Path(root).resolve()
        self.index = Path(index)
        manifest = self.index / 'all_documents.json'
        self.chunks = {}
        self.documents = {}
        self.by_name = {}
        h = hashlib.sha256()
        for name in ('index.faiss', 'index.pkl', 'all_documents.json'):
            path = self.index / name
            h.update(name.encode())
            h.update((digest(path) if path.exists() else 'missing').encode())
        self.index_version = h.hexdigest()
        items = json.loads(manifest.read_text(encoding='utf8')) if manifest.exists() else []
        for item in items:
            meta = item.get('metadata', {})
            name = meta.get('file_name') or meta.get('source', '')
            # Only exact basenames already present in the corpus are registered.
            if not name or Path(name).name != name or '/' in name or '\\' in name:
                continue
            doc_id = 'doc_' + hashlib.sha256(name.encode()).hexdigest()[:24]
            if doc_id not in self.documents:
                path = (self.root / name).resolve()
                if path.parent != self.root:
                    continue
                entry = dict(doc_id=doc_id, doc_name=name, path=path, doc_version=None, pages=0, labels=[], texts=[])
                if path.is_file():
                    try:
                        with pymupdf.open(path) as pdf:
                            entry.update(doc_version=digest(path), pages=len(pdf),
                                         labels=[page.get_label() or None for page in pdf],
                                         texts=[compact(page.get_text()) for page in pdf])
                    except Exception:
                        pass # Missing/corrupt originals are represented as unavailable.
                self.documents[doc_id] = entry
                self.by_name[name] = entry
            key = (name, str(meta.get('page_num')), str(meta.get('chunk_id', '')))
            self.chunks[key] = item.get('page_content', '')

    def catalog(self):
        return [dict(doc_id=d['doc_id'], doc_name=d['doc_name'], doc_version=d['doc_version'],
                     pages=d['pages'], index_version=self.index_version, available=bool(d['pages']))
                for d in self.documents.values()]

    def resolve(self, doc_id: str, version: str):
        doc = self.documents.get(doc_id)
        if not doc or not doc['doc_version'] or not doc['path'].is_file():
            raise FileNotFoundError('原文不可用')
        if doc['path'].resolve().parent != self.root:
            raise FileNotFoundError('原文不可用')
        if version != doc['doc_version'] or digest(doc['path']) != version:
            raise ValueError('原文版本已变化，请重新提问后核验')
        return doc['path']

    def snapshots(self, docs):
        snapshots = []
        for number, raw in enumerate(docs[:30], 1):
            name = str(raw.get('file_name', ''))
            chunk = str(raw.get('chunk_id', ''))
            value = str(raw.get('page_num', 0))
            page = int(value) if re.fullmatch(r'[1-9]\d*', value) else 0
            original = str(raw.get('content', ''))
            snippet = original[:500]
            doc = self.by_name.get(name)
            reason = None
            if not doc or not doc['pages']: reason = '原文未注册或不可用'
            elif not 1 <= page <= doc['pages']: reason = '物理页码不存在'
            elif self.chunks.get((name, str(page), chunk)) != original: reason = '片段与已加载索引不一致'
            elif not snippet.strip() or compact(snippet) not in doc['texts'][page - 1]: reason = '片段无法在当前PDF物理页逐字核验'
            snapshots.append(dict(id=number, doc_id=doc['doc_id'] if doc else '',
                doc_version=doc['doc_version'] if doc else None, index_version=self.index_version,
                doc_name=name, page=page, page_label=doc['labels'][page - 1] if doc and 1 <= page <= doc['pages'] else None,
                chunk_id=chunk, snippet=snippet, original_length=len(original), truncated=len(original) > 500,
                bbox=None, score=raw.get('score') if isinstance(raw.get('score'), (int,float)) and math.isfinite(raw['score']) else None,
                source_verified=reason is None, included_in_prompt=False, unavailable_reason=reason))
        return snapshots

    def validate(self, text, candidates):
        # Conservative Markdown exclusion: closed/unclosed fences, indented code,
        # inline code and escaped brackets cannot establish a visible citation.
        lines, fence = [], None
        for line in text.splitlines():
            structural = re.sub(r'^(?: {0,3}>\s?)+', '', line)
            while True:
                stripped = re.sub(r'^ {0,3}(?:[-+*]|\d+[.)])\s+', '', structural)
                if stripped == structural: break
                structural = stripped
            marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', structural)
            if fence:
                if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip(): fence = None
                continue
            if marker: fence = marker[1]; continue
            if re.match(r'^( {4}|\t)', structural): continue
            lines.append(line)
        clean = re.sub(r'(`+)[\s\S]*?\1', '', '\n'.join(lines))
        clean = re.sub(r'\\.', '', clean)
        ids = set(int(value) for value in re.findall(r'\[(\d+)\](?!\()', clean))
        allowed = {c['id']: c for c in candidates if c['source_verified'] and c.get('included_in_prompt')}
        if not ids or not ids.issubset(allowed):
            return None
        used = [allowed[key] for key in sorted(ids)]
        for item in used:
            try: self.resolve(item['doc_id'], item['doc_version'])
            except (FileNotFoundError, ValueError): return None
            if item['index_version'] != self.index_version: return None
            doc = self.documents[item['doc_id']]
            page = item['page']
            if not 1 <= page <= doc['pages'] or compact(item['snippet']) not in doc['texts'][page - 1]: return None
        return used
