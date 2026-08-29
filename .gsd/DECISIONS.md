# 架构决策

## 2026-08-10：简历文本资源与分析导出

- 简历原件上传后异步解析，文本继续存入 `resumes.parsed_text`，不新增重复存储表。
- `GET /api/resumes/{id}/text` 返回入库纯文本；`download=true` 仅改变下载呈现，不改变资源语义。
- 筛选结果沿用 `GET /api/runs/{id}/export`，以可选 `format=xlsx|csv|json` 做增量扩展。
- 文本和导出响应均使用 `Cache-Control: private, no-store`，因为内容包含候选人个人信息。
- 当前 API 仅供受信本地环境的同仓库前端调用，暂不引入 URL 版本；后续变更保持字段与格式的增量兼容。

## 2026-08-10：候选人选择与文档导出

- 继续使用 `GET /api/runs/{id}/export`，新增可重复查询参数 `evaluation_id`；不传时导出整个批次，传入时严格按参数顺序仅导出所选结果。
- 任一 `evaluation_id` 不属于当前批次时返回 422，防止静默遗漏或跨批次混入候选人。
- `format` 增量支持 `md` 与 `docx`；两种文档均包含完整分析结构、维度分数、证据及人工判断。
- 所有新增导出格式继续使用 `Cache-Control: private, no-store`，现有 XLSX、CSV、JSON 调用保持兼容。
- PDF 预览不依赖浏览器插件：`preview` 返回受 CSP 约束的逐页 HTML，`preview/pages/{page_number}` 按需渲染 PNG；原件下载接口保持不变。

## 2026-08-10：岗位级筛选结果聚合

- 新增 `GET /api/jobs/{id}/evaluations`，将岗位全部历史筛选记录聚合为分页资源；使用 `page` / `page_size` 是因为招聘人员需要明确跳页，`page_size` 上限为 100。
- 搜索、人工状态、匹配等级和排序均在服务端分页前执行；默认按筛选日期倒序，响应增加 `run_id` 与 `screened_at`。
- 同一简历多次筛选保留多条记录，以筛选日期区分，避免覆盖历史评分版本。
- 新增岗位级 `GET /api/jobs/{id}/export`，支持跨批次选择评估记录；原有运行级查询与导出接口继续保留兼容。
