# HireFlow

HireFlow 是一个本地优先的招聘简历筛选工作台。招聘人员为岗位填写文本 JD 和筛选提示词，批量上传 PDF/DOCX 简历，系统用同一份标准生成带原文证据的评估卡，最终推进或淘汰状态始终由人决定。

## 一键运行

```bash
cp .env.example .env
# 编辑 .env，至少填写 OPENAI_API_KEY，并确认 TEXT_MODEL 与 VISION_MODEL
docker compose up --build
```

打开 <http://127.0.0.1:8000>。第一次构建会安装中英文 Tesseract OCR，因此需要几分钟。
如果 8000 已被占用，在 `.env` 中把 `APP_PORT` 改为其他端口，例如 `8010`。

停止服务：

```bash
docker compose down
```

岗位、运行历史和简历文件会保留。只有执行 `docker compose down -v` 才会同时删除 PostgreSQL 数据卷；`data/` 下的简历文件仍需手动删除。

## 本地开发

后端需要 Python 3.11+ 和本机 PostgreSQL；测试使用 SQLite，不需要启动数据库：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q backend/tests
```

开发模式分别启动后端和前端：

```bash
source .venv/bin/activate
DATABASE_URL=sqlite:///data/hireflow.db DATA_DIR=data uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
npm run dev
```

Vite 会把 `/api` 代理到 `127.0.0.1:8000`。

## 运行规则

- 仅接受 PDF 与 DOCX；默认单文件 20 MB、PDF 50 页、每个岗位 200 份。
- PDF 每一页优先发送给 `VISION_MODEL`，失败后使用本地 `chi_sim+eng` OCR。
- DOCX 在本地提取文本，并转换为清洗后的 HTML 预览。
- 默认同时处理 5 份简历。单份失败不会阻塞整个批次，可在「任务中心」取消任务或重试失败项。
- 每次筛选都会冻结 JD、提示词和模型名；重新评分成功后替换该简历的当前结果，失败时保留旧结果。
- 岗位、简历档案、筛选任务和单次结果删除后进入全局回收站，保留 30 天后自动物理清理。
- 简历解析文本可以人工修正并留痕，也可以确认后从原文件重新解析。
- 筛选结果支持跨页选择、批量审核、批量重评、批量删除和 XLSX/CSV/JSON/Markdown/DOCX 导出。
- 配置的模型接口会接收候选人的个人信息，请确认供应商和使用方式符合你的组织要求。

## 验证

```bash
.venv/bin/pytest -q backend/tests
cd frontend && npm test && npm run build
docker compose config
```
