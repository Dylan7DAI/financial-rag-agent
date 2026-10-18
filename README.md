# Financial RAG Agent

Financial RAG Agent 是一个面向金融研报的可溯源问答项目。它解析 PDF 正文与表格，使用 FAISS、BM25 和 CrossEncoder 完成混合检索，再通过 LangGraph 组织查询改写、检索、结果判断、恢复和回答，输出带文件名与页码引用的答案。

| 能力 | 当前实现 |
| --- | --- |
| 研报入库 | PDF 正文与表格解析、中文分块、来源与页码保留 |
| 混合检索 | 向量召回 + BM25，候选结果由 CrossEncoder 重排 |
| 多轮问答 | 会话历史、结构化改写、子问题检索和引用生成 |
| 调用管理 | 严格参数校验、超时、临时错误重试、参数修复与空结果泛化 |
| 四个原子工具 | 研报检索、相邻上下文展开、研报清单查询、确定性指标计算 |

项目提供两种启动方式：CLI 终端问答，以及 Docker Compose 启动 FastAPI + Vue 界面。当前采用有界条件工作流；研报检索已进入问答主链，其余三个工具通过 Python 入口显式调用。网页现已支持模型真实文本增量 SSE。

## 本次新增

- 网页多轮会话持久化，支持搜索、重命名、停止生成、重新生成和反馈。
- 引用关联文档版本、片段和 PDF 物理页，可打开原文并高亮真实区域；改进年份与表格召回。
- 单一知识库自动纳入文档目录中的全部 PDF，提供文件清单、处理状态和知识库重命名，无需登录。
- 网页复用原 Agent 工作流及四个工具，补充自动化测试、GitHub CI、明暗主题和窄屏适配。

## 快速启动

所有命令都在项目根目录执行。两种启动方式都需要模型 API 配置。CLI 需要先建库；网页会自动处理目录中的全部 PDF。

### 1. 获取源码与配置

```bash
git clone https://github.com/Dylan7DAI/financial-rag-agent.git
cd financial-rag-agent
```

复制配置模板：

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

Linux / macOS：

```bash
cp .env.example .env
```

编辑 `.env`，至少填写以下三项：

```env
OPENAI_API_KEY=你的API密钥
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=你的模型名称
```

支持兼容 OpenAI 接口的模型服务。`EMBEDDING_MODEL_NAME` 和 `RERANKER_MODEL_NAME` 分别指定本地向量模型与重排模型，模板默认使用 CPU；本地 CUDA 环境可将两个 `*_DEVICE` 改为 `cuda`。首次运行会下载模型，已有模型也可填写本地目录。

### 2. 方式一：CLI

环境要求：Python 3.11+。在独立虚拟环境中安装依赖：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

Linux / macOS：

```bash
source .venv/bin/activate
```

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

将 PDF 研报放入 `dataset/raw_pdf/`，首次使用时构建知识库：

```bash
python main.py build
```

随后启动交互问答：

```bash
python main.py chat
```

也可以只问一个问题：

```bash
python main.py chat --question "海光信息2024年净利润是多少？"
```

已有知识库时可以跳过建库。默认使用 `dataset/vector_store/` 下的 `index.faiss`、`index.pkl` 和 `all_documents.json`；建库与问答必须使用同一个 Embedding 模型。

### 3. 方式二：Docker Compose

环境要求：Docker 和 Docker Compose。默认镜像使用 CPU，不需要宿主机安装 Python 或 Node.js。

先将 PDF 放入 `dataset/raw_pdf/`，然后启动前后端；网页会自动入库：

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

| 服务 | 地址 |
| --- | --- |
| 问答界面 | http://localhost:5173 |
| 后端 API 文档 | http://localhost:8000/docs |

`dataset/` 只读挂载到容器，网页索引与会话保存在宿主机 `runtime/`；下载的模型保存在 Docker 的 `model-cache` 数据卷。首次启动需要等待模型加载与文档处理完成，默认设备为 `cpu`。

