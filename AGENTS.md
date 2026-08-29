# Repository Guidelines

## 项目结构与架构

HireFlow 是本地优先的招聘简历筛选工作台。后端位于 `backend/app/`：`main.py` 提供 FastAPI 接口，`documents.py` 解析 PDF/DOCX，`evaluator.py` 调用模型，`runner.py` 隔离并发任务，`schemas.py` 与 `models.py` 分别是 API 数据结构和数据库实体。数据库迁移放在 `backend/alembic/versions/`，后端测试放在 `backend/tests/`。React/Vite 前端位于 `frontend/src/`，静态资源位于 `frontend/public/`。`data/` 和 `.env` 是本地运行数据，不得提交。

`DocumentProcessor`、`Evaluator`、`ScreeningRunner` 是模块边界；不要在 HTTP 路由中复制解析、评分或调度逻辑。评分协议与总分计算以 `evaluator.py` 和 `schemas.py` 为准。

## 构建、测试与开发命令

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'                 # 安装后端及测试依赖
.venv/bin/pytest -q backend/tests       # 运行后端测试
cd frontend && npm install && npm test  # 安装依赖并运行 Vitest
npm run build                           # TypeScript 检查并生成生产包
docker compose up --build               # 启动应用和 PostgreSQL
docker compose down                     # 停止服务，保留数据卷
```

本地前端使用 `npm run dev`；后端可运行 `DATABASE_URL=sqlite:///data/hireflow.db DATA_DIR=data uvicorn app.main:app --reload`。提交前还应执行 `docker compose config`。

## 编码风格与命名

代码注释、docstring、界面文案和文档使用简体中文。Python 使用 4 空格、类型标注、`snake_case` 函数/模块名和 `PascalCase` 类名；保持 `from __future__ import annotations`。React/TypeScript 使用 2 空格、组件 `PascalCase`、变量与 Hook `camelCase`。当前没有自动格式化或 lint 配置，修改时遵循相邻代码风格，避免无关格式化。

## 测试规范

后端使用 pytest/pytest-asyncio，前端使用 Vitest、Testing Library 和 jsdom。测试文件命名为 `test_*.py` 或 `*.test.tsx`。新行为应先通过公开 API 或用户可见交互添加失败测试，再实现修复；涉及模型评分时使用 fake evaluator，禁止在测试中调用真实模型。修改数据库结构必须同时新增 Alembic 迁移并覆盖升级后的行为。

## 提交与 Pull Request

当前仓库没有可用提交历史可供归纳。提交信息使用简短、祈使式中文，例如 `修复：统一维度评分口径`、`功能：支持选人导出`，一次提交只处理一个主题。PR 必须说明动机、主要改动、验证命令和数据迁移影响；界面变更附前后截图，关联 issue，并明确是否影响模型提示词、评分版本或导出格式。

## 安全与配置

简历属于不可信个人信息：不得写入日志、测试夹具或提交记录，不得向评估模型提供 shell、网络搜索或文件写入工具。密钥只放在 `.env`；提交前检查 diff，避免泄露 `OPENAI_API_KEY`。删除数据卷的 `docker compose down -v` 属于破坏性操作，除非明确要求，否则不要执行。
