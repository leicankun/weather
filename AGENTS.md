# AGENTS.md

天气与出行助手。后端 Python / FastAPI / PostgreSQL；两个前端均 Vue 3 / JavaScript。

- 当前架构：docs/architecture.md；启动：docs/LOCAL_SETUP.md；测试：docs/testing.md。
- 修改前阅读相关代码，确认新文件是否已存在。不得覆盖本地 .env。
- 前端检查：根目录 `npm run check:js`、`npm --prefix frontend-user run lint`、`npm test`、`npm run build`。
- 浏览器验证：`npm run test:e2e`。契约测试使用 mock，仍须按需验证真实 API。
- 后端：`python -m ruff check .`、`python -m ruff format --check .`、`python -m pytest`（backend 目录）。
- 无 PostgreSQL 时可执行 `python -m pytest --no-db -o addopts= -q`，须明确报告跳过的集成测试。
- TypeScript 已按用户要求移除，不再执行 tsc/vue-tsc，不引入 .ts/.tsx 或 script lang=ts。
- API 返回 `{code,message,data}`；前端 api/http.js 已解包一次。
- 新表同步 models/__init__.py 和 Alembic 迁移；涉及并发数据更新需相应测试。
- 完成改动后运行相关检查、实际调用接口或页面，更新文档和验证记录。
- 使用实际配置的 Git 身份创建本地提交，不冒用上游作者身份，不推送上游。
