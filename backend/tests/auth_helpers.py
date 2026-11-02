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