查看运行日志与停止服务：

```bash
docker compose -f docker/docker-compose.yml logs -f backend
docker compose -f docker/docker-compose.yml down
```

## 使用说明

### 终端与网页问答

CLI 中直接输入问题，输入 `/clear` 清空当前会话，输入 `quit`、`exit` 或按 `Ctrl+C` 退出。会话历史保存在进程内，重启后清空。

网页中输入问题并点击“发送”，可继续追问或清空当前会话。建议问题包含公司、年份和指标，例如：

- “海光信息2024年净利润是多少？”
- “比较海光信息2023年与2024年的营业收入。”
- “那同比增速呢？”（同一会话中的追问）

回答引用来自入库研报；检索失败时，系统按恢复预算修复或泛化查询，仍无法回答则返回降级说明。

### CLI 参数

```bash
# 查看命令帮助
python main.py --help

# 指定研报目录和知识库输出目录
python main.py build --pdf-dir "/path/to/reports" --save-path "/path/to/vector_store"

# 使用已有知识库，并指定会话
python main.py chat --vector-store "/path/to/vector_store" --session-id research
```

`python main.py` 默认进入交互问答。CLI 的 `build` 会重建目标目录中的索引；新增研报后需重新建库并重启 CLI。网页可通过知识库的“刷新文件清单”发现新增 PDF。

### API

| 接口 | 用途 |
| --- | --- |
| `POST /api/v2/sessions` | 创建持久会话 |
| `POST /api/v2/chat` | 真实增量 SSE 问答 |
| `POST /api/v2/sessions/{id}/clear` | 清空指定会话 |
| `GET/PATCH /api/v2/knowledge-base` | 查看或重命名唯一知识库 |

旧版问答 HTTP 接口已由 `/api/v2` 替代；请求模型可在 http://localhost:8000/docs 中查看和试用。

### 原子工具

Python 中可以通过同一套生命周期管理器调用工具：

```python
from agent import FinancialRAGAgent

agent = FinancialRAGAgent()
print(agent.list_tools())
result = agent.invoke_tool(
    "metric_calculate",
    {"operation": "growth_rate", "values": [120, 100], "precision": 2},
)
print(result)  # result 包含计算结果与 observation；增长率为 20%
```

`context_expand` 读取指定片段的相邻块；`report_catalog` 查询知识库中的研报清单；`metric_calculate` 计算传入数值，不负责自动抽取指标或判断会计口径。也可通过 `GET /api/v2/tools` 和 `POST /api/v2/tools/{name}` 查看与调用四个工具。

## 项目结构

```text
agent/       LangGraph 编排、调用生命周期、工具、记忆与评测
rag/         PDF 解析、分块、向量建库、混合检索与重排
configs/     模型、检索与调用预算配置
dataset/     原始 PDF 与知识库（本地数据，不随源码分发）
docker/      FastAPI 后端、Vue 前端和 Compose 配置
main.py      CLI 入口
```

## 常见问题

- **CLI 提示知识库缺失**：先运行 `python main.py build`；网页无需手动建库。默认 PDF 解析适用于可提取文字的 PDF。
- **模型下载或请求失败**：检查网络、API 配置和模型路径；CPU 首次精排较慢时可增大 `TOOL_CALL_TIMEOUT_SECONDS`。
- **检索结果更新不及时**：CLI 重新建库后重启；网页刷新文件清单并查看处理状态。

调用生命周期与工具单测：

```bash
python -m unittest discover -s agent/test -p test_call_lifecycle.py -v
```

检索和 RAGAS 评测代码分别位于 `rag/test/` 与 `agent/test/`；运行 RAGAS 需另外安装 `ragas>=0.3,<1`。OCR 与模型微调依赖按对应模块另行安装。

许可证：[MIT](LICENSE)。
