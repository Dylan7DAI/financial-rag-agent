# 单一知识库

网页始终采用一个本机知识库，不启用账号、授权、权限或资源归属功能。`dataset/raw_pdf/` 中全部 PDF（含子目录，忽略大小写）自动参与处理。启动、读取知识库或清单时同步；符号链接及目录外文件不纳入。

## 接口

| 接口 | 行为 |
| --- | --- |
| `GET /api/v2/knowledge-base` | 唯一库 ID、名称、文件数、已可检索数及后台处理状态 |
| `PATCH /api/v2/knowledge-base` | `{"name":"名称"}`，去首尾空白，1–80 字符，持久化到 SQLite |
| `GET /api/v2/knowledge-base/documents?offset=0&limit=20` | 全部 PDF 的分页清单、页数、文档/索引版本和实际 RAG 状态；每页最多 100 |
| `GET /api/v2/tools` | 共享 Agent 的四个只读工具 |
| `POST /api/v2/tools/{name}` | `{"arguments":{...},"scope":{...}}`；参数由统一生命周期校验，scope 可省略，默认全库 |

保留会话、问答、反馈、版本化 PDF 原文、检索预览、追踪记录和导出接口；具体请求模型以 `/docs` 为准。兼容只读 `/knowledge-bases` 返回一项、`/managed-documents` 返回已发布文件。旧 `/api/chat`、`/api/stream/chat`、账号/成员授权/多库管理接口已移除。

## 数据行为

- 未就绪文件显示处理中或失败，只有校验并发布的索引参与检索。
- 同名同 SHA256 文件复用文档与索引，不反复入库。修改原文件时建立候选快照，成功后原子发布，失败保留旧版。
- 每个回答固定该次文档版本；并发索引发布不会在回答中途替换证据。
- 目录移除的已跟踪文件退出新检索；保留旧版原文及历史索引用于历史核验。不改写原 `dataset/vector_store/`。
- 会话、库名和索引持久化到 `runtime/`。旧库合并前生成 `runtime/pre-single-workspace.sqlite3` 在线备份。
- 旧 SQLite 中的身份列和历史表保留兼容，但没有账号或权限相关运行路径；`LOCAL_AUTO_LOGIN_*`、`ENABLE_LEGACY_LOCAL_API`、`ENABLE_V2_API`、`SINGLE_KNOWLEDGE_BASE` 旧开关均不再控制服务。

当前自有测试目录有贵州茅台、海光信息、宁德时代三份 PDF。代码库不分发这些 PDF，其他使用者需放入自己的资料。
