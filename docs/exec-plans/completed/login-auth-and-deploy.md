# 登录认证与局域网部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 HireFlow 增加单管理员账号密码登录（HttpOnly 签名 Cookie 会话），保护全部业务接口，并部署到 `kenny@192.168.1.39:27183` 供局域网访问。

**Architecture:** 后端新增 `auth.py` 模块（`itsdangerous` 签名 Cookie + `require_auth` 依赖），业务路由收敛到 `APIRouter(dependencies=[Depends(require_auth)])`，`/api/health`、`/api/auth/*`、静态资源豁免；前端新增登录页，`api.ts` 统一处理 401。部署用 rsync + docker compose，宿主机端口 27183。

**Tech Stack:** FastAPI 0.141 + itsdangerous 2.x（新增依赖）、React 19 + Vitest、docker compose、SSH/rsync。

**Spec:** [docs/superpowers/specs/2026-11-02-login-auth-design.md](../superpowers/specs/2026-11-02-login-auth-design.md)

## Global Constraints

- 文案、注释、文档一律简体中文；Python 4 空格 + 类型标注 + `from __future__ import annotations`；TypeScript 2 空格、组件 `PascalCase`。
- 新行为先写失败测试再实现；测试禁止调用真实模型（沿用 fake evaluator 惯例）。
- 密钥只进 `.env`，不提交；不把密码、Cookie、简历内容写入日志或提交记录。
- 端口：宿主机默认 `27183`（`APP_PORT` 可覆盖），容器内部固定 `8000`。
- 认证：单管理员（`ADMIN_USERNAME`/`ADMIN_PASSWORD`），会话为无状态签名 Cookie（HttpOnly、SameSite=Lax、不设 Secure，局域网 HTTP）；`SESSION_SECRET` 或 `ADMIN_PASSWORD` 为空时 `create_app` 抛 `RuntimeError` 拒绝启动。
- 豁免清单：`/api/health`、`/api/auth/*`、静态前端资源；`/docs`、`/openapi.json` 保持开放（已在 spec 中接受）。
- 无数据库结构变更，不新增 Alembic 迁移。
- 验证命令：`.venv/bin/pytest -q backend/tests`、`cd frontend && npm test`、`npm run build`、`docker compose config`。
- macOS 环境：`sed -i ''`；每个任务独立提交，提交信息用简短祈使式中文。

---

### Task 1: 后端认证模块与登录接口

**Files:**
- Modify: `pyproject.toml`（dependencies 加 `itsdangerous`）
- Modify: `backend/app/config.py`（新增 4 个配置字段）
- Modify: `backend/app/schemas.py`（新增 2 个 schema）
- Create: `backend/app/auth.py`
- Modify: `backend/app/main.py`（导入 auth，注册 `auth_router`）
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_auth_api.py`

**Interfaces:**
- Consumes: `Settings`（`backend/app/config.py`）、`create_app()`（`backend/app/main.py`）、`app.state.settings`（已有，`main.py:251`）。
- Produces:
  - `Settings.admin_username: str`、`Settings.admin_password: str`、`Settings.session_secret: str`、`Settings.session_max_age: int`
  - `auth.SESSION_COOKIE = "hireflow_session"`
  - `auth.create_session_token(settings: Settings, username: str) -> str`
  - `auth.read_session_username(settings: Settings, token: str) -> str | None`
  - `auth.require_auth(request: Request) -> str`（FastAPI 依赖，无效抛 401）
  - `auth.auth_router`（prefix `/api/auth`）：`POST /login` → 200 `{username}` + Set-Cookie；`POST /logout` → 200 `{ok: true}`；`GET /me` → 200 `{username}` 或 401
  - `schemas.AuthLoginRequest {username: str, password: str}`、`schemas.AuthSessionOut {username: str}`
  - 测试环境默认凭据（`backend/tests/conftest.py`）：`test-admin` / `test-password` / `test-session-secret`

- [ ] **Step 1: pyproject 增加 itsdangerous 依赖并安装**

在 `pyproject.toml` 的 `dependencies` 列表中，`"python-multipart>=0.0.12,<1",` 之后插入：

```python
  "itsdangerous>=2.1,<3",
```

Run: `.venv/bin/pip install -e '.[dev]'`
Expected: 安装成功，无报错。

- [ ] **Step 2: 写失败测试**

创建 `backend/tests/conftest.py`（pytest 在加载任何测试模块前先加载它，使模块级 `app = create_app()` 与测试内 `Settings()` 拿到一致的测试凭据；环境变量优先于 `.env`）：

```python
"""固定测试用管理员凭据与会话密钥（环境变量优先于 .env）。"""

from __future__ import annotations

import os

