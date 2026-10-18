"""Hand-authored fixture ground truth v1. Never mined from model responses.
Development and regression IDs are disjoint. PDFs generated deterministically.
"""
CORPUS_VERSION='beta-fixture-v1'
EXPECTED_PDF_SHA256={'alpha':'190a843556d587372d4b5e5b27ce64eb9db1422310ee67eac725bde6cf68a1e5','beta':'1eab868b11345dcdbafd8b158b65d125f8fe1ab67332e6a6b00196130536d623'}
EXPECTED_FAISS_SHA256={'alpha':'28b6ce2b646e34863e605a9a1e167b64d7f7cbf029bf9dd84bf4eb359b56c9b9','beta':'a5db2035f6ad186766327e30f87fcf74f104b6b1bfd03943b478ac5bf063f3a7'}
DOCUMENTS={'alpha':{'revenue':125,'unit':'million USD','page':1},'beta':{'revenue':275,'unit':'million USD','page':1}}
CASES=[
 dict(id='dev-lookup',split='dev',question='Alpha revenue?',docs=['alpha'],answerable=True,points=['125','million USD'],support='Revenue is 125 million USD.',page=1),
 dict(id='dev-table',split='dev',question='What is the 2022 table revenue?',docs=['alpha'],answerable=True,points=['125','USD m'],support='2022 | 125',page=1),
 dict(id='reg-lookup',split='regression',question='Beta revenue?',docs=['beta'],answerable=True,points=['275','million USD'],support='Revenue is 275 million USD.',page=1),
 dict(id='reg-scope',split='regression',question='Revenue under Alpha scope?',docs=['alpha'],answerable=True,points=['125','million USD'],support='Revenue is 125 million USD.',page=1),
 dict(id='reg-cross',split='regression',question='Compare Alpha and Beta revenue',docs=['alpha','beta'],answerable=True,points=['125','275'],support=['Revenue is 125 million USD.','Revenue is 275 million USD.'],page=1),
 dict(id='reg-follow',split='regression',question='And its units?',history=['Alpha revenue?'],docs=['alpha'],answerable=True,points=['million USD'],support='Revenue is 125 million USD.',page=1),
 dict(id='reg-empty',split='regression',question='Revenue with no selected documents?',docs=[],answerable=False,points=[],support=None,page=None),
 dict(id='reg-unanswerable',split='regression',question='Alpha 2040 stock price?',docs=['alpha'],answerable=False,points=[],support=None,page=None),
 dict(id='reg-permission',split='regression',question='Unauthorized Beta revenue?',docs=[],answerable=False,points=[],support=None,page=None),
]
