"""后端 HTTP 安全策略：CORS 源白名单、可选写端点鉴权、写路径安全约束。

SEC-1 加固背景：本地回环服务原本无自身鉴权且 CORS 为 `*`，若新增写端点
（项目初始化 / 导入）接收客户端原始磁盘路径，同机浏览器可能在应用运行期间
越权读写。本模块把安全约束集中为可单测的纯函数，供 server.py 复用。
"""
from __future__ import annotations

import hmac
import os

# 桌面端 webview 与本地开发/后端自带页面的合法源
_DEFAULT_ALLOWED_ORIGINS = (
    "tauri://localhost",
    "http://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "http://localhost:12333",
    "http://127.0.0.1:12333",
)


def load_allowed_origins() -> frozenset[str]:
    """返回被允许跨域读取后端的源集合。

    默认含桌面端 webview 源与本地开发/后端源；可用环境变量
    GALTRANSL_ALLOWED_ORIGINS（逗号分隔）追加 Web 模式前端源。
    """
    origins = set(_DEFAULT_ALLOWED_ORIGINS)
    extra = (os.environ.get("GALTRANSL_ALLOWED_ORIGINS") or "").strip()
    if extra:
        # 浏览器 Origin 不含结尾斜杠，统一去掉以免白名单匹配失效
        origins.update(o.strip().rstrip("/") for o in extra.split(",") if o.strip())
    return frozenset(origins)


def origin_allowed(origin: str | None, allowed: frozenset[str]) -> bool:
    """请求源是否在白名单内。"""
    if not origin:
        return False
    return origin in allowed


def load_api_token() -> str:
    """返回写端点鉴权令牌；为空表示不启用写鉴权（向后兼容）。"""
    return (os.environ.get("GALTRANSL_API_TOKEN") or "").strip()


def token_ok(auth_header: str | None, expected: str) -> bool:
    """校验 Authorization 头；未配置令牌时一律放行。

    使用 hmac.compare_digest 做时序安全的比较，避免明文 == 的旁道风险。
    仅接受 `Bearer <token>` 形式（方案名大小写不敏感）。
    """
    if not expected:
        return True
    if not auth_header:
        return False
    try:
        scheme, token = auth_header.split(" ", 1)
    except ValueError:
        return False
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(token, expected)


def safe_under_project(project_dir: str, rel_path: str) -> str:
    """将相对路径解析到项目目录内，越界则抛 ValueError。

    供未来写端点（init/import）复用，落实「不接收客户端原始磁盘路径」契约：
    所有写目标必须是 project_dir 之下的相对路径，杜绝路径穿越。使用
    os.path.commonpath 做归属判断，可稳健处理项目恰为文件系统根、以及
    Windows 跨盘符等边界情况。
    """
    if not rel_path:
        raise ValueError("相对路径不能为空")
    project_dir = os.path.normpath(project_dir)
    rel_path = rel_path.replace("\\", "/")
    if rel_path.startswith("/") or rel_path.startswith("//"):
        raise ValueError(f"拒绝绝对路径：{rel_path}")
    target = os.path.normpath(os.path.join(project_dir, rel_path))
    try:
        common = os.path.commonpath([project_dir, target])
    except ValueError:
        raise ValueError(f"非法路径，超出项目目录：{rel_path}")
    if common != project_dir:
        raise ValueError(f"非法路径，超出项目目录：{rel_path}")
    return target
