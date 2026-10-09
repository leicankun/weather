"""pre-commit 的前端检查钩子（跨平台）。

为什么用 Python 脚本而不是在 .pre-commit-config.yaml 里直接写 `npm run lint`：

    Windows 上 npm 的真实可执行文件是 `npm.cmd`，pre-commit 会按字符串
    去 PATH 里找叫 `npm` 的文件，结果找不到并报错；Linux/macOS 又完全正常。
    与其为不同系统维护两套配置，不如让 Python 用 shutil.which() 去找——
    它会自动匹配 PATHEXT，Windows / macOS / Linux 行为一致。

    另外，靠 Python 判断"npm 是否装了"也更容易给出**人话**的提示，
    而不是丢一段 stack trace 给开发者。

检查内容：
    1. eslint  —— Vue / JavaScript 代码规范
    2. check-javascript —— 两个前端不引入 TypeScript 源文件

注意：这里刻意不跑 `npm run build`（会额外做打包，慢且没必要）。
打包和浏览器回归由 CI 执行。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# 项目根目录（本文件在 scripts/ 下）
ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend-user"


def _run(cmd: list[str], cwd: Path, label: str) -> bool:
    """执行命令，返回是否成功。找不到可执行文件时跳过（不算失败）。"""
    exe = shutil.which(cmd[0])
    if exe is None:
        print(f"⚠️  未找到 {cmd[0]}，跳过「{label}」。")
        print("    如需启用，请先安装 Node.js 并在 frontend-user 下执行 npm install。")
        return True

    print(f"→ {label}")
    result = subprocess.run([exe, *cmd[1:]], cwd=cwd, check=False)
    return result.returncode == 0


def main() -> int:
    if not FRONTEND_DIR.exists():
        print("⚠️  未找到 frontend-user 目录，跳过前端检查。")
        return 0

    if not (FRONTEND_DIR / "node_modules").exists():
        print("⚠️  frontend-user/node_modules 不存在，跳过前端检查。")
        print("    请先执行：cd frontend-user && npm install")
        return 0

    checks = [
        (["npm", "run", "lint"], FRONTEND_DIR, "eslint 代码检查"),
        (["node", "scripts/check-javascript.mjs"], ROOT, "JavaScript 源文件检查"),
    ]

    failed: list[str] = []
    for cmd, cwd, label in checks:
        if not _run(cmd, cwd, label):
            failed.append(label)

    if failed:
        print("\n❌ 前端检查未通过：" + "、".join(failed))
        print("   修完再提交；确实要跳过可用 git commit --no-verify")
        return 1

    print("✅ 前端检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
