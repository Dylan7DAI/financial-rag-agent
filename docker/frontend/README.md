# 研报问答前端

Vue 3 + Vite；API、会话状态、引用及 PDF 模块采用 TypeScript。Node.js 22.18+。

```bash
npm ci
npm run dev
```

默认同源 `/api/v2`，Vite 代理到 `http://127.0.0.1:8000`；Compose 使用 `API_PROXY_TARGET=http://backend:8000`。配置示例见 `.env.example`。生产静态部署也需要配置 `/api` 转发。

```bash
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Windows 已安装 Edge 时可设置 `$env:PLAYWRIGHT_CHANNEL='msedge'`。E2E 使用明确标注的 fixture 服务，不代表真实模型效果。

真实服务验证：`npm run test:live` 核对目录全部 PDF、检索、原文和页面；`npm run test:integration` 调用实际生成模型验证整合后的 Agent 流程。结果写入 Git 忽略的 `test-artifacts/`。

当前没有登录、权限或多知识库页面；唯一知识库显示全部文件和处理状态，支持重命名。会话、引用原文、记录反馈、明暗主题和桌面/窄屏交互保留。完整运行方式见根目录 README。
