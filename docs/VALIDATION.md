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

## 尚未验证的部分

- 本机没有可用的 Docker / PostgreSQL / Redis 服务，数据库迁移和 201 项依赖数据库的测试尚未执行。`--no-db` 显式跳过数据库相关用例，默认 pytest 行为仍要求真实测试库。
- `/health` 因 PostgreSQL 和 Redis 不可用而返回 503；不能据此宣称全系统健康。
- 未配置真实模型、高德、SMTP、Web Push 等密钥，因此真实 Agent 对话、真实 AI 行程生成、通知投递和全部数据库 CRUD 联调尚未执行。
- Docker 镜像构建、完整 Compose 启动、生产覆盖文件和 GitHub Actions 尚未实际运行；YAML 解析不等于容器运行验证。
- 可选 OCR、文档解析、语音识别依赖未安装和联调；核心依赖已安装。
- 两端构建存在大于 500KB 的产物提示，主要涉及 UI / 图表依赖；构建通过，后续可按需加载优化。

启动依赖和配置步骤见 [LOCAL_SETUP.md](LOCAL_SETUP.md)。源代码中的上游署名与原仓库历史保留，未向上游推送改动。
