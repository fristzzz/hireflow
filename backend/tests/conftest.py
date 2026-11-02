"""固定测试用管理员凭据与会话密钥（环境变量优先于 .env）。"""

from __future__ import annotations

import os

os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
