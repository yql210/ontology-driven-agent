"""Rate limiter — 基于 slowapi 的请求限流。

全局限流默认 60/minute，Chat 端点默认 10/minute（LLM 调用成本高）。
所有阈值均可通过环境变量调整：
    ONTOAGENT_RATE_LIMIT=60/minute        （全局限流）
    ONTOAGENT_CHAT_RATE_LIMIT=10/minute   （chat 端点限流）
"""

from __future__ import annotations

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

_rate_limit_default = os.getenv("ONTOAGENT_RATE_LIMIT", "60/minute")

limiter = Limiter(key_func=get_remote_address, default_limits=[_rate_limit_default])
