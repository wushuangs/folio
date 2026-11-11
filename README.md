# Folio · 知识有据可循

本地优先的多格式文档知识问答工作台，支持混合检索、带来源定位引用的模型回答，以及人工审核知识的发布与复用。

## 核心功能

- **知识问答**：按文档范围提问、追问上下文、刷新后恢复会话与原文引用；推荐问题随文档内容变化。
- **知识库管理**：PDF、TXT、Markdown、DOCX、CSV、XLSX 及图片上传；本地 OCR、解析报告、片段修正与停用、索引版本切换、文档归档与恢复。
- **混合检索**：BM25 关键词检索与可选向量召回，通过 RRF 融合结果；未配置向量服务时可使用关键词检索。
- **检索诊断**：在知识库输入问题，查看关键词、向量、融合排序和最终依据，定位解析或召回问题；诊断不创建对话，也不调用回答模型。
- **质量与反馈**：保留原回答与来源快照，独立验证原文，人工编辑并发布标准答案。发布后自动解决反馈，无需发布时可填写原因关闭；支持相似反馈归组与知识批量撤回。
- **审核答案复用**：与标准问题一致时优先返回唯一有效的已发布答案；相近问题继续检索与生成。来源归档或索引版本变化后，相关审核知识暂停使用。
- **目录同步与应用接入**：配置本地同步目录后预览变更并确认更新；更新失败保留旧文档。可为外部应用创建固定文档范围与独立 API Key，提供问答和检索接口。

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

多模型在 `.env.local` 的 `LLM_PROFILES` 中配置 ID、名称、接口、模型及密钥环境变量名（示例见 `.env.example`），可在问答区选择，支持兼容接口的本地无密钥模型。向量模型独立配置。

修改后重启后端；新增或更换向量模型后，在知识库执行“向量重建”。重建失败会保留旧索引。模型调用会向配置的供应商发送问题或文档片段，并可能产生费用。

可在 `.env.local` 设置 `LOCAL_SYNC_DIR` 为独立文件夹的绝对路径，重启后在知识库预览并确认文件变更。片段修正会生成新索引版本，原文件和旧版本仍可回退；若保留向量索引，修改文字时会调用向量服务。

在“模型与设置 → 业务应用接入”创建应用并保存只显示一次的 API Key。使用 `Authorization: Bearer <API Key>` 调用 `POST /api/integrations/{应用 ID}/chat`（SSE）或 `/search`（JSON）；应用只能访问设置中选定的文档。目录同步更新文档时，应用范围会随新版本切换和回退。需要对外暴露服务时，仅转发 `/api/integrations/` 路径，管理页面和其他 API 仍应限制在可信网络。

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

现有反馈可导出为本地待标注题集：`.venv/bin/python scripts/export_feedback_questions.py`。人工填写预期页码或片段后，运行 `.venv/bin/python scripts/evaluate_quality.py --questions output/quality/feedback-candidates.json --mode local`。未标注题不会计入命中率；模型 token 用量尚未由当前流式接口返回，评测报告记录上下文与回答字符数，不将其当作 token。

## 使用边界

- 面向本机单用户，尚无登录、多租户与权限控制；后端使用单个 worker。
- 单份文件最大 50 MB，PDF 最多 1500 页。扫描 PDF 和图片自动执行本地 OCR：macOS 使用 Apple Vision（需 Xcode 命令行工具），其他系统需安装 Tesseract 与 `chi_sim` / `eng` 语言包；Docker 已包含。
- 解析报告标记空白、文字稀疏、乱码与 OCR 位置，支持查看提取文字；不代表解析准确率。Word 按逻辑节、表格按工作表分段定位，不伪造排版页码；Excel 不计算公式。复杂表格、图纸与嵌入图片仍可能解析不完整。
- PDF 引用页码为文件页序号，其他格式使用节、行段等逻辑位置。检索命中与引用编号有效不代表答案正确，关键结论需核对原文。
- 生成后检查引用与来源覆盖；检查通过后展示回答，不能代替人工事实审核。
- 反馈归组基于本地词语相似度，仅提示共性线索，不自动合并。批量撤回校验版本并保留历史。
- 审核发布会更新后续检索，保留发布历史，不训练模型，也不改写历史聊天。

## 许可证

代码采用 [MIT 许可证](LICENSE)。第三方产品手册及依赖适用各自授权，详见 [NOTICE](NOTICE)。
