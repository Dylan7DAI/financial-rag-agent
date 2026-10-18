# -*- coding: utf-8 -*-
from typing import Any, Dict, List, Callable

from langchain_core.documents import Document
from langchain_core.tools import tool


class StructuredRagSearchAdapter:
    """
    接收包装好的 search_fn，支持 structured query：
    - 多 sub_queries 分别调用已有 search_fn(query, mode, use_reranker)
    - 标准化底层返回结果
    - 去重
    - 后过滤
    - 均衡融合
    """

    def __init__(self, search_fn: Callable, final_top_k: int = 5):
        if not callable(search_fn):
            raise TypeError("search_fn 必须是可调用对象")
        self.search_fn = search_fn
        self.final_top_k = final_top_k

    def search(
        self,
        structured_query: Dict[str, Any],
        mode: str = "hybrid",
        use_reranker: bool = True,
    ) -> Dict[str, Any]:
        if not isinstance(structured_query, dict):
            raise TypeError("structured_query 必须是 dict")

        sub_queries = structured_query.get("sub_queries", []) or []
        rewritten_query = structured_query.get("rewritten_query", "") or ""
        filters = structured_query.get("filters", {}) or {}
        exclude_terms = structured_query.get("exclude_terms", []) or []
        required_terms = structured_query.get("required_terms", []) or []

        if not sub_queries:
            if rewritten_query:
                sub_queries = [rewritten_query]
            else:
                raise ValueError("structured_query 至少需要 sub_queries 或 rewritten_query")

        all_docs: List[Dict[str, Any]] = []

        for sq in sub_queries: #对输入的每一个子查询都调用一次底层检索接口，得到的结果会带上一个字段 "retrieved_by" 标记是由哪个子查询检索到的
            raw_results = self.search_fn(
                query=sq,
                mode=mode,
                use_reranker=use_reranker,
            )
            docs = self._normalize_results(raw_results)

            for d in docs:
                d["retrieved_by"] = sq

            docs = self._apply_filters(docs, filters)
            docs = self._apply_exclude_terms(docs, exclude_terms)
            docs = self._apply_required_terms(docs, required_terms)

            all_docs.extend(docs)

        docs = self._deduplicate_docs(all_docs)
        docs = self._balance_docs_by_subquery(
            docs,
            sub_queries=sub_queries,
            final_top_k=structured_query.get("top_k", self.final_top_k),
        )

        return {
            "query": structured_query,
            "docs": docs,
        }

    def as_langchain_tool(self):
        @tool("structured_rag_search")
        def structured_rag_search(query_json: str) -> str:
            """
            搜索金融研报知识库，支持结构化复杂查询 JSON。
            """
            import json

            structured_query = json.loads(query_json)
            result = self.search(structured_query=structured_query)
            docs = result["docs"]

            if not docs:
                return "未检索到相关研报内容。"

            formatted = []
            for i, d in enumerate(docs, 1):
                formatted.append(
                    f"[片段{i}]\n"
                    f"文件名: {d.get('file_name', '')}\n"
                    f"页码: {d.get('page_num', '')}\n"
                    f"子查询: {d.get('retrieved_by', '')}\n"
                    f"内容: {d.get('content', '')}\n"
                )
            return "\n".join(formatted)

        return structured_rag_search

    def _normalize_results(self, results: Any) -> List[Dict[str, Any]]: #同样的函数在rag_tool里也有
        """
        将底层 search_fn 返回统一标准化
        兼容：
        - List[Document]
        - Dict
        - List[Dict]
        - 单个 Document
        """
        normalized_docs: List[Dict[str, Any]] = []

        if not results:
            return normalized_docs

        if isinstance(results, dict):
            results = results.get("docs", results.get("documents", []))

        if isinstance(results, Document):
            results = [results]

        if isinstance(results, list):
            for idx, item in enumerate(results):
                if isinstance(item, Document):
                    metadata = item.metadata or {}
                    normalized_docs.append(
                        {
                            "doc_id": metadata.get("doc_id", f"doc_{idx}"),
                            "chunk_id": metadata.get("chunk_id", f"chunk_{idx}"),
                            "file_name": metadata.get("file_name", ""),
                            "page_num": metadata.get("page_num", ""),
                            "content": item.page_content,
                            "score": metadata.get("score", None),
                            "metadata": metadata,
                        }
                    )
                elif isinstance(item, dict):
                    metadata = item.get("metadata", {}) or {}
                    normalized_docs.append(
                        {
                            "doc_id": item.get("doc_id", f"doc_{idx}"),
                            "chunk_id": item.get("chunk_id", f"chunk_{idx}"),
                            "file_name": item.get("file_name", metadata.get("file_name", "")),
                            "page_num": item.get("page_num", metadata.get("page_num", "")),
                            "content": item.get("content", item.get("page_content", "")),
                            "score": item.get("score", metadata.get("score", None)),
                            "metadata": metadata,
                        }
                    )

        return normalized_docs

    def _apply_filters(self, docs: List[Dict[str, Any]], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not filters:
            return docs

        values = [str(v).strip() for v in filters.values() if v not in ("", None, [], {})]
        if not values:
            return docs

        filtered = []
        for d in docs:
            text = f"{d.get('content', '')} {d.get('metadata', {})}".lower()
            if all(v.lower() in text for v in values):
                filtered.append(d)

        return filtered

    def _apply_exclude_terms(self, docs: List[Dict[str, Any]], exclude_terms: List[str]) -> List[Dict[str, Any]]:
        if not exclude_terms:
            return docs

        filtered = []
        for d in docs:
            text = d.get("content", "").lower()
            if any(term.lower() in text for term in exclude_terms):
                continue
            filtered.append(d)
        return filtered

    def _apply_required_terms(self, docs: List[Dict[str, Any]], required_terms: List[str]) -> List[Dict[str, Any]]:
        if not required_terms:
            return docs

        filtered = []
        for d in docs:
            text = d.get("content", "").lower()
            if all(term.lower() in text for term in required_terms):
                filtered.append(d)

        return filtered

    def _deduplicate_docs(self, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = {}
        for d in docs:
            key = (
                str(d.get("file_name", "")),
                str(d.get("page_num", "")),
                str(d.get("chunk_id", "")),
            )
            if key not in seen:
                seen[key] = d
        return list(seen.values())

    def _balance_docs_by_subquery(
        self,
        docs: List[Dict[str, Any]],
        sub_queries: List[str],
        final_top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        buckets = {sq: [] for sq in sub_queries}
        others = []

        for d in docs:
            sq = d.get("retrieved_by")
            if sq in buckets:
                buckets[sq].append(d)
            else:
                others.append(d)

        result = []
        seen = set()

        while len(result) < final_top_k:
            moved = False
            for sq in sub_queries:
                if buckets[sq]:
                    d = buckets[sq].pop(0)
                    key = (
                        str(d.get("file_name", "")),
                        str(d.get("page_num", "")),
                        str(d.get("chunk_id", "")),
                    )
                    if key not in seen:
                        seen.add(key)
                        result.append(d)
                        moved = True
                        if len(result) >= final_top_k:
                            break
            if not moved:
                break

        if len(result) < final_top_k:
            for d in others:
                key = (
                    str(d.get("file_name", "")),
                    str(d.get("page_num", "")),
                    str(d.get("chunk_id", "")),
                )
                if key not in seen:
                    seen.add(key)
                    result.append(d)
                    if len(result) >= final_top_k:
                        break

        return result


def create_structured_rag_search_adapter(search_fn: Callable, final_top_k: int = 5) -> StructuredRagSearchAdapter:
    return StructuredRagSearchAdapter(
        search_fn=search_fn,
        final_top_k=final_top_k,
    )
