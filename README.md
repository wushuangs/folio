# Folio — Answers grounded in your documents

Folio is a local-first, open-source RAG workspace for asking questions across your documents. It combines keyword and optional vector retrieval, answers with traceable sources, and a review workflow that turns verified answers into reusable knowledge.

[English](#english) · [简体中文](#简体中文)

## English

### What it does

- **Ask with context:** Search selected documents, ask follow-up questions, reopen conversations, and inspect source citations. Suggested questions change with the selected documents.
- **Manage knowledge:** Upload PDF, TXT, Markdown, DOCX, CSV, XLSX, PNG, JPEG, and WebP files. Inspect OCR and parsing reports, correct or disable indexed passages, and roll back index revisions.
- **Retrieve and diagnose:** Combine BM25 keyword search with optional vector search using reciprocal rank fusion. Inspect retrieval rankings without starting a chat or calling the answer model.
- **Review and publish:** Check answers against their original sources, publish verified question–answer–source records, and reuse an exact matching published answer while its sources remain valid.
- **Connect other applications:** Preview changes in a local document folder, update indexes without replacing a working index prematurely, and create API-key-protected chat and search endpoints with per-application document scopes.

### Run locally

Requires Node.js 20.9+, pnpm, and Python 3.12+.

```bash
git clone https://github.com/wushuangs/folio.git
cd folio
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock.txt
pnpm --dir web install
cp .env.example .env.local
./scripts/dev.sh
```

Open the [workspace](http://127.0.0.1:3000) or the [API docs](http://127.0.0.1:8000/docs). Local data is stored in `data/` and is ignored by Git. Keyword search works without a model key.

### Models, sync, and API access

Set model credentials in `.env.local`; see [.env.example](.env.example). `ZHIPU_API_KEY` enables the default `glm-4.7` answer model and `embedding-3` embedding model. Alternatively, configure `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` and the corresponding `EMBEDDING_*` variables for compatible services. `LLM_PROFILES` adds selectable answer models. Restart the backend after changing configuration; rebuild vectors after changing the embedding model. Model calls may send questions or document passages to the configured provider and may incur charges.

Set `LOCAL_SYNC_DIR` to a separate document folder to preview and confirm file changes in the knowledge base. Corrections create a new index revision; the original file and earlier revisions remain available. Folder updates keep the old document searchable until the new index is ready, and application document scopes follow successful updates and rollbacks.

In the UI, open **模型与设置 → 业务应用接入** (Models & Settings → Application Access), create an application, and save its API key when it is displayed. Call `POST /api/integrations/{app_id}/chat` for an SSE response or `POST /api/integrations/{app_id}/search` for JSON with `Authorization: Bearer <API_KEY>`. If exposing an endpoint externally, forward only `/api/integrations/` and keep the management UI and other API routes on a trusted network.

### Docker and development

```bash
docker compose --env-file .env.local up --build
```

Docker uses PostgreSQL with pgvector and persistent volumes; a direct local start uses SQLite. Without `.env.local`, use `docker compose up --build` for keyword search.

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
cd web
pnpm typecheck
pnpm lint
pnpm build
```

Retrieval and answer evaluation instructions are in [evaluation/README.md](evaluation/README.md). The current streaming model API does not report token usage, so evaluation reports do not treat character counts as tokens.

**Current scope:** Folio is designed for one local user and does not provide login, multi-tenant access control, or a production authorization layer for management routes. Files are limited to 50 MB and PDFs to 1,500 pages. OCR and parsing reports help find issues but do not guarantee extraction accuracy; citations and automated answer checks do not replace human verification.

The code is licensed under [MIT](LICENSE). Third-party manuals and dependencies retain their own terms; see [NOTICE](NOTICE).

## 简体中文

### 主要功能

- **知识问答**：按选定文档提问、连续追问、恢复会话并查看原文引用；推荐问题随文档变化。
- **知识管理**：上传 PDF、TXT、Markdown、DOCX、CSV、XLSX、PNG、JPEG、WebP；查看 OCR 和解析报告，修正或停用片段，并回退索引版本。
- **检索诊断**：结合 BM25 关键词检索与可选的向量检索，通过 RRF 融合结果；可在不创建对话、不调用回答模型的情况下查看检索排序。
- **审核发布**：核对原文后发布“问题＋标准答案＋来源”，在来源仍有效时复用精确匹配的已发布答案。
- **业务接入**：预览本地目录变更，待新索引就绪后切换文档；为不同应用设置文档范围和独立 API Key，提供问答与检索接口。

### 本地启动

需要 Node.js 20.9+、pnpm、Python 3.12+。

```bash
git clone https://github.com/wushuangs/folio.git
cd folio
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock.txt
pnpm --dir web install
cp .env.example .env.local
./scripts/dev.sh
```

打开[工作台](http://127.0.0.1:3000)或 [API 文档](http://127.0.0.1:8000/docs)。本地数据保存在 `data/`，不会提交到 Git。无需模型密钥即可使用关键词检索。

### 模型、同步与接口

在 `.env.local` 中配置模型，完整选项见 [.env.example](.env.example)。填写 `ZHIPU_API_KEY` 可启用默认的 `glm-4.7` 回答模型和 `embedding-3` 向量模型；也可分别使用 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 及对应的 `EMBEDDING_*` 变量接入兼容服务。`LLM_PROFILES` 可增加可切换的回答模型。修改配置后重启后端；更换向量模型后重建向量。模型调用可能向供应商发送问题或文档片段，并产生费用。

将 `LOCAL_SYNC_DIR` 设为独立的文档目录后，可在知识库预览并确认文件变更。片段修正会创建新的索引版本，原文件和旧版本仍可回退。目录更新会在新索引就绪前保留旧文档；业务应用的文档范围随成功更新和回退切换。

在“模型与设置 → 业务应用接入”创建应用并保存仅显示一次的 API Key。携带 `Authorization: Bearer <API_KEY>` 调用 `POST /api/integrations/{app_id}/chat` 获取 SSE，或调用 `POST /api/integrations/{app_id}/search` 获取 JSON。对外开放时仅转发 `/api/integrations/`，管理页面及其他 API 路由应留在可信网络。

### Docker 与开发验证

```bash
docker compose --env-file .env.local up --build
```

Docker 使用 PostgreSQL + pgvector 和持久化卷；直接本地启动默认使用 SQLite。没有 `.env.local` 时可运行 `docker compose up --build`，使用关键词检索。

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
cd web
pnpm typecheck
pnpm lint
pnpm build
```

检索与回答评测见 [evaluation/README.md](evaluation/README.md)。当前流式模型接口尚不返回 token 用量，评测不会把字符数当作 token 数。

**当前适用范围**：Folio 面向本机单用户，管理路由尚无登录、多租户权限或生产级鉴权。单文件最大 50 MB，PDF 最多 1,500 页。OCR 和解析报告有助于发现问题，但不保证提取准确；引用和自动回答检查不能替代人工核对。

代码采用 [MIT 许可证](LICENSE)。第三方手册及依赖适用各自条款，见 [NOTICE](NOTICE)。
