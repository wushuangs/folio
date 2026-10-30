# Folio · 知识有据可循

本地优先的 PDF 知识问答工作台，支持混合检索、带页码引用的流式回答，以及人工审核知识的发布与复用。

## 核心功能

- **知识问答**：按文档范围提问、多轮对话、历史记录与原文引用；推荐问题随文档内容变化。
- **知识库管理**：PDF 上传与异步解析、表格提取、索引重建、历史版本切换、文档归档与恢复。
- **混合检索**：BM25 关键词检索与可选向量召回，通过 RRF 融合结果；未配置向量服务时可使用关键词检索。
- **质量与反馈**：保留原回答与来源快照，独立验证原文，人工编辑并发布标准答案。发布后自动解决反馈，无需发布时可填写原因关闭。
- **审核答案复用**：相同标准问题优先直接返回唯一有效的已发布答案；相近问题继续检索与生成。来源归档或索引版本变化后，相关审核知识暂停使用。

## 快速启动

需要 Node.js 20.9+、pnpm、Python 3.12+。

```bash
git clone https://github.com/wushuangs/folio.git
cd folio
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock.txt
pnpm --dir web install
cp .env.example .env.local
# 按需编辑 .env.local 中的模型配置
./scripts/dev.sh
```

打开 [工作台](http://127.0.0.1:3000)；后端 [API 文档](http://127.0.0.1:8000/docs) 默认在 8000 端口。本地数据保存在 `data/`，不会提交到 Git。

**无需密钥也可使用**：上传 PDF 后进行原文检索。配置回答模型后可生成答案，配置 Embedding 后可建立向量索引。“仅查原文”模式只在本地检索 PDF，不调用模型，也不使用审核答案。

## 模型配置

在根目录 `.env.local` 中配置，完整选项见 [.env.example](.env.example)。默认使用智谱 `glm-4.7` 和 `embedding-3`，填写 `ZHIPU_API_KEY` 即可启用两项服务。

也支持分别配置兼容 OpenAI 格式的服务：

| 用途 | 环境变量 |
| --- | --- |
| 回答模型 | `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` |
| 向量模型 | `EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL` |

修改后重启后端；新增或更换向量模型后，在知识库执行“向量重建”。重建失败会保留旧索引。模型调用会向配置的供应商发送问题或文档片段，并可能产生费用。

## Docker 启动

```bash
docker compose --env-file .env.local up --build
```

使用 PostgreSQL + pgvector，数据库与文档通过命名卷持久化，Web 绑定 `127.0.0.1:3000`。无 `.env.local` 时可直接运行 `docker compose up --build`，使用原文检索。默认数据库密码仅用于本地开发。

## 技术与目录

前端采用 Next.js、React、TypeScript；后端采用 FastAPI、SQLAlchemy、pdfplumber。直接本地启动默认使用 SQLite，Docker 使用 PostgreSQL + pgvector。

```text
web/src/features/   问答、知识库、质量反馈与共享布局
web/src/app/        页面路由与同源 API 代理
backend/app/        解析、索引、检索、模型调用与审核知识
backend/tests/      后端测试
scripts/            启动、手册导入与评测脚本
evaluation/         问答评测题集与说明
```

## 开发验证

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
cd web
pnpm typecheck
pnpm lint
pnpm build
```

检索与回答评测见 [evaluation/README.md](evaluation/README.md)。

## 使用边界

- 面向本机单用户，尚无登录、多租户与权限控制；后端使用单个 worker。
- 支持文字型 PDF（最大 50 MB / 1500 页），扫描件需先 OCR；复杂表格和图纸仍可能解析不完整。
- 引用页码为 PDF 文件页序号。检索命中与引用编号有效不代表答案正确，关键结论需核对原文。
- 审核发布会更新后续检索，保留发布历史，不训练模型，也不改写历史聊天。

## 许可证

代码采用 [MIT 许可证](LICENSE)。第三方产品手册及依赖适用各自授权，详见 [NOTICE](NOTICE)。