os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
```

创建 `backend/tests/test_auth_api.py`：

```python
"""验证管理员登录、会话 Cookie 校验与启动配置检查。"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import SESSION_COOKIE
from app.config import Settings
from app.main import create_app


def make_app(tmp_path: Path, **overrides: object) -> FastAPI:
    return create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        settings=Settings(**overrides),
    )


def test_missing_session_secret_fails_startup(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_app(
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            settings=Settings(session_secret=""),
        )


def test_missing_admin_password_fails_startup(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        create_app(
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            settings=Settings(admin_password=""),
        )


def test_login_success_sets_httponly_cookie(tmp_path: Path) -> None:
    settings = Settings()
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": settings.admin_username, "password": settings.admin_password},
        )
        assert response.status_code == 200
        assert response.json() == {"username": settings.admin_username}
        cookie_header = response.headers["set-cookie"]
        assert SESSION_COOKIE in cookie_header
        assert "httponly" in cookie_header.lower()
        assert "samesite=lax" in cookie_header.lower()
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json() == {"username": settings.admin_username}


def test_login_rejects_wrong_credentials_with_generic_detail(tmp_path: Path) -> None:
    settings = Settings()
    app = make_app(tmp_path)
    with TestClient(app) as client:
        wrong_user = client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": settings.admin_password},
        )
        wrong_pass = client.post(
            "/api/auth/login",
            json={"username": settings.admin_username, "password": "wrong-password"},
        )
        assert wrong_user.status_code == 401
        assert wrong_pass.status_code == 401
        assert wrong_user.json()["detail"] == "用户名或密码错误"
        assert wrong_pass.json()["detail"] == wrong_user.json()["detail"]


def test_me_requires_login(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/auth/me").status_code == 401


def test_logout_clears_cookie_and_invalidates_session(tmp_path: Path) -> None:
    settings = Settings()
    app = make_app(tmp_path)
    with TestClient(app) as client:
        client.post(
            "/api/auth/login",
            json={"username": settings.admin_username, "password": settings.admin_password},
        )
        assert client.get("/api/auth/me").status_code == 200
        logout = client.post("/api/auth/logout")
        assert logout.status_code == 200
        assert client.cookies.get(SESSION_COOKIE) is None
        assert client.get("/api/auth/me").status_code == 401


def test_tampered_cookie_is_rejected(tmp_path: Path) -> None:
    settings = Settings()
    app = make_app(tmp_path)
    with TestClient(app) as client:
        client.post(
            "/api/auth/login",
            json={"username": settings.admin_username, "password": settings.admin_password},
        )
        token = client.cookies.get(SESSION_COOKIE)
        assert token is not None
        flipped = token[:-1] + ("A" if token[-1] != "A" else "B")
        client.cookies.set(SESSION_COOKIE, flipped)
        assert client.get("/api/auth/me").status_code == 401


def test_expired_cookie_is_rejected(tmp_path: Path) -> None:
    settings = Settings(session_max_age=60)
    app = make_app(tmp_path, session_max_age=60)
    with TestClient(app) as client:
        old_timestamp = int(time.time()) - 10_000
        with patch(
            "itsdangerous.timed.TimestampSigner.get_timestamp",
            return_value=old_timestamp,
        ):
            response = client.post(
                "/api/auth/login",
                json={"username": settings.admin_username, "password": settings.admin_password},
            )
        assert response.status_code == 200
        assert client.get("/api/auth/me").status_code == 401
```

Run: `.venv/bin/pytest -q backend/tests/test_auth_api.py`
Expected: FAIL——`ModuleNotFoundError: No module named 'app.auth'`（或登录接口 404）。

- [ ] **Step 3: config.py 增加配置字段**

在 `backend/app/config.py` 的 `Settings` 类中，`imap_poll_seconds: int = 300` 之后追加：

```python
    admin_username: str = "admin"
    admin_password: str = ""
    session_secret: str = ""
    session_max_age: int = 604800
```

- [ ] **Step 4: schemas.py 增加登录 schema**

在 `backend/app/schemas.py` 文件末尾追加（凭据字段不设 `min_length`，保证错误凭据统一走 401 而非 422）：

```python
class AuthLoginRequest(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=200)


class AuthSessionOut(BaseModel):
    username: str
```

- [ ] **Step 5: 实现 auth.py 模块**

创建 `backend/app/auth.py`：

```python
"""管理员登录、登出与会话 Cookie 校验。"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import Settings
from .schemas import AuthLoginRequest, AuthSessionOut

SESSION_COOKIE = "hireflow_session"
SESSION_SALT = "hireflow.session"

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=SESSION_SALT)


def create_session_token(settings: Settings, username: str) -> str:
    """签发带时间戳的签名会话令牌。"""
    return _serializer(settings).dumps({"username": username})


