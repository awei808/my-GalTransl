"""校对页单句 AI 建议：上下文装配、提示词构建与 LLM 调用。

与 HTTP 解耦的纯逻辑模块：server 端点只做参数校验、单飞与缓存读取；
build_suggest_messages / request_suggestion / extract_suggestion 可被
未来的 agent 或 CLI 工具复用。
"""

import re
import time
from typing import Any

from GalTransl import LOGGER

# 单次建议请求的默认超时（秒）：单句生成通常很快，留足慢模型/推理模型的余量
DEFAULT_SUGGEST_TIMEOUT = 120

# 回复开头常见的说明性前缀（剥掉后才是译文本身）
_SUGGEST_PREFIXES = ("译文：", "译文:", "建议译文：", "建议译文:", "修改后译文：", "修改后译文:")


def build_suggest_messages(
    src: str,
    current_dst: str,
    *,
    prev_dst: str = "",
    next_dst: str = "",
    problem: str = "",
    doub_content: str = "",
    instruction: str = "",
    target_lang: str = "简体中文",
) -> list[dict[str, str]]:
    """构造单轮对话消息。current_dst 为空表示该句尚未翻译。"""
    system = (
        f"你是 Galgame 译文校对专家。基于日文原文与当前译文，给出一条更符合语境、"
        f"更自然流畅的{target_lang}译文。"
        "只对确有明显改进空间的句子做实质性修改，不为改而改；"
        "原样保留原文中的系统符号、控制码（如 %p;、%f...;）、占位符与空格用法，"
        "标点转为中文标点；只输出修改后的译文本身，不要任何解释、前后缀或引号包裹。"
    )
    parts: list[str] = []
    parts.append(f"<原文>\n{src}")
    dst_display = current_dst if current_dst else "（尚未翻译）"
    parts.append(f"<当前译文>\n{dst_display}")
    if prev_dst:
        parts.append(f"<上一句译文（衔接参考）>\n{prev_dst}")
    if next_dst:
        parts.append(f"<下一句译文（衔接参考）>\n{next_dst}")
    if problem:
        parts.append(f"<已检出的问题>\n{problem}")
    if doub_content:
        parts.append(f"<存疑内容>\n{doub_content}")
    if instruction:
        parts.append(f"<用户的补充要求（优先遵循）>\n{instruction}")
    parts.append("请只输出修改后的译文本身。若当前译文已足够好，原样输出当前译文。")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def extract_suggestion(text: str) -> str:
    """从 LLM 回复中提取建议译文：剥代码块围栏、常见前缀与整体包裹引号。"""
    out = (text or "").strip()
    if not out:
        return ""
    fenced = re.search(r"```[a-zA-Z]*\s*\n(.+?)\s*```", out, re.DOTALL)
    if fenced:
        out = fenced.group(1).strip()
    changed = True
    while changed and out:
        changed = False
        for prefix in _SUGGEST_PREFIXES:
            if out.startswith(prefix):
                out = out[len(prefix):].strip()
                changed = True
        # 整体被 ASCII 双引号 / 全角弯引号包裹时剥掉（成对才剥；「」可能是 legitimate 对话引号，不动）
        for left, right in (('"', '"'), ("“", "”"), ("‘", "’")):
            if len(out) > 1 and out.startswith(left) and out.endswith(right):
                out = out[1:-1].strip()
                changed = True
    return out


def request_suggestion(
    messages: list[dict[str, str]],
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = DEFAULT_SUGGEST_TIMEOUT,
) -> str:
    """同步调用 OpenAI 兼容接口（非流式），返回原始回复文本。异常向上抛出。"""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    start = time.time()
    resp = client.chat.completions.create(model=model, messages=messages)
    content = resp.choices[0].message.content or "" if resp.choices else ""
    LOGGER.info(
        f"AI 建议请求完成: model={model} 耗时={time.time() - start:.1f}s 回复长度={len(content)}"
    )
    return content


def normalize_base_url(endpoint: str) -> str:
    """端点规范化：剥 /chat/completions 后缀，无 /vN 时补 /v1（与 name-table 口径一致）。"""
    endpoint = (endpoint or "https://api.openai.com").strip()
    if endpoint.endswith("/chat/completions"):
        endpoint = endpoint.replace("/chat/completions", "")
    if not re.search(r"/v\d+", endpoint):
        endpoint = endpoint.rstrip("/") + "/v1"
    return endpoint.strip("/")


def pick_real_token(tokens: Any) -> dict[str, Any] | None:
    """从 token 列表挑第一个真实 token（跳过 -example- 示例 key）。"""
    if not isinstance(tokens, list):
        return None
    for token_entry in tokens:
        if not isinstance(token_entry, dict):
            continue
        token = str(token_entry.get("token", "") or "")
        if not token or "-example-" in token:
            continue
        return token_entry
    return None
