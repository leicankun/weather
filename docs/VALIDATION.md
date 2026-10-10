# 重构验证记录

验证日期：2026-10-09。Windows / Python 3.13.2 / Node.js 24.16.0。

## 已通过

| 检查 | 结果 |
| --- | --- |
| `npm run check:js` | 两个前端无 TypeScript 源文件或 Vue TS 脚本 |
| `npm --prefix frontend-user run lint` | ESLint 通过 |
| `npm test` | 5 个测试文件，18 项通过 |
| `npm run build` | 用户端、管理端均构建成功；用户端 PWA 生成成功 |
| `npm run test:e2e`，`PLAYWRIGHT_CHANNEL=chrome` | 8 项浏览器契约测试通过 |
| `python -m ruff check .` | 后端检查通过 |
| `python -m ruff format --check .` | 194 个 Python 文件格式通过 |
| `python -m pytest --no-db -o addopts= -q` | 498 项通过，201 项跳过 |
| PowerShell 语法解析 | setup.ps1、dev.ps1 通过 |
| YAML 解析 | Compose 主文件和 GitHub Actions 文件通过解析 |
| `python scripts/precommit_frontend.py` | JavaScript 检查钩子通过 |

浏览器契约测试覆盖天气展示与移动导航、登录跳转、SSE 聊天、AI 行程草案与显式保存、偏好保存后刷新、接口错误重试、路线与穿搭、其余业务页面加载。API 响应由测试拦截，不代表真实 LLM 或数据库联调通过。

另行完成管理端浏览器冒烟检查：登录页显示、回车提交、普通用户令牌被清除并返回登录页，无无限重定向和浏览器运行时异常。管理端修改后重新构建成功。

## 真实接口与页面

- 已运行 FastAPI：根路径、OpenAPI 文档、区县列表成功返回；OpenAPI 包含 80 个路径，区县列表包含 11 个区。
- 已真实调用 Open-Meteo，获取广州和天河区天气；页面成功显示实时温度与未来天气。
- Chrome 桌面 1440px 和手机 390px 页面已截图检查；页面无运行时异常，手机页面无横向溢出，导航可折叠。
- 天气时间统一显示北京时间，支持 UTC 带时区与本地无时区返回格式。

## 2026-10-09 尚未验证的部分

- 本机没有可用的 Docker / PostgreSQL / Redis 服务，数据库迁移和 201 项依赖数据库的测试尚未执行。`--no-db` 显式跳过数据库相关用例，默认 pytest 行为仍要求真实测试库。
- `/health` 因 PostgreSQL 和 Redis 不可用而返回 503；不能据此宣称全系统健康。
- 未配置真实模型、高德、SMTP、Web Push 等密钥，因此真实 Agent 对话、真实 AI 行程生成、通知投递和全部数据库 CRUD 联调尚未执行。
- Docker 镜像构建、完整 Compose 启动、生产覆盖文件和 GitHub Actions 尚未实际运行；YAML 解析不等于容器运行验证。
- 可选 OCR、文档解析、语音识别依赖未安装和联调；核心依赖已安装。
- 两端构建存在大于 500KB 的产物提示，主要涉及 UI / 图表依赖；构建通过，后续可按需加载优化。

启动依赖和配置步骤见 [LOCAL_SETUP.md](LOCAL_SETUP.md)。源代码中的上游署名与原仓库历史保留，未向上游推送改动。

## 2026-10-10：修复业务表迁移遗漏

本次环境已有 Docker PostgreSQL 17，包含 vector、PostGIS、TimescaleDB 扩展。开发库原处于 `0013_user_preferences`，但缺少 `user_knowledge`、`chat_messages`、`itineraries`、`clean_tasks`，导致个人知识库列表接口报表不存在。新增 `0014_missing_business_tables` 迁移，并提高 Alembic 最低版本至 1.13.3（支持创建表时的 `if_not_exists` 参数）。

| 检查 | 结果 |
| --- | --- |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 195 个文件通过 |
| 对新增迁移单独执行 Ruff 检查及格式检查 | 通过；迁移目录不在默认检查范围内 |
| `python -m pytest tests/api/test_migrations.py -o addopts= -q` | 2 项通过：空库迁移及知识库增删查、检索、用户隔离；旧库升级保留数据 |
| `python -m pytest` | 699 项通过，2 项失败，无跳过；执行了数据库集成测试 |
| `./scripts/dev.ps1 migrate` | 开发库成功升级到 `0014_missing_business_tables`，所有 ORM 模型对应表均存在 |
| 真实后端 `GET http://127.0.0.1:8000/api/v1/my-knowledge` | 使用本地用户 1 的令牌调用，HTTP 200、`code=0`、`total=0` |

完整套件的两项失败均位于 `tests/api/test_notifications.py`：`TestVapidKey::test_返回公钥` 假定已配置 VAPID 公钥，但本地配置为空；`TestPrefs::test_默认全开` 精确比对旧的四字段响应，实际接口已包含 `risk_enabled`。本次未修改通知接口或这些测试；不能把本次完整套件表述为全部通过。前端未改动，未重复运行前端检查；外部模型调用未在本次验证范围内。

## 2026-10-10：多智能体接入多轮聊天历史

复用已有 `chat_messages` 表及会话读写服务，无数据库结构变化。普通和 SSE 入口向多智能体传递当前用户、当前会话最近 10 条消息；领域、通用与汇总节点按原角色接收历史，LLM 路由使用历史理解省略问题。流式路径复用已计算的路由结果，包括空列表，避免重新路由后转发错误的节点。未修改本地 `.env`。

| 检查 | 结果 |
| --- | --- |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 196 个文件通过 |
| 多智能体、聊天历史服务和聊天 API 相关回归 | 59 项通过，包含 13 项新增回归 |
| `python -m pytest` | 712 项通过、2 项失败、无跳过；两项失败仍为上述通知用例 |
| 真实 HTTP + PostgreSQL + 已配置 DeepSeek | 在临时后端以 `AGENT_MODE=multi` 运行，普通接口发送“你好，我叫小林，请记住我的名字。”，同一会话流式追问名字，回答正确包含“小林”，收到 `done` |
| 对话落库与清理 | 真实接口查询到 4 条消息（两轮）；验证会话已删除，临时后端已停止 |

API 回归使用真实数据库和完整多智能体图，模型替身覆盖单领域、多领域汇总及通用回答，验证最近 10 条限制与用户/会话/匿名隔离。真实模型验证覆盖通用回答的两轮记忆；没有宣称全部外部工具联调通过。前端未改动，未重复前端检查。目前没有历史摘要，独立图片路径尚未接入历史。