def read_session_username(settings: Settings, token: str) -> str | None:
    """校验签名与有效期，返回会话用户名；无效返回 None。"""
    try:
        data = _serializer(settings).loads(token, max_age=settings.session_max_age)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    username = data.get("username")
    return username if isinstance(username, str) else None


def current_settings(request: Request) -> Settings:
    return request.app.state.settings


def require_auth(
    request: Request,
    settings: Settings = Depends(current_settings),
) -> str:
    """路由依赖：校验会话 Cookie，无效抛 401，返回用户名。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    username = read_session_username(settings, token)
    if username is None:
        raise HTTPException(status_code=401, detail="会话无效或已过期")
    return username


@auth_router.post("/login", response_model=AuthSessionOut)
def login(
    payload: AuthLoginRequest,
    response: Response,
    settings: Settings = Depends(current_settings),
) -> AuthSessionOut:
    """校验管理员凭据并种下 HttpOnly 会话 Cookie。"""
    username_ok = secrets.compare_digest(
        payload.username.encode("utf-8"),
        settings.admin_username.encode("utf-8"),
    )
    password_ok = secrets.compare_digest(
        payload.password.encode("utf-8"),
        settings.admin_password.encode("utf-8"),
    )
    if not (username_ok and password_ok):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(settings, payload.username),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
    )
    return AuthSessionOut(username=payload.username)


@auth_router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    """清除会话 Cookie（无状态会话由客户端丢弃令牌）。"""
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@auth_router.get("/me", response_model=AuthSessionOut)
def me(username: str = Depends(require_auth)) -> AuthSessionOut:
    """返回当前登录用户名；未登录返回 401。"""
    return AuthSessionOut(username=username)
```

- [ ] **Step 6: main.py 注册登录接口并加启动校验**

`backend/app/main.py` 三处修改：

1. 在 `from .config import Settings` 之前插入一行：

```python
from .auth import auth_router, require_auth
```

2. 在 `create_app` 中，`runtime_settings = settings or Settings()` 及其后的 `database_url`/`data_dir` 覆盖逻辑之后、`engine, session_factory = create_database(...)` 之前，插入启动校验：

```python
    if not runtime_settings.session_secret:
        raise RuntimeError("SESSION_SECRET 未配置：请在 .env 中设置会话签名密钥")
    if not runtime_settings.admin_password:
        raise RuntimeError("ADMIN_PASSWORD 未配置：请在 .env 中设置管理员密码")
```

3. 在文件末尾 `frontend_dist = Path.cwd() / "frontend" / "dist"` 之前插入：

```python
    app.include_router(auth_router)
```

- [ ] **Step 7: 运行测试验证通过**

Run: `.venv/bin/pytest -q backend/tests/test_auth_api.py`
Expected: PASS（9 个用例全绿）。

Run: `.venv/bin/pytest -q backend/tests`
Expected: 全量既有测试仍通过（此阶段业务接口尚未挂保护，既有测试不受影响）。

- [ ] **Step 8: 提交**

```bash
git add pyproject.toml backend/app/config.py backend/app/schemas.py backend/app/auth.py backend/app/main.py backend/tests/conftest.py backend/tests/test_auth_api.py
git commit -m "功能：后端管理员登录与会话校验"
```

---

### Task 2: 业务接口统一挂会话保护

**Files:**
- Modify: `backend/app/main.py`（`api_router` + 42 处装饰器转换 + `include_router`）
- Create: `backend/tests/auth_helpers.py`
- Modify: 9 个既有测试文件（`test_jobs_api.py`、`test_pool_api.py`、`test_paste_api.py`、`test_job_paste_api.py`、`test_mail_ingest.py`、`test_identity.py`、`test_lifecycle_api.py`、`test_screening_api.py`、`test_functional_regressions.py`）
- Modify: `backend/tests/test_auth_api.py`（新增业务接口 401 用例）

**Interfaces:**
- Consumes: Task 1 的 `require_auth`、`/api/auth/login`、测试凭据。
- Produces:
  - 全部 `/api/*` 业务接口未携带有效 Cookie 时返回 401；`/api/health` 与 `/api/auth/*` 豁免。
  - `auth_helpers.authed_client(app: FastAPI)`——上下文管理器，yield 已登录的 `TestClient`。

- [ ] **Step 1: 写失败测试（业务接口 401 + 健康检查豁免）**

在 `backend/tests/test_auth_api.py` 末尾追加：

```python
def test_business_api_requires_login_and_health_stays_open(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.post(
            "/api/auth/login",
            json={"username": "any", "password": "any"},
        ).status_code == 401  # 登录接口本身豁免保护，走凭据校验
        assert client.get("/api/jobs").status_code == 401
        assert client.post("/api/jobs", json={}).status_code == 401
        assert client.get("/api/pool/resumes").status_code == 401
```

Run: `.venv/bin/pytest -q backend/tests/test_auth_api.py::test_business_api_requires_login_and_health_stays_open`
Expected: FAIL——`assert 200 == 401`（业务接口当前未保护）。

- [ ] **Step 2: main.py 收敛业务路由到 api_router**

`backend/app/main.py` 三处修改：

1. 扩展 fastapi 导入（第 14 行），加入 `APIRouter`：

```python
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, UploadFile, status
```

2. 在 `def get_session() -> Iterator[Session]:` 函数定义结束之后（`def sync_result_of` 之前）插入：

```python
    # 业务接口统一挂会话校验；健康检查与登录接口保持豁免。
    api_router = APIRouter(dependencies=[Depends(require_auth)])
```

3. 用 sed 把 42 个业务路由装饰器从 `@app.` 转为 `@api_router.`（排除 `/api/health`，两步均在仓库根目录执行）：

```bash
sed -i '' -E 's/@app\.(get|post|patch|delete|put)\("\/api\//@api_router.\1("\/api\//g' backend/app/main.py
sed -i '' 's/@api_router\.get("\/api\/health")/@app.get("\/api\/health")/' backend/app/main.py
```

4. 在 `app.include_router(auth_router)` 之后插入：

```python
    app.include_router(api_router)
```

验证转换结果：

```bash
grep -c '@api_router\.' backend/app/main.py   # 期望 42
grep -c '@app\.' backend/app/main.py          # 期望 1（仅 /api/health）
grep -n '@app\.' backend/app/main.py          # 期望只有 /api/health 一行
```

- [ ] **Step 3: 既有测试接入已登录客户端**

创建 `backend/tests/auth_helpers.py`：

```python
"""提供已登录的 TestClient，供既有接口测试复用。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings


