"""python -m evals.p2_eval --output runtime/evals/p2-offline.json

Offline retrieval/provenance checks use real parser/FAISS/BM25 and explicitly
fixture embeddings. Factual correctness/refusal/first token require online
generation and are NOT inferred from valid references or fixture answers.
"""
import argparse
import hashlib
import json
from pathlib import Path
from agent.test.test_knowledge import KnowledgeTest
from agent.test.fixtures import pdf_bytes
from evals.p2_cases import CASES,CORPUS_VERSION,EXPECTED_PDF_SHA256,EXPECTED_FAISS_SHA256
from docker.backend.documents import digest


def run():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='runtime/evals/p2-offline.json');args=parser.parse_args()
    t=KnowledgeTest();t.setUp()
    try:
        a=t.upload(table=True);b=t.upload(275)
        pdf_hashes={'alpha':hashlib.sha256(pdf_bytes(table=True)).hexdigest(),'beta':hashlib.sha256(pdf_bytes(275)).hexdigest()}
        index_hashes={name:digest(t.p.knowledge.root/doc['active_index']/'index.faiss') for name,doc in [('alpha',a),('beta',b)]}
        if pdf_hashes!=EXPECTED_PDF_SHA256 or index_hashes!=EXPECTED_FAISS_SHA256:raise RuntimeError('固定语料/索引快照发生变化；核验变更并建立新评测版本，不沿用原分母')
        ids={'alpha':a['id'],'beta':b['id']};raw=[];recall_n=recall_d=0;valid_n=valid_d=0
        for case in CASES:
            snapshot=t.pin({'doc_ids':[ids[n] for n in case['docs']]})
            found=snapshot.search(case['question']);candidates=snapshot.snapshots(found)
            expected=set(case['docs']) if case['answerable'] else set()
            hit={name for name,identifier in ids.items() if any(c['doc_id']==identifier for c in candidates)}
            support=case['support'] if isinstance(case['support'],list) else [case['support']] if case['support'] else []
            support_found=all(any(s in c['snippet'] for c in candidates) for s in support)
            if expected:recall_d+=len(expected);recall_n+=len(expected & hit)
            for c in candidates:
                c['included_in_prompt']=True
                valid_d+=1;valid_n+=bool(snapshot.validate(f'Protocol fixture [{c["id"]}]',[c]))
            raw.append({**case,'scope_document_ids':[ids[n] for n in case['docs']], 'index_version':snapshot.index_version,
                'retrieved_chunk_ids':[c['chunk_id'] for c in candidates], 'retrieved_document_versions':sorted({c['doc_version'] for c in candidates}),
                'support_excerpt_found':support_found if support else None,'retrieval_expected_document_hits':len(expected & hit),
                'factual_correctness':None,'claim_support':None,'refusal_correctness':None,'online_status':'SKIP: offline; no model calls'})
        result={'schema':1,'corpus_version':CORPUS_VERSION,'kind':'OFFLINE_FIXTURE_EMBEDDINGS_REAL_PARSER_FAISS_BM25',
            'corpus_sha256':pdf_hashes,'faiss_sha256':index_hashes,
            'metrics':{'fixture_document_recall':{'numerator':recall_n,'denominator':recall_d},
                'fixture_citation_page_chunk_integrity':{'numerator':valid_n,'denominator':valid_d},
                'fact_correctness':None,'claim_support':None,'refusal_correctness':None,'first_token_ms':None},
            'notes':['引用完整性不是事实正确率。','reg-unanswerable 仅检查样本规范，离线不评判拒答。','点踩样本仅经人工核验、脱敏后加入下一版本。','开发集和回归集互斥；新版本比较原始样本与分项分子分母，不合并为准确率。'], 'samples':raw}
        path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({'corpus_version':CORPUS_VERSION,'samples':len(raw),'metrics':result['metrics'],'online':'SKIPPED'},ensure_ascii=False))
        if recall_n!=recall_d or valid_n!=valid_d or any(r['support_excerpt_found'] is False for r in raw):raise SystemExit(1)
    finally:t.tearDown()

if __name__=='__main__':run()
