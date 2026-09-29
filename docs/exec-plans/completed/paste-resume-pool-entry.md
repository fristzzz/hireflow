# 粘贴文本录入简历 + 入池身份提取

状态：已完成（2026-09-15）
创建：2026-09-15

## 已确认决策（grilling 结论）

1. 粘贴文本创建的简历进**简历池**（`job_id=NULL`），与邮箱捕获一致
2. 粘贴表单：必填文本域 + 可选姓名；姓名留空由后续提取自动补
3. 粘贴文本落盘为 `.txt` 文件（`data/uploads/pool/{id}.txt`，`mime_type=text/plain`），`parse_status` 直接 `ready`，预览/下载原件链路零改动
4. 查重按**身份信息**（姓名/邮箱/手机号）：发现重复 → 提醒并展示疑似重复项，用户确认后才录入；无重复直接录入；漏网重复事后人工处理
5. **简历入池即做结构化身份提取**：异步调现有文本模型输出 `{name, email, phone}` JSON，邮箱/手机号用正则兜底校验；范围仅限入池简历（邮箱 + 粘贴），直传岗位的不提取
6. 存储：`resumes` 表加 `identity_name` / `identity_email` / `identity_phone` / `identity_status`（`none`/`pending`/`ready`/`failed`）四列 + Alembic 迁移；存量池简历回填为 `pending`，由后台循环统一处理

## 后端改动

- `backend/alembic/versions/20260915_0009_resume_identity.py`：加四列；存量 `job_id IS NULL AND deleted_at IS NULL` 的简历置 `identity_status='pending'`，其余 `'none'`
- `backend/app/models.py`：`Resume` 加四列
- `backend/app/identity.py`（新模块）：`IdentityExtractor`，模型提取 + 正则兜底（模型输出与正则冲突时以正则为准）；遵循模块边界，不写进路由
- `backend/app/main.py`：
  - lifespan 增加 `process_pending_identities` 轮询循环（参照 mail_ingest 挂法），处理 `identity_status='pending'`；提取成功后按 `should_update_display_name` 逻辑补 `display_name`
  - `POST /api/pool/resumes/paste`：入参 `{text, name?, confirm?}`；文本规范化（统一换行、去首尾空白、压缩连续空行）后算 sha256、落盘；未 `confirm` 时用正则提取 email/phone + 用户给的 name，在池内未删除简历中匹配（identity 列 + `parsed_text` LIKE 兜底），命中则返回疑似重复列表且不创建；`confirm=true` 或无重复时创建，`source='paste'`，`identity_status='pending'`
  - 邮箱捕获入池处同步置 `identity_status='pending'`
- `backend/app/schemas.py`：粘贴入参/出参 schema；`ResumeOut`/`PoolResumeOut` 的 `source` 联合类型加 `"paste"`

## 前端改动

- `frontend/src/types.ts`：`source` 联合类型加 `"paste"`
- 简历池页加「粘贴文本」按钮 + 弹窗（文本域 + 可选姓名）；命中重复时展示疑似重复列表，提供「仍然录入 / 取消」
- 池列表来源标签：`paste` → `粘贴文本`

## 测试

- `backend/tests/test_paste_api.py`：粘贴创建、空文本拒绝、重复提醒（不创建）、confirm 后创建、身份回填状态
- `backend/tests/test_identity.py`：fake 提取器覆盖模型输出解析、正则兜底、失败置 `failed`
- 前端如改动交互复杂则补 `*.test.tsx`；模型调用一律 fake

## 验证

```bash
.venv/bin/pytest -q backend/tests
cd frontend && npm test && npm run build
docker compose config
```
