# 代码约定

## 后端

- Python / FastAPI；业务逻辑放 services，HTTP 参数与响应放 routers。
- 响应用 success(data) 包装；下载文件和 SSE 保持对应格式。
- 新增模型同时注册到 models/__init__.py 并新增 Alembic 迁移。
- 使用 CurrentUser / OptionalUser 保持鉴权边界。数据库操作按 user_id 隔离。
- 修改后执行 ruff、相关测试与实际接口调用。

## 前端

- 使用 Vue 3 `<script setup>` 与 JavaScript，不添加 TS 语法或依赖 TS 构建。
- api/http.js 已将 `{code,message,data}` 解包成 data，不重复读取 res.data。
- 用 Pinia 管理登录状态，用 Vue Router 的 meta.auth 保护需要登录的页面。
- 每个异步操作须有加载、失败、重试状态；保存行为由明确按钮触发。
- Markdown 必须经过 DOMPurify；不要直接渲染模型或用户提供的 HTML。
- Element Plus 的 el-form 已渲染为 form，不再外套原生 form。
- 修改后执行 JS 检查、lint、Vitest、构建及相关 Playwright 用例。

当前启动与验证说明见 LOCAL_SETUP.md、VALIDATION.md。history 与博客中的 React/TS 约定属于上游历史版本。
