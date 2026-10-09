# 2026-10-09 Vue / JavaScript 重构

基于上游提交 09a7dba20d8fd7018833984b638b8f0f111d6db8。

用户端由 React + TypeScript 重写为 Vue 3 + JavaScript，管理端 Vue SFC、API、路由及 Store 移除类型语法。保留 Python / FastAPI / PostgreSQL 与原有 Agent 工具和业务服务。

新增可中止的 SSE 读取器，正确处理 UTF-8 跨块、CRLF、多行 data 和流结束；登录过期时清理完整账号状态；Markdown 展示经过 DOMPurify 清洗。前端只缓存应用静态资源，不缓存带用户身份的 API 响应。

同步 Node 容器版本、开发代理端口、环境配置模板、PowerShell 启动脚本、数据库迁移启动顺序及当前技术文档。多模态解析从基础安装拆成可选依赖，减少初次启动下载。

验证结果详见 docs/VALIDATION.md；浏览器契约测试与真实数据库/模型联调分开记录。
