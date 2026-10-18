"""Shared graph with injected corpora; no legacy index or external models."""
import asyncio
import json
import unittest
from unittest.mock import Mock,patch
from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from agent.orchestrator import FinancialRAGAgent


def document(name,text='Revenue 125'):
    return Document(page_content=text,metadata={'doc_id':name,'chunk_id':name+'-1','file_name':name+'.pdf',
        'page_num':1,'score':.02,'score_kind':'rrf'})


class UnifiedAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_followup_reuses_question_without_model_rewrite_or_old_answer(self):
        search=Mock(return_value=[document('alpha')]);factory=Mock(side_effect=AssertionError('Unneeded model call'))
        agent=FinancialRAGAgent.from_retriever(search,[document('alpha')],llm_factory=factory)
        result=await agent.prepare_v2('那2021年呢？','one',[('海光信息2022年营业收入','UNTRUSTED_OLD_ANSWER')])
        query=search.call_args.kwargs['query']
        self.assertIn('海光信息',query);self.assertIn('2021',query);self.assertNotIn('UNTRUSTED',query)
        self.assertNotIn('2022',query,'A new explicit year replaces the preceding year')
        self.assertIn('营业收入',query,'A year-only follow-up keeps the preceding metric')
        self.assertTrue(result['retrieval_success']);factory.assert_not_called()
        self.assertEqual(result['debug_info']['rewrite_mode'],'rule')

    async def test_complex_query_uses_shared_rewrite_and_subqueries(self):
        model=Mock();model.invoke.return_value=AIMessage(content=json.dumps({'task_type':'compare','rewritten_query':'compare reports',
            'sub_queries':['alpha Revenue','beta Revenue'],'filters':{},'use_history':False}))
        search=Mock(side_effect=lambda query,**kw:[document('alpha' if 'alpha' in query else 'beta')])
        agent=FinancialRAGAgent.from_retriever(search,[document('alpha'),document('beta')],llm_factory=lambda:model)
        result=await agent.prepare_v2('比较两家公司的营收','two',[])
        self.assertEqual({d['doc_id'] for d in result['retrieved_docs']},{'alpha','beta'})
        self.assertEqual(search.call_count,2);model.invoke.assert_called_once()
        self.assertTrue(result['retrieval_success'],'RRF weights must not use the legacy score threshold')

    async def test_empty_retrieval_has_one_bounded_recovery(self):
        model=Mock();model.invoke.return_value=AIMessage(content=json.dumps({'task_type':'fact','rewritten_query':'Revenue',
            'sub_queries':['Revenue'],'filters':{},'use_history':False}))
        search=Mock(return_value=[])
        agent=FinancialRAGAgent.from_retriever(search,[],llm_factory=lambda:model,max_retries=1)
        result=await agent.prepare_v2('Revenue?','three',[])
        self.assertEqual(search.call_count,2);model.invoke.assert_called_once()
        self.assertFalse(result['retrieval_success']);self.assertEqual(result['generalization_count'],1)

    async def test_permanent_failure_never_generalizes_or_loads_models(self):
        search=Mock(side_effect=PermissionError('fixture denied'));factory=Mock(side_effect=AssertionError('Do not broaden'))
        agent=FinancialRAGAgent.from_retriever(search,[],llm_factory=factory)
        result=await agent.prepare_v2('Revenue?','four',[])
        self.assertFalse(result['retrieval_success']);self.assertEqual(search.call_count,1);factory.assert_not_called()

    async def test_concurrent_agents_and_all_four_tools_keep_their_own_corpus(self):
        with patch('agent.orchestrator.load_vector_store',side_effect=AssertionError('No legacy index')):
            agents=[FinancialRAGAgent.from_retriever(lambda **kw:[document('alpha')],[document('alpha')]),
                FinancialRAGAgent.from_retriever(lambda **kw:[document('beta')],[document('beta')])]
        results=await asyncio.gather(*(a.prepare_v2('Revenue?',str(i),[]) for i,a in enumerate(agents)))
        for name,agent,result in zip(('alpha','beta'),agents,results):
            self.assertEqual({d['doc_id'] for d in result['retrieved_docs']},{name})
            self.assertEqual(len(agent.list_tools()),4)
            catalog=agent.invoke_tool('report_catalog',{})['result']['reports']
            self.assertEqual([d['doc_id'] for d in catalog],[name])
            context=agent.invoke_tool('context_expand',{'doc_id':name,'chunk_id':name+'-1'})['result']['docs']
            self.assertEqual({d['doc_id'] for d in context},{name})
            self.assertEqual(agent.invoke_tool('context_expand',{'doc_id':'other','chunk_id':'other-1'})['result']['docs'],[])
            self.assertEqual(agent.invoke_tool('metric_calculate',{'operation':'growth_rate','values':[125,100]})['result']['result'],25)

    async def test_greeting_with_financial_question_still_retrieves(self):
        search=Mock(return_value=[document('alpha')])
        agent=FinancialRAGAgent.from_retriever(search,[document('alpha')])
        result=await agent.prepare_v2('你好，Revenue?','five',[])
        self.assertEqual(result['intent'],'rag_qa');search.assert_called_once()

    async def test_required_terms_and_filters_do_not_silently_return_nonmatching_docs(self):
        agent=FinancialRAGAgent.from_retriever(lambda **kw:[document('alpha')],[document('alpha')])
        for task in ('filter','fact'):
            for constraint in ({'required_terms':['NOT_PRESENT']},{'filters':{'company':'NOT_PRESENT'}}):
                payload={'task_type':task,'original_query':'Revenue','rewritten_query':'Revenue','sub_queries':['Revenue'],
                    'filters':{},'use_history':False,**constraint}
                self.assertEqual(agent.invoke_tool('report_search',{'structured_query':payload})['result']['docs'],[])
