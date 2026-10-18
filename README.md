# Financial RAG Agent

面向金融研报的可溯源问答项目。Vue 3 网页与 Python CLI 共用 `FinancialRAGAgent` 的 LangGraph 工作流及四个只读工具，支持查询改写、复杂问题拆分、混合检索、重排和有界恢复。网页进一步提供真实流式回答、版本化引用、PDF 原文定位及持久会话。

当前产品是**本机单一知识库**：文档目录中的全部 PDF 自动加入，不需要登录。知识库页面提供文件清单、处理状态和重命名。

## 快速启动

需要 Docker Desktop / Docker Compose。首次启动会下载本地嵌入及重排模型，CPU 可以运行。

```powershell
git clone https://github.com/Dylan7DAI/financial-rag-agent.git
cd financial-rag-agent
Copy-Item .env.example .env
New-Item -ItemType Directory -Force dataset/raw_pdf
```

编辑 `.env`，填写服务商的 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `LLM_MODEL_NAME`；将研报放入 `dataset/raw_pdf/`，然后执行：

```powershell
docker-compose -f docker/docker-compose.yml up -d --build
```

也可使用 `docker compose`。网页为 [localhost:5173](http://localhost:5173/)，接口说明为 [localhost:8000/docs](http://localhost:8000/docs)，健康检查为 `/health`。端口只绑定本机回环地址。

启动、进入知识库或刷新清单时，后端递归同步全部 PDF。处理成功后参与检索；扫描件、加密或损坏文件会显示失败，不冒充已入库。清单可分页，不截断为前 100 个文件。

## 网页能力

- 多轮消息与新建、搜索、重命名、清空、删除会话；SQLite 保存消息、状态和反馈，刷新/重启后恢复。
- 真实模型文本增量；停止生成、重新生成及旧版本查看；取消或失败回答不进入后续上下文。
- 区分检索候选和最终引用；引用绑定文档 SHA256、索引快照、片段与物理页，点击引用后可查看 PDF。有真实坐标时高亮区域，否则定位整页。
- 保存调用观察、检索/生成/首字耗时、可获得的 token 用量、复制和反馈记录。
- 桌面/窄屏布局、明暗主题、Markdown 表格与代码、安全净化及中文输入法保护。

编号与来源校验用于保证可追溯性，不等同逐句事实正确率。复杂跨页表、图像和扫描件仍有局限；Web 未配置 OCR，也没有承诺两秒首字延迟。

## 共享 Agent 与四个工具

网页每次请求注入当前文档快照，复用原有 Agent 图：

```text
会话历史 → 意图判断 → 查询改写/拆分 → report_search → 检索判断
                                      ↑               │
                                      └─ 有界恢复 ─────┘
→ 网页真实流式生成 → 引用校验 → SQLite 保存
```

短追问可规则补全公司上下文；复杂问题使用模型生成严格结构化查询。工具实例与语料按请求隔离，共享超时、重试和并发预算；恢复不会切换到另一套全库索引。

| 工具 | 用途 |
| --- | --- |
| `report_search` | 普通/结构化、多子查询的研报检索 |
| `context_expand` | 读取指定片段前后的有限上下文 |
| `report_catalog` | 按公司、年份列出当前快照的研报 |
| `metric_calculate` | 对传入数值计算和、差、比率、增长率、毛利率；使用 Decimal |

`GET /api/v2/tools` 查看工具，`POST /api/v2/tools/{name}` 传入 `{"arguments": {...}}` 调用。计算工具不自动抽取财务数值，也不替代会计口径核对。Agent 图自动使用检索工具，其余工具也可通过 API/Python 显式调用。

## Python / CLI

需要 Python 3.11+。安装依赖：

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
python main.py build
python main.py chat --question "海光信息2022年的营业收入是多少？"
python main.py chat
```

CLI 建库写入 `dataset/vector_store/`；网页使用 `runtime/knowledge/` 的不可变快照。两者共用 Agent 图及工具，保留各自适合的索引存储方式。CLI 使用的 PDF/索引路径可通过 `main.py --help`、`main.py build --help` 与 `main.py chat --help` 查看。

```python
from agent import FinancialRAGAgent

agent = FinancialRAGAgent()
print(agent.invoke_tool("metric_calculate", {
    "operation": "growth_rate", "values": [125, 100]
}))
```

## 数据与升级

`dataset/`、`runtime/`、`.env`、模型、测试截图及本地规划稿不进入 Git。Compose 对原始 `dataset/` 只读挂载，新索引、会话和库名保存在宿主机 `runtime/`；重新构建容器不会清除它们。

旧管理文档并入唯一知识库，保留文档 ID、版本和原有会话；切换前生成 SQLite 在线备份。旧账号/权限相关表若已存在则保留数据，但相关功能及接口已移除。没有可靠关联的旧引用会明确标为不可核验，不根据文件名伪造映射。

## 开发与验证

前端需要 Node.js 22.18+，在 `docker/frontend/` 执行：

```bash
npm ci
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Windows 可设置 `$env:PLAYWRIGHT_CHANNEL='msedge'` 使用已安装的 Edge。前端开发 `npm run dev`，默认代理至 `127.0.0.1:8000`；容器中代理至 `backend:8000`。

后端回归不调用外部模型：

```bash
python -m unittest discover -s agent/test -p "test_*.py" -v
python -m evals.p2_eval
```

离线评测使用明确标注的 fixture embedding 和真实 PDF parser/FAISS/BM25，只衡量对应的召回及引用协议，不代表真实答案准确率。

已启动真实服务时，在前端目录运行 `npm run test:live` 核对全部 PDF、实际检索、原文 SHA256、重命名和布局；`npm run test:integration` 验证完整 Agent/生成/引用链路，会调用配置中的模型并留下独立验收会话。

详见 [整合说明](product/Integration.md) 与 [单一知识库接口](product/Single_Knowledge_Base.md)。项目基于 [qndydc/financial-rag-agent](https://github.com/qndydc/financial-rag-agent) 延续开发，保留原许可证。
