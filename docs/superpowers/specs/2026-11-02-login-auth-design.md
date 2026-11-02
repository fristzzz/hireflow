# 登录认证与局域网部署设计

日期：2026-11-02
状态：已确认（方案 A）

## 背景与目标

当前项目没有任何登录认证：后端 `backend/app/main.py` 的全部 `/api/*` 路由公开，前端 `frontend/src/api.ts` 使用裸 `fetch` 不带凭据。需要将应用部署到 `kenny@192.168.1.39` 供局域网访问，因此必须增加账号密码登录，防止局域网内其他机器直接读取简历数据。

目标：

1. 前端登录页 + 后端账号密码校验，登录态用 HttpOnly Cookie 保存。
2. 单管理员账号，凭据存于 `.env`。
3. 通过 SSH 直接部署到 `192.168.1.39` 并验证登录链路。

非目标：

- 多用户体系、角色权限、注册/找回密码。
- HTTPS（局域网内走 HTTP；Cookie 不设 `Secure` 标志）。
- 反向代理层认证（nginx basic auth 等）。

## 方案选型

| 方案 | 说明 | 结论 |
| --- | --- | --- |
| A. Starlette 生态签名 Cookie 会话 | `itsdangerous` 签名 HttpOnly Cookie，`require_auth` 依赖保护路由 | **采用**：依赖最少、无状态、换 `SESSION_SECRET` 即全员下线 |
| B. JWT 存 HttpOnly Cookie | PyJWT 签发带 `exp` 的令牌 | 弃用：单管理员场景无收益，提前登出还需黑名单 |
| C. 反向代理层认证 | nginx basic auth | 弃用：无前端登录页，体验与需求不符，多一层运维 |

## 详细设计

### 后端

**配置（`backend/app/config.py`）新增字段：**

- `admin_username: str = "admin"` — 管理员用户名。
- `admin_password: str = ""` — 管理员密码（明文存 `.env`，与现有 `IMAP_PASSWORD` 惯例一致）。
- `session_secret: str = ""` — Cookie 签名密钥；为空时**启动报错**（不用不安全默认值上线，行为可预期）。
- `session_max_age: int = 604800` — 会话有效期，默认 7 天。

**新模块 `backend/app/auth.py`：**

- `POST /api/auth/login`：请求体 `{username, password}`；用 `secrets.compare_digest` 恒时比较；成功后签发 `itsdangerous` 签名 Cookie（名称如 `hireflow_session`），属性 `HttpOnly; SameSite=Lax`，携带过期时间；失败返回 401，不区分用户不存在/密码错误。
- `POST /api/auth/logout`：清除 Cookie。
- `GET /api/auth/me`：返回 `{username}` 或 401，供前端判断登录态。
- `require_auth` FastAPI 依赖：校验 Cookie 签名与过期，失败返回 401。

**路由保护（`backend/app/main.py`）：**

- 全部业务路由挂 `Depends(require_auth)`。
- 豁免：`/api/health`、`/api/auth/*`、静态前端资源（登录页本身需要可加载）。
- CORS 配置保持现状（前端经 Vite 代理同源，Cookie 自动携带）。

### 前端

**`frontend/src/api.ts`：**

- `request`/`requestText` 统一检测 401，通过模块级可注册回调（`onUnauthorized`）通知应用切换到登录页。
- Cookie 同源自动携带，不手动加 `Authorization` 头。

**登录页（新增组件）：**

- 应用启动时调用 `GET /api/auth/me` 判断登录态；未登录展示登录表单（用户名/密码），登录成功后进入主界面。
- 登录失败展示错误提示，不暴露"用户不存在"等差异信息。

### 错误处理

- 登录失败：401 + 通用文案（如「用户名或密码错误」）。
- 会话过期/被吊销：业务接口 401 → 前端回到登录页。
- `SESSION_SECRET` 未配置：启动失败并给出明确报错（避免用不安全的默认值上线）。
### 测试

后端（pytest，不调用真实模型）：

1. 未登录访问业务接口返回 401；`/api/health` 与登录接口豁免。
2. 正确凭据登录成功并种下 Cookie；错误凭据 401。
3. 携带有效 Cookie 访问业务接口成功；登出后同一 Cookie 失效。
4. 篡改 Cookie 签名返回 401；过期 Cookie 返回 401。

前端（Vitest + Testing Library）：

1. 未登录时渲染登录页。
2. 登录成功后进入主界面；失败展示错误提示。

### 部署

1. `docker-compose.yml`：app 端口改为 `0.0.0.0:${APP_PORT:-27183}:8000`——宿主机用非常用端口 **27183**（避开 8000/8080/3000 等大众端口，也避开 32768+ 系统临时端口段），容器内部仍为 8000；`APP_PORT` 环境变量可覆盖。局域网访问地址为 `http://192.168.1.39:27183`。
2. `.env` 新增 `ADMIN_USERNAME`、`ADMIN_PASSWORD`、`SESSION_SECRET`（密钥只进 `.env`，不提交）。
3. 部署流程：SSH 到 `kenny@192.168.1.39` → 同步代码 → `docker compose up -d --build` → 用 curl 验证「未登录 401 → 登录成功 → 携带 Cookie 访问成功」链路；部署时顺带确认 27183 在目标机未被占用。
4. 前置条件：目标机已装 Docker 且从本机可 SSH；当前检查时 `192.168.1.39` 不可达（No route to host），部署步骤需等其可达后执行。

## 数据迁移影响

无数据库结构变更，无需 Alembic 迁移。

## 安全约定

- 密钥与凭据只放 `.env`，提交前检查 diff。
- 登录与会话校验不记录密码或 Cookie 内容到日志。
- 简历数据仍属不可信个人信息，认证模块不引入任何模型调用。
