"""Rate limiter — 基于 slowapi 的请求限流。

全局限流默认 60/minute，Chat 端点默认 10/minute（LLM 调用成本高）。
所有阈值均可通过环境变量调整：
    ONTOAGENT_RATE_LIMIT=60/minute        （全局限流）
    ONTOAGENT_CHAT_RATE_LIMIT=10/minute   （chat 端点限流）

生产环境使用 Nginx 反向代理时，限流 key 从 X-Forwarded-For 提取真实客户端 IP。
多 worker 部署时可通过 RATE_LIMIT_STORAGE_URI=redis://... 共享限流状态。
"""

from __future__ import annotations

import os

from slowapi import Limiter

_rate_limit_default = os.getenv("ONTOAGENT_RATE_LIMIT", "60/minute")
_chat_rate_limit = os.getenv("ONTOAGENT_CHAT_RATE_LIMIT", "10/minute")
_storage_uri = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")


def _get_real_ip(request) -> str:  # slowapi 协议不要求类型注解
    """获取真实客户端 IP。

    生产环境经 Nginx 反向代理时，request.client.host 是 Nginx 的 IP 而非用户 IP。
    从 X-Forwarded-For header 取第一个地址（即原始客户端 IP）。
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


limiter = Limiter(key_func=_get_real_ip, default_limits=[_rate_limit_default], storage_uri=_storage_uri)
