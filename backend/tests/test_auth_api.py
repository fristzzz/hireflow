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
        # 篡改倒数第二位：末位 base64 只带 4 个有效位，翻转可能解码出相同签名
        flipped = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
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
