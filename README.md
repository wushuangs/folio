# Folio · 知识有据可循

本地优先的 PDF 知识问答工作台。支持上传、异步解析、文档范围选择、对话持久化、检索片段、PDF 页码引用、流式模型回答，以及导入本项目已有的 100 页中文手册。

## 快速启动

需要 Node.js 20.9+、pnpm、Python 3.12+。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock.txt
cd web
pnpm install
cd ..
./scripts/dev.sh
```

访问 http://127.0.0.1:3000 。API 文档为 http://127.0.0.1:8000/docs 。本地数据保存在 `data/`，不提交 Git。

不配置密钥也能上传并检索真实原文；此时明确显示“原文检索模式”，不伪造生成答案或向量检索。启用生成模型后才是生成式 RAG，启用 Embedding 后才有混合检索。

## 模型配置

复制 `.env.example` 为根目录 `.env.local`。默认使用智谱 GLM。只需在 `.env.local` 填入 `ZHIPU_API_KEY` 即可同时配置回答与 Embedding，默认模型为 `glm-4.7` 和 `embedding-3`。也可以分别指定其他供应商：

```dotenv
ZHIPU_API_KEY=你的智谱密钥
LLM_BASE_URL=https://供应商接口/v1
LLM_API_KEY=你的密钥
LLM_MODEL=供应商模型名称
EMBEDDING_BASE_URL=https://向量供应商接口/v1
EMBEDDING_API_KEY=你的向量密钥
EMBEDDING_MODEL=向量模型名称
```

Embedding 是可选项，可与回答服务使用不同供应商。重启后端加载配置；配置或更换向量模型后，在知识库中点击“向量重建”。密钥不会返回前端。`BACKEND_URL` 为前端服务器环境变量；修改它时需传入 Next.js 进程环境或 `web/.env.local`。

## Docker / PostgreSQL

```bash
docker compose --env-file .env.local up --build
```

未创建 `.env.local` 时，使用 `docker compose up --build` 即可进入无模型的原文检索模式。Compose 使用 PostgreSQL + pgvector，API 不暴露宿主端口，Web 仅绑定本机。默认数据库密码仅供本地开发；可通过 `POSTGRES_PASSWORD` 改为不含 URL 保留字符的随机值。数据库与文档分别持久化在命名卷中。

## 结构与扩展

- `web/src/features/knowledge/`：问答、知识库和来源面板。
- `web/src/app/api/[...path]/`：同源服务端代理，浏览器不直连模型和后端。
- `backend/app/ingestion.py`：PDF 解析、分块、进度与重新索引。
- `backend/app/retrieval.py`：中文双字词和技术代码 BM25、可选向量召回、RRF 融合。
- `backend/app/providers.py`：Embedding 与流式回答接口。
- `backend/app/db.py`：文档、分块、会话和消息；SQLite / PostgreSQL 兼容。
- `scripts/bootstrap.py`：幂等导入 `downloads/` 的手册。

RAG 与会话、界面、业务工具解耦。后续可以增加客户身份、客服坐席、人工接管与订单工具，而不重写文档检索。

## 当前边界

- 面向本机单人使用；尚无登录、多租户与企业权限。不要直接暴露公网。
- 支持文字型 PDF，最大 50 MB / 1500 页；扫描件需先 OCR。已有边框表格的几何列与合并单元格处理；无边框复杂表格、跨页表格和图纸仍有解析限制，涉及参数与接线请核对 PDF。
- 正文块约 380 字符、重叠 50 字符；表格按行分片，长行约 440 字符。命中小块后补充相邻上下文。引用页码指 PDF 文件页序号。
- 任务状态持久化在数据库，重启后续作；目前支持单个 API worker，多实例运行需要任务租约或专用队列。
- SQLite 使用 FTS5 和数据库内精确余弦函数；PostgreSQL 使用 GIN 全文候选与 pgvector 距离查询。没有 HNSW 或外部语义 Reranker。
- 引用来源和页码取自真实检索块；编号检查不能证明每条生成结论受原文支持，仍需效果评估。
- 模型异常、取消均保存对应状态；会话保存文档范围，不把不完整内容保存成成功回答。

## 验证

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
cd web
pnpm typecheck
pnpm lint
pnpm build
```

测试覆盖实际 100 页 PDF 入库、去重、技术代码检索、页码依据、无依据问题、历史消息、原文件范围请求、错误文件与加密文档。

## 接口依据

- [智谱兼容接口](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)
- [GLM-4.7](https://docs.bigmodel.cn/cn/guide/models/text/glm-4.7)
- [Embedding-3](https://docs.bigmodel.cn/cn/guide/models/embedding/embedding-3)

当前已验证无密钥的完整原文检索链路；供应商真实调用需要用户填写有效密钥后验证。

生产构建使用 Next.js 官方 Webpack 模式，以避开本机 Turbopack 工作端口权限问题；开发仍使用默认 Turbopack。Docker 配置已提供，当前主机 Docker daemon 未运行，未实测容器启动。

## 本轮升级后的使用方式

- “文字重建”只在本地解析，激活不含向量的关键词版本。“向量重建”会将分片发送到配置的 Embedding 服务，成功后原子切换；失败时保留旧版本。
- “索引版本”可切换历史版本。文档归档后退出检索，勾选“显示已归档文档”可恢复；原文件、版本和历史消息均保留。
- 勾选“仅查原文（本地处理）”后，不向模型发送文档或问题。取消勾选后按照模型配置进行生成式问答。
- 生成回答校验本轮引用编号并补全来源清单；编号校验不能证明每条结论都受原文支持。
- “模型已配置”仅表示配置存在，不表示在线服务、额度与权限已经检测通过。
- `backend/app/parsing.py` 负责版面与有边框表格的几何列映射；`text.py` 负责中文分词和协议名称规范化。无边框、多层表头、跨页表格、扫描件和图纸仍可能解析不完整。
- `scripts/evaluate_local.py --questions 题集.json --output 结果.json` 仅评估本地检索，不调用模型。测试通过 `backend/tests/conftest.py` 强制使用临时库与空密钥。

修复明细、回归结果与尚未验证的边界见 [UPGRADE_REPORT.md](UPGRADE_REPORT.md)。

## 模型服务连接排查

打开 `http://127.0.0.1:8000/api/health/network` 可从后端进程检查向量服务网络；`?service=generation` 检查回答服务。探测不发送密钥、文档或问题，401 代表已收到接口响应，不代表密钥、余额和模型调用已经验证。

连接错误会区分 DNS、代理、证书、进程网络权限与连接超时。Embedding 仅对尚未建立连接的失败执行最多三次尝试；不因证书或权限错误关闭 TLS 校验。临时连接恢复后，历史失败任务仍保留原错误记录，需要重新点击“向量重建”。如普通终端可以联网，而后端提示 DNS 或进程权限失败，请从允许联网的本地终端运行 `./scripts/dev.sh`，并确认旧的 8000/3000 端口进程已停止。