@contextmanager
def authed_client(app: FastAPI) -> Iterator[TestClient]:
    """启动应用并以管理员身份登录，yield 可用的 TestClient。"""
    settings = Settings()
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": settings.admin_username, "password": settings.admin_password},
        )
        assert response.status_code == 200, response.text
        yield client
```

批量替换既有测试的客户端构造与导入（仓库根目录执行）：

```bash
sed -i '' 's/with TestClient(/with authed_client(/g' backend/tests/test_*.py
sed -i '' 's/^from fastapi.testclient import TestClient$/from auth_helpers import authed_client\nfrom fastapi.testclient import TestClient/' backend/tests/test_*.py
```

说明：`TestClient` 导入保留——多个测试文件用它做类型标注（如 `def create_job(client: TestClient)`）；`mail_helpers.py` 不匹配 `test_*.py`，其内部 `isinstance(client, TestClient)` 断言不受影响。

验证：

```bash
grep -rn 'with TestClient(' backend/tests/          # 期望无输出
grep -rL 'from auth_helpers import authed_client' backend/tests/test_jobs_api.py backend/tests/test_pool_api.py backend/tests/test_paste_api.py backend/tests/test_job_paste_api.py backend/tests/test_mail_ingest.py backend/tests/test_identity.py backend/tests/test_lifecycle_api.py backend/tests/test_screening_api.py backend/tests/test_functional_regressions.py   # 期望无输出（9 个文件都已含导入）
```

若第二个命令列出某文件，手工在该文件 `from fastapi.testclient import TestClient` 行后补 `from auth_helpers import authed_client`。

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/bin/pytest -q backend/tests/test_auth_api.py`
Expected: PASS（10 个用例）。

Run: `.venv/bin/pytest -q backend/tests`
Expected: 全量通过（65 处客户端全部经 `authed_client` 登录）。

- [ ] **Step 5: 提交**

```bash
git add backend/app/main.py backend/tests/auth_helpers.py backend/tests/test_auth_api.py backend/tests/test_*.py
git commit -m "功能：业务接口统一挂会话保护"
```

---

### Task 3: 前端登录页与 401 会话处理

**Files:**
- Modify: `frontend/src/api.ts`
- Create: `frontend/src/LoginPage.tsx`
- Create: `frontend/src/LoginPage.test.tsx`
- Modify: `frontend/src/App.tsx`（登录态门闩 + 退出登录按钮）
- Modify: `frontend/src/styles.css`（登录页样式）

**Interfaces:**
- Consumes: Task 1 的 `POST /api/auth/login`、`GET /api/auth/me`、`POST /api/auth/logout`（Cookie 同源自动携带）。
- Produces:
  - `api.login(payload: { username: string; password: string }): Promise<{ username: string }>`
  - `api.logout(): Promise<{ ok: boolean }>`
  - `api.me(): Promise<{ username: string }>`
  - `setOnUnauthorized(handler: (() => void) | null): void`（`api.ts` 导出；401 时调用）
  - `LoginPage({ onLoggedIn }: { onLoggedIn: () => void })` 默认导出
  - `App` 三态：`checking`（加载中）/ `anonymous`（登录页）/ `authenticated`（主界面）
  - `JobList` 新增 prop `onLogout: () => void`

