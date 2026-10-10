# 测试

前端：`npm --prefix frontend-user run lint`、`npm test`、`npm run build`。根目录 `npm run check:js` 检查两端没有 TS 源文件。

浏览器：`npm run test:e2e`。首次安装 Chromium：在 frontend-user 执行 `npx playwright install chromium`。可在 PowerShell 设置 `$env:PLAYWRIGHT_CHANNEL = 'chrome'` 使用现有 Chrome。

当前浏览器回归覆盖天气字段与移动布局、登录跳转、SSE 对话、草案生成后显式保存、偏好保存回读、错误重试、路线穿搭和各页面启动。API 在测试中按契约拦截，不代替真实数据库/模型验证。截图与 trace 放在 test-results，避免加入 Git。

后端：backend 目录执行 `python -m pytest`；完整套件依赖 PostgreSQL（vector / PostGIS）。测试库独立命名为 weather_db_test，会重建表。

`tests/api/test_migrations.py` 额外创建名称为 `weather_migration_<随机值>_test` 的临时数据库，用 Alembic 从空库迁移到最新版本，验证所有模型表存在及个人知识库接口的增删查、向量检索和用户隔离。另一个用例验证从旧版本升级时保留 ORM 已建表中的数据。测试结束删除临时库；测试账号需有创建数据库权限，迁移还需要 TimescaleDB 扩展。该回归不依赖 `create_all` 初始化，能发现迁移漏建表的问题。

无数据库的本地验证使用 `python -m pytest --no-db -o addopts= -q`。该模式显式跳过依赖 db/client 等集成夹具的用例，并禁止初始化/清理数据库。默认模式继续运行完整套件，CI 仍使用真实 PostgreSQL。

每次验证的实际通过数、跳过数、环境限制记录在 VALIDATION.md。不能把 no-db 或前端 mock 测试表述成全栈联调成功。
