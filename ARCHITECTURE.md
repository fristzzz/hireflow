# Architecture

## Status
现状文档，全部内容来自对仓库的实际观察。未来设想若有会明确标注为 proposal。

## Repository Shape
- **Type**: Web App（本地优先单体检工作台：FastAPI 后端 + React/Vite 前端）
- **Languages**: Python ≥3.11（后端）、TypeScript/React 19（前端）
- **Build/test**: pip + pytest（`pyproject.toml`），npm + Vitest/tsc（`frontend/package.json`），docker compose 起 PostgreSQL（`docker-compose.yml`）

## Top-Level Structure
- `backend/app/` — FastAPI 应用与全部业务逻辑
- `backend/alembic/versions/` — 数据库迁移
- `backend/tests/` — pytest 后端测试
- `frontend/src/` — React 前端（`App.tsx`、`api.ts`、`types.ts`）
- `data/` — 本地运行数据（uploads、derived、临时 SQLite），不提交
- `docs/exec-plans/` — 多步骤工作的执行计划（active/ 与 completed/）

## Entry Points
- 后端：`uvicorn app.main:app`（`backend/app/main.py`，lifespan 中挂 IMAP 轮询循环与身份提取轮询循环）
- 前端：`npm run dev` → Vite dev server（`frontend/index.html` → `src/main.tsx`）
- 生产：`docker compose up --build`（应用 + PostgreSQL，端口由 `APP_PORT` 控制）

## Major Components
- `main.py` — HTTP 路由层（健康检查、邮箱同步、简历池、岗位、简历、评估运行、评审）
- `documents.py` (`DocumentProcessor`) — PDF/DOCX 解析与预览
- `evaluator.py` (`Evaluator`) — 调用兼容 OpenAI 的模型进行维度评分；评分协议以此为准
- `runner.py` (`ScreeningRunner`) — 并发任务隔离与调度
- `mail_ingest.py` — IMAP 捕获邮箱简历附件进入「简历池」，只记录同步计数
- `identity.py` (`IdentityExtractor`) — 入池简历的结构化身份提取（模型输出 JSON，邮箱/手机号正则兜底），由 lifespan 轮询循环驱动
- `schemas.py` / `models.py` — API 数据结构 / 数据库实体
- `exporter.py`、`lifecycle.py`、`workbench.py`、`names.py`、`errors.py` — 导出、生命周期、工作台、命名、错误处理

模块边界：`DocumentProcessor`、`Evaluator`、`ScreeningRunner` 不在 HTTP 路由中复制。

## Data Flow
简历来源三条路径：手动上传（`POST /api/jobs/{id}/resumes`）、IMAP 邮箱捕获或粘贴文本（池级 `POST /api/pool/resumes/paste` 与岗位级 `POST /api/jobs/{id}/resumes/paste`；岗位级成功即启动单份简历评分任务）。粘贴文本落盘为 `.txt` 并按身份信息（姓名/邮箱/手机号）查重（岗位级另含 sha256 完全相同判重），命中疑似重复需用户确认。评估由 `POST /api/jobs/{id}/runs` 触发，`ScreeningRunner` 并发执行，`Evaluator` 产出维度评分与总分，结果经评审接口（`/api/evaluations/...`）人工确认。评估卡的 `gender`/`age` 只用于简历池档案筛选，不参与评分；简历池的年龄/性别/分数筛选在前端本地完成。入池简历由后台循环提取 `identity_name`/`identity_email`/`identity_phone`（`identity_status`：none/pending/ready/failed），直传岗位的不提取。

## Open Questions
- 无（init 阶段未发现文档与代码的明显冲突）

## Proposals
- 暂无