- [ ] **Step 1: 写失败测试**

创建 `frontend/src/LoginPage.test.tsx`：

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { expect, test, vi } from "vitest";

import App from "./App";
import LoginPage from "./LoginPage";

const wrap = (ui: ReactElement) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
};

test("未登录时渲染登录页", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ detail: "未登录" }), { status: 401 })),
  );
  wrap(<App />);
  expect(await screen.findByRole("button", { name: "登录" })).toBeInTheDocument();
  expect(screen.getByLabelText("用户名")).toBeInTheDocument();
  expect(screen.getByLabelText("密码")).toBeInTheDocument();
  expect(screen.queryByText("还没有岗位案卷")).not.toBeInTheDocument();
});

test("登录成功后进入主界面", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) {
        return new Response(JSON.stringify({ detail: "未登录" }), { status: 401 });
      }
      if (url.endsWith("/api/auth/login") && init?.method === "POST") {
        return new Response(JSON.stringify({ username: "test-admin" }));
      }
      return new Response(JSON.stringify([]));
    }),
  );
  wrap(<App />);
  fireEvent.change(await screen.findByLabelText("用户名"), { target: { value: "test-admin" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "test-password" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(await screen.findByText("还没有岗位案卷")).toBeInTheDocument();
});

test("登录失败展示错误提示", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "用户名或密码错误" }), { status: 401 }),
    ),
  );
  render(<LoginPage onLoggedIn={() => undefined} />);
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("用户名或密码错误");
  expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();
});
```

Run: `cd frontend && npm test`
Expected: FAIL——`LoginPage.tsx` 不存在 / `api.login` 类型错误（编译失败即为红灯）。

- [ ] **Step 2: api.ts 增加认证接口与 401 回调**

`frontend/src/api.ts` 三处修改：

1. 在 `json` 常量之后插入：

```ts
let unauthorizedHandler: (() => void) | null = null;

/** 注册全局 401 处理器（用于切回登录页）；传 null 表示清除。 */
export function setOnUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}
```

2. 在 `request` 与 `requestText` 中，`const response = await fetch(url, init);`（`requestText` 为 `await fetch(url)`）之后、`if (!response.ok)` 之前插入同一行：

```ts
  if (response.status === 401) unauthorizedHandler?.();
