"""Application startup and current API; no external model calls."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from agent.test.fixtures import Embeddings
from configs import rag_config


class WebApiTest(unittest.TestCase):
    def test_empty_application_starts_without_legacy_index_accounts_or_model_api(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);sources=root/'raw';sources.mkdir()
            spec=importlib.util.spec_from_file_location('isolated_web_api',Path(__file__).resolve().parents[2]/'docker/backend/api.py')
            module=importlib.util.module_from_spec(spec)
            with patch.dict(os.environ,{'CHAT_DB_PATH':str(root/'chat.sqlite3'),'PLATFORM_ROOT':folder}),\
                patch('rag.embeddings.embed_model.LocalEmbeddings',return_value=Embeddings()),\
                patch.object(rag_config,'RAW_PDF_DIR',str(sources)),\
                patch('rag.load_vector_store',side_effect=AssertionError('Do not load legacy corpus')):
                spec.loader.exec_module(module)
            with TestClient(module.app) as client:
                self.assertEqual(client.get('/health').json()['pipeline'],'shared-agent-graph-v1')
                self.assertEqual(client.get('/api/v2/knowledge-base').json()['document_count'],0)
                self.assertEqual(client.get('/api/v2/sessions').json(),[])
                self.assertEqual(client.get('/api/v2/auth/status').status_code,404)
                self.assertEqual(client.post('/api/chat',json={'query':'old'}).status_code,404)
                session=client.post('/api/v2/sessions').json()
                self.assertEqual(client.post('/api/v2/chat',json={'query':' ','session_id':session['id'],'request_id':'bad'}).status_code,422)
                tools=client.get('/api/v2/tools').json()
                self.assertEqual({t['name'] for t in tools},{'report_search','context_expand','report_catalog','metric_calculate'})
                response=client.post('/api/v2/tools/metric_calculate',json={'arguments':{'operation':'growth_rate','values':[125,100]}}).json()
                self.assertEqual(response['result']['result'],25)
                invalid=client.post('/api/v2/tools/metric_calculate',json={'arguments':{'operation':'ratio','values':[125,0]}}).json()
                self.assertEqual(invalid['observation']['error_type'],'invalid_arguments')
                self.assertEqual(client.post('/api/v2/tools/unknown',json={}).status_code,404)
