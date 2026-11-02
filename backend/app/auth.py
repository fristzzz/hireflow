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