```

3. 在 `export const api = {` 中 `health:` 一行之前插入：

```ts
  login: (payload: { username: string; password: string }) =>
    request<{ username: string }>("/api/auth/login", json(payload)),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  me: () => request<{ username: string }>("/api/auth/me"),
```

- [ ] **Step 3: 创建 LoginPage 组件**

创建 `frontend/src/LoginPage.tsx`：

```tsx
import { useState, type FormEvent } from "react";
import { Lock } from "lucide-react";

import { api } from "./api";

export default function LoginPage({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login({ username, password });
      onLoggedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <form className="auth-card" onSubmit={submit}>
        <div className="brand-mark"><span>HF</span></div>
        <span className="eyebrow">SECURE WORKSPACE</span>
        <h1>登录 HireFlow</h1>
        <p className="auth-hint">简历数据仅限授权人员访问。</p>
        <label>
          用户名
          <input
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
          />
        </label>
        <label>
          密码
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {error && <p className="auth-error" role="alert">{error}</p>}
        <button className="button primary" type="submit" disabled={busy}>
          {busy ? "登录中…" : "登录"}
        </button>
      </form>
    </main>
  );
}
```

- [ ] **Step 4: styles.css 追加登录页样式**

在 `frontend/src/styles.css` 中（`.loading-block, .full-loading` 规则之后）追加：

```css
.auth-shell { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
.auth-card { width: min(420px, 100%); background: var(--paper); border: 1px solid var(--line); box-shadow: var(--shadow); padding: 42px 38px; display: flex; flex-direction: column; gap: 14px; }
.auth-card h1 { font-size: 30px; margin: 0; }
.auth-hint { color: var(--muted); font-size: 13px; margin: 0 0 6px; }
.auth-card label { display: flex; flex-direction: column; gap: 8px; font-size: 12px; font-weight: 700; }
.auth-error { color: var(--rust); font-size: 13px; margin: 0; }
.auth-card .button { margin-top: 8px; }
```

- [ ] **Step 5: App.tsx 加登录态门闩与退出登录**

`frontend/src/App.tsx` 四处修改：

1. lucide-react 导入列表中，在 `LoaderCircle,` 之后插入一行 `LogOut,`（`LoginPage.tsx` 自行导入 `Lock`，App 不需要）。

2. 第 46 行 `import { api } from "./api";` 改为：

```tsx
import { api, setOnUnauthorized } from "./api";
import LoginPage from "./LoginPage";
```

3. 在 `export default function App() {` 之后、`const [jobId, ...]` 之前插入，并在函数返回前加门闩（`ToastContext.Provider` 的 JSX 保持不变，仅整体后移）；同时给 `JobList` 传入 `onLogout`：

```tsx
export default function App() {
  const [authState, setAuthState] = useState<"checking" | "anonymous" | "authenticated">("checking");
  const [jobId, setJobId] = useState<string | null>(null);
  const [poolOpen, setPoolOpen] = useState(false);
  const [trashOpen, setTrashOpen] = useState(false);
  const [notices, setNotices] = useState<{ id: number; message: string; kind: NoticeKind }[]>([]);
  useEffect(() => {
    setOnUnauthorized(() => setAuthState("anonymous"));
    api.me()
      .then(() => setAuthState("authenticated"))
      .catch(() => setAuthState("anonymous"));
    return () => setOnUnauthorized(null);
  }, []);
  const notify: Notify = (message, kind = "success") => { const id = Date.now() + Math.random(); setNotices((items) => [...items, { id, message, kind }]); window.setTimeout(() => setNotices((items) => items.filter((item) => item.id !== id)), 3500); };
  const handleLogout = async () => {
    await api.logout().catch(() => undefined);
    setAuthState("anonymous");
  };
  if (authState === "checking") return <div className="full-loading"><LoaderCircle className="spin" />正在检查登录状态</div>;
  if (authState === "anonymous") return <LoginPage onLoggedIn={() => setAuthState("authenticated")} />;
  return <ToastContext.Provider value={notify}>{poolOpen ? <PoolDesk onBack={() => setPoolOpen(false)} /> : trashOpen ? <TrashDesk onBack={() => setTrashOpen(false)} /> : jobId ? <JobWorkspace jobId={jobId} onBack={() => setJobId(null)} onTrash={() => setTrashOpen(true)} /> : <JobList onOpen={setJobId} onTrash={() => setTrashOpen(true)} onPool={() => setPoolOpen(true)} onLogout={handleLogout} />}<div className="toast-stack" aria-live="polite">{notices.map((notice) => <div className={`toast ${notice.kind}`} key={notice.id}>{notice.kind === "success" ? <Check /> : <CircleAlert />}<span>{notice.message}</span><button aria-label="关闭通知" onClick={() => setNotices((items) => items.filter((item) => item.id !== notice.id))}><X /></button></div>)}</div></ToastContext.Provider>;
}
```

注意：以上第 3 点给出的是 `App` 函数替换后的完整形态——新增的只有 `authState` 状态、`useEffect`（注册/清理 401 回调 + 调 `api.me()`）、`handleLogout`、两行门闩 return，以及 `JobList` 多传的 `onLogout={handleLogout}`；其余 JSX 与原实现逐字一致。

4. `JobList` 组件签名与顶栏按钮。第 327 行改为：

```tsx
function JobList({ onOpen, onTrash, onPool, onLogout }: { onOpen: (id: string) => void; onTrash: () => void; onPool: () => void; onLogout: () => void }) {
```

在顶栏 `.topbar-actions` 中「回收站」按钮之后、`health-pill` 之前插入：

```tsx
<button className="button quiet" type="button" aria-label="退出登录" onClick={onLogout}><LogOut size={15} />退出登录</button>
```

- [ ] **Step 6: 运行前端测试与构建验证**

Run: `cd frontend && npm test`
Expected: PASS——含新增 3 个用例；既有 `App.test.tsx` 全绿（其 fetch mock 对未匹配 URL 一律返回 200，`/api/auth/me` 视为已登录，无需改动；若个别用例因登录门闩失败，为该用例的 mock 增加 `/api/auth/me` 返回 200 分支即可）。

Run: `cd frontend && npm run build`
Expected: `tsc -b && vite build` 成功，无类型错误。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/api.ts frontend/src/LoginPage.tsx frontend/src/LoginPage.test.tsx frontend/src/App.tsx frontend/src/styles.css
git commit -m "功能：前端登录页与 401 会话处理"
```

---

### Task 4: 部署配置与文档

**Files:**
- Modify: `docker-compose.yml`（端口绑定）
- Modify: `.env.example`（端口 + 认证变量）
- Modify: `.env`（本地，不提交：APP_PORT + 认证变量）
- Modify: `README.md`、`ARCHITECTURE.md`

**Interfaces:**
- Consumes: Task 1 的启动校验（`SESSION_SECRET`/`ADMIN_PASSWORD` 必填）。
- Produces: compose 默认宿主机端口 `27183`、绑定 `0.0.0.0`；本地 `.env` 具备完整可启动的认证配置；文档与行为一致。

- [ ] **Step 1: docker-compose.yml 改端口**

```yaml
    ports:
      - "0.0.0.0:${APP_PORT:-27183}:8000"
```

（替换原 `"127.0.0.1:${APP_PORT:-8000}:8000"`；容器内仍是 8000。）

- [ ] **Step 2: .env.example 更新**

1. `APP_PORT=8000` 改为 `APP_PORT=27183`。
2. 在该行之后追加：

```
# 管理员登录（必填，留空应用拒绝启动）
ADMIN_USERNAME=admin
ADMIN_PASSWORD=
# 会话 Cookie 签名密钥（必填，留空应用拒绝启动；生成：python3 -c "import secrets; print(secrets.token_urlsafe(32))"）
SESSION_SECRET=
```

- [ ] **Step 3: 本地 .env 补齐认证配置**

仓库根目录执行（改 `APP_PORT` 并在不存在时追加凭据；不回显密码到日志之外的任何提交物）：

```bash
python3 - <<'PY'
from pathlib import Path
import secrets

path = Path(".env")
text = path.read_text()
text = text.replace("APP_PORT=8000", "APP_PORT=27183")
if "ADMIN_PASSWORD=" not in text:
    text += (
        "\nADMIN_USERNAME=admin"
        f"\nADMIN_PASSWORD={secrets.token_urlsafe(12)}"
        f"\nSESSION_SECRET={secrets.token_urlsafe(32)}\n"
    )
path.write_text(text)
print("ADMIN_PASSWORD =", [line for line in text.splitlines() if line.startswith("ADMIN_PASSWORD=")][0])
PY
```

把打印出的 `ADMIN_PASSWORD` 记下来（后续登录与部署验证要用；`.env` 不入 git）。

验证（不启动服务，仅确认配置可加载）：

Run: `.venv/bin/python -c "from app.config import Settings; s = Settings(); assert s.admin_password and s.session_secret; print('配置 OK')"`
Expected: `配置 OK`。

- [ ] **Step 4: 更新 README.md**

1. 快速上手第 2 步注释：

```
# 2. 编辑 .env 配置：填入 OPENAI_API_KEY、ADMIN_PASSWORD、SESSION_SECRET
```

2. 访问地址行（原 `http://127.0.0.1:8000`）：

```
- 启动后浏览器访问：**`http://127.0.0.1:27183`**，首次打开需用管理员账号登录。
```

3. 端口占用提示行：

```
- 默认宿主机端口为非常用端口 `27183`；若被占用，修改 `.env` 中的 `APP_PORT` 即可映射至其他端口。
```

4. 环境变量表：`APP_PORT` 行默认值 `8000` 改为 `27183`；并在其后追加三行：

```
| **`ADMIN_USERNAME`** | `admin` | 管理员登录用户名 |
| **`ADMIN_PASSWORD`** | *必填* | 管理员登录密码（留空应用拒绝启动） |
| **`SESSION_SECRET`** | *必填* | 会话 Cookie 签名密钥（留空应用拒绝启动） |
```

（README 中 `uvicorn ... --port 8000` 与 Vite 代理 `8000` 两处是本地开发链路，保持不变。）

- [ ] **Step 5: 更新 ARCHITECTURE.md**

1. Top-Level Structure 的前端行改为：

```
- `frontend/src/` — React 前端（`App.tsx`、`LoginPage.tsx`、`api.ts`、`types.ts`）
```

2. Major Components 中 `main.py` 行之后追加：

```
- `auth.py` — 管理员登录/登出/会话校验；`require_auth` 经 `api_router` 保护全部业务接口（豁免 `/api/health` 与 `/api/auth/*`）
```

3. Entry Points 的生产行改为：

```
- 生产：`docker compose up --build`（应用 + PostgreSQL，宿主机端口由 `APP_PORT` 控制，默认 27183；访问需管理员登录）
```

- [ ] **Step 6: 全量验证**

Run: `docker compose config`
Expected: 配置解析成功，ports 为 `0.0.0.0:27183:8000`（或 `APP_PORT` 覆盖值）。

Run: `.venv/bin/pytest -q backend/tests`
Expected: 全绿。

Run: `cd frontend && npm test && npm run build`
Expected: 全绿且构建成功。

- [ ] **Step 7: 提交**

```bash
git add docker-compose.yml .env.example README.md ARCHITECTURE.md
git commit -m "配置：部署默认端口 27183 并补充认证配置文档"
```

提交后确认 `.env` 未被跟踪：`git status --short` 输出中不得出现 `.env`。

---

### Task 5: 部署到 192.168.1.39 并验证

**Files:**
- 无仓库文件改动（部署完成后把本计划移入 `docs/exec-plans/completed/` 并提交）。

**Interfaces:**
- Consumes: Task 1–4 的产物（源码、`.env`、compose 端口 27183）。
- Produces: `http://192.168.1.39:27183` 可访问且强制登录；curl 链路验证记录。

- [ ] **Step 1: 检查目标机可达性与 Docker**

Run:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 kenny@192.168.1.39 'echo SSH_OK; docker --version; docker compose version; (nc -z 127.0.0.1 27183 >/dev/null 2>&1 && echo PORT_BUSY) || echo PORT_FREE'
```

Expected: `SSH_OK`、docker 与 compose 版本号、`PORT_FREE`（`nc` 在 macOS/Linux 均可用；若无 `nc` 改用 `lsof -iTCP:27183 -sTCP:LISTEN` 判断）。

**若 SSH 不可达或无 Docker：停止，向用户报告具体错误，不要自行安装 Docker 或改用其他机器。**

- [ ] **Step 2: 同步代码到目标机**

```bash
rsync -a \
  --exclude '.git' --exclude '.venv' --exclude 'data' \
  --exclude 'node_modules' --exclude 'frontend/dist' \
  --exclude '__pycache__' --exclude '*.egg-info' \
  --exclude '.pytest_cache' --exclude '*.db' --exclude '.claude' \
  /Users/kennywang/app/hireflow/ kenny@192.168.1.39:~/hireflow/
```

（`.env` 包含在同步内容里，走 SSH 加密通道；不同步 `data/`，目标机从空数据卷起步。）

- [ ] **Step 3: 远程确认 .env 端口并启动**

```bash
ssh kenny@192.168.1.39 'grep -E "^(APP_PORT|ADMIN_USERNAME)=" ~/hireflow/.env'
```

Expected: `APP_PORT=27183`、`ADMIN_USERNAME=admin`（若 `APP_PORT` 不是 27183，先在远程改掉再继续）。

```bash
ssh kenny@192.168.1.39 'cd ~/hireflow && docker compose up -d --build'
```

Expected: 构建并启动成功（首次构建含 npm/pip/OCR 依赖，可能数分钟；命令超时设 600000ms）。

- [ ] **Step 4: curl 验证完整认证链路**

在本机执行（`U`/`P` 从本地 `.env` 读取，不写死）：

```bash
BASE=http://192.168.1.39:27183
U=$(grep '^ADMIN_USERNAME=' /Users/kennywang/app/hireflow/.env | cut -d= -f2)
P=$(grep '^ADMIN_PASSWORD=' /Users/kennywang/app/hireflow/.env | cut -d= -f2)

curl -s -o /dev/null -w 'health=%{http_code}\n' "$BASE/api/health"                                   # 期望 200
curl -s -o /dev/null -w 'static=%{http_code}\n' "$BASE/"                                              # 期望 200（登录页可加载）
curl -s -o /dev/null -w 'jobs_anon=%{http_code}\n' "$BASE/api/jobs"                                   # 期望 401
curl -s -o /dev/null -w 'login_wrong=%{http_code}\n' -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' -d '{"username":"'"$U"'","password":"wrong"}'   # 期望 401
curl -s -c /tmp/hf.cookies -o /dev/null -w 'login_ok=%{http_code}\n' -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' -d '{"username":"'"$U"'","password":"'"$P"'"}'   # 期望 200
curl -s -b /tmp/hf.cookies -o /dev/null -w 'jobs_authed=%{http_code}\n' "$BASE/api/jobs"               # 期望 200
curl -s -b /tmp/hf.cookies -c /tmp/hf.cookies -o /dev/null -w 'logout=%{http_code}\n' -X POST "$BASE/api/auth/logout"   # 期望 200
curl -s -b /tmp/hf.cookies -o /dev/null -w 'jobs_after_logout=%{http_code}\n' "$BASE/api/jobs"          # 期望 401
```

Expected: 8 行输出全部符合注释中的期望值；同时人工在浏览器打开 `http://192.168.1.39:27183` 确认出现登录页。

**任一期望不符：按调试工作流读完整错误 → 定位 → 修复 → 重新验证；修复若涉及仓库文件，回到对应任务补测试与提交。**

- [ ] **Step 5: 收尾——计划归档**

```bash
mv docs/exec-plans/active/login-auth-and-deploy.md docs/exec-plans/completed/login-auth-and-deploy.md
git add docs/exec-plans/
git commit -m "文档：登录认证与部署计划归档"
```

向用户汇报：访问地址、管理员用户名、密码（来自本地 `.env`）、验证结果。
