"""Explicit fixture models/PDFs; never used by the production application."""
import hashlib
import json
import pymupdf
from langchain_core.messages import AIMessage, AIMessageChunk


class Embeddings:
    def embed_query(self,text):
        from docker.backend.knowledge import tokens
        vector=[0.0]*32
        for word in tokens(text):vector[int(hashlib.sha256(word.encode()).hexdigest()[:4],16)%32]+=1
        return vector

    def embed_documents(self,texts):return [self.embed_query(text) for text in texts]


class Model:
    def invoke(self,messages):
        return AIMessage(content=json.dumps({'task_type':'fact','rewritten_query':'Revenue',
            'sub_queries':['Revenue'],'filters':{},'use_history':False}))

    async def astream(self,messages):
        text=messages[0]['content'] if isinstance(messages[0],dict) else messages[0].content
        yield AIMessageChunk(content='Revenue is 125 million USD [1].' if '125' in text else 'Revenue is 275 million USD [1].')


def pdf_bytes(value=125,rotated=False,table=False):
    pdf=pymupdf.open();page=pdf.new_page(width=500,height=600)
    page.insert_text((40,55),f'FIXTURE ONLY / Beta regression v1. Revenue is {value} million USD.')
    if table:
        for x in (40,190,350):page.draw_line((x,100),(x,190))
        for y in (100,130,160,190):page.draw_line((40,y),(350,y))
        for x,y,text in [(50,120,'Year'),(200,120,'Revenue (USD m)'),(50,150,'2022'),(200,150,str(value)),(50,180,'2023'),(200,180,'150')]:page.insert_text((x,y),text)
        page.insert_text((40,215),'Unit: million USD. Footnote: audited fixture data only.')
    if rotated:page.set_rotation(90)
    result=pdf.tobytes(no_new_id=True);pdf.close();return result
