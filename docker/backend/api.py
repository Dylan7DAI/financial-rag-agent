"""Single-workspace Web API using the shared FinancialRAGAgent graph."""
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from configs import rag_config
from docker.backend.chat_store import ChatStore
from docker.backend.single_workspace import SingleWorkspace
from docker.backend.v2 import create_router


def create_app(*,embeddings=None,root=None,source_dir=None,llm_factory=None):
    if embeddings is None:
        from rag.embeddings.embed_model import LocalEmbeddings
        embeddings=LocalEmbeddings()
    root=Path(root or os.getenv('PLATFORM_ROOT','runtime'))
    store=ChatStore(os.getenv('CHAT_DB_PATH',str(root/'chat.sqlite3')))
    workspace=SingleWorkspace(store,embeddings,root,source_dir=source_dir if source_dir is not None else rag_config.RAW_PDF_DIR)

    @asynccontextmanager
    async def lifespan(app):
        workspace.knowledge.start()
        try: yield
        finally:
            workspace.knowledge.stop.set();workspace.knowledge.wake.set()
            workspace.knowledge.thread.join(timeout=5)
            if not workspace.knowledge.thread.is_alive():store.close()

    app=FastAPI(title='Financial RAG Agent',version='2.0',lifespan=lifespan)
    app.state.workspace=workspace
    app.add_middleware(CORSMiddleware,allow_origins=os.getenv('APP_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(','),
        allow_methods=['GET','POST','PATCH','DELETE','OPTIONS'],allow_headers=['Content-Type'])
    app.middleware('http')(workspace.middleware)
    app.include_router(workspace.router())
    options={'llm_factory':llm_factory} if llm_factory else {}
    app.include_router(create_router(type('WorkspaceAgent',(),{'clear_history':lambda *a:None})(),store=store,platform=workspace,**options))

    @app.get('/health')
    def health():return {'status':'ok','mode':'single','pipeline':'shared-agent-graph-v1'}
    return app


app=create_app()
