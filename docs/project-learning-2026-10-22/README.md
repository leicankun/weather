# 知天气项目学习指南 · 2026-10-22

本版覆盖全部十个学习模块，采用 standard 深度，包含项目面试准备。由 project-learning 技能的网页模板和现有学习材料生成，再逐模块对照当前工作区源码修订。

## 阅读

在项目根目录运行：

```powershell
python -m http.server 8420 --bind 127.0.0.1 --directory docs/project-learning-2026-10-22
```

浏览器打开 <http://127.0.0.1:8420/>。通过 HTTP 访问，页面才能读取同目录 JSON。服务仅监听本机、仅提供本指南目录。

页面支持全文搜索、模块目录、前后翻页、代码展开、架构图、面试答案展开、深色主题和阅读位置恢复。阅读记录使用本版独立的 localStorage 前缀。四项渲染依赖已放在 learning-assets 中，页面不依赖在线 CDN。

## 内容

- 4 章、109 节：前置准备、架构设计、实战演练、面试准备。
- 10 个模块，每个模块含导读与 8 节实践内容。
- 79 条调用链、373 个源码步骤，其中 371 个包含与源码匹配的连续代码摘录；另外两步使用 SQL 说明。
- 106 个业务端点的入口索引、19 张 Mermaid 图、7 道结合项目实现的面试题。

十个模块依次是：用户认证与偏好、天气数据与首页、推荐与行程、单 Agent 对话、多智能体与人工审批、RAG 与知识库、对话记忆、内容运营与任务、用户端前端、管理端前端。

## 文件

| 文件 | 用途 |
| --- | --- |
| index.html | 交互阅读器，源自已安装技能的 assets/template.html |
| project-learning.json | 完整学习材料，符合技能提供的 JSON Schema |
| source-validation.json | 结构检查结果及 118 个引用源码文件的 SHA-256 |
| browser-validation.json | 本地页面实际交互与 19 张图的验证结果 |
| VALIDATION.md | 本次检查范围、结果和限制 |
| preview.png | 真实浏览器预览截图 |
| learning-assets/ | 原有本地 highlight.js、marked、Mermaid 和代码主题资源 |

## 依据与边界

当前架构以 [architecture.md](../architecture.md)、[本地启动](../LOCAL_SETUP.md)、[测试说明](../testing.md) 和实际代码为准。源码位置相对项目根目录，行号对应本版核对时的工作区；后续代码变化时可用文件哈希判断是否需要刷新材料。

本次保留根目录已有 index.html、project-learning.json、learning-assets，以及 agent.py、embedding_client.py 的既有本地修改。学习内容包括这些工作区代码的现状，未修改业务逻辑或本地 .env。文档区分设计意图、实际实现和已知边界；不将本次阅读检查当作真实模型或数据库验收。
