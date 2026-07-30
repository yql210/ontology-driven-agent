"""安全的 Git 仓库克隆服务。

设计目标：
- 防止 SSRF：URL scheme + hostname 白名单
- 防止命令注入：``subprocess.run`` + 参数列表（``shell=False``），URL 经 ``urllib.parse`` 校验
- 防止路径穿越：临时子目录用 ``uuid4`` 命名
- 资源控制：浅克隆 ``--depth 1 --single-branch`` + 超时
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from ontoagent.config import OntoAgentConfig

logger = logging.getLogger(__name__)


class GitCloneError(RuntimeError):
    """Git clone 失败。"""


class GitCloneService:
    """安全的 Git clone 服务。

    安全措施：
    - URL scheme 白名单（``https``/``git``/``ssh``），防止 ``file://`` 等危险 scheme
    - URL hostname 白名单（``config.git_allowed_hosts``），防 SSRF
    - 浅克隆 ``--depth 1 --single-branch``，节省带宽与磁盘
    - 超时控制（``config.git_clone_timeout``），防止挂死
    - 临时目录用 ``uuid4`` 命名，防路径穿越
    - URL 经 ``urllib.parse`` 校验，``subprocess.run`` 使用参数列表（非 shell）
    """

    ALLOWED_SCHEMES: frozenset[str] = frozenset({"https", "git", "ssh"})

    def __init__(self, config: OntoAgentConfig) -> None:
        """初始化克隆服务。

        Args:
            config: OntoAgent 配置（读取 ``git_allowed_hosts``、``git_clone_timeout``、
                ``git_work_dir``）。
        """
        self._config = config

    async def clone(
        self,
        repo_url: str,
        branch: str = "main",
        token: str | None = None,
    ) -> Path:
        """安全 clone Git 仓库到临时目录。

        Args:
            repo_url: Git 远端 URL（必须是 scheme + host 白名单内的合法 URL）。
            branch: 待克隆分支，默认 ``main``。
            token: 可选访问令牌，注入到 https URL 的 userinfo 部分。

        Returns:
            克隆完成的本地目录路径（位于 ``config.git_work_dir`` 之下）。

        Raises:
            GitCloneError: URL 校验失败或 git 命令执行失败。
        """
        self._validate_url(repo_url)

        work_dir = Path(self._config.git_work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        target = work_dir / f"repo-{uuid.uuid4().hex}"

        authed_url = self._inject_token(repo_url, token) if token else repo_url

        cmd: list[str] = [
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            branch,
            authed_url,
            str(target),
        ]

        logger.info("[GitClone] cloning %s (branch=%s) → %s", self._mask_url(repo_url), branch, target)

        try:
            await asyncio.to_thread(
                subprocess.run,
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=self._config.git_clone_timeout,
            )
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            raise GitCloneError(f"git clone failed (exit={e.returncode}): {stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise GitCloneError(f"git clone timed out after {self._config.git_clone_timeout}s") from e

        logger.info("[GitClone] clone complete: %s", target)
        return target

    def _validate_url(self, url: str) -> None:
        """校验 URL scheme 和 hostname。

        Args:
            url: 待校验的 URL 字符串。

        Raises:
            GitCloneError: scheme 不在白名单，或 hostname 不在配置的白名单内。
        """
        if not url or not url.strip():
            raise GitCloneError("Empty repository URL")

        # SSH 形如 git@github.com:org/repo.git 不被 urlparse 解析为带 scheme
        # 先按 SSH 简写格式兜底处理
        candidate = url
        if "://" not in candidate and candidate.startswith("git@"):
            # 转换为 ssh://git@host/path 形式再做校验
            try:
                _, rest = candidate.split("@", 1)
                host, path = rest.split(":", 1)
                candidate = f"ssh://git@{host}/{path}"
            except ValueError as e:
                raise GitCloneError(f"Invalid SSH URL: {url}") from e

        parsed = urlparse(candidate)
        if parsed.scheme not in self.ALLOWED_SCHEMES:
            raise GitCloneError(f"URL scheme '{parsed.scheme}' not in allowed {sorted(self.ALLOWED_SCHEMES)}")
        hostname = (parsed.hostname or "").lower()
        allowed = {h.lower() for h in self._config.git_allowed_hosts}
        if not hostname or hostname not in allowed:
            raise GitCloneError(f"URL host '{hostname}' not in whitelist {sorted(allowed)}")

    def _inject_token(self, repo_url: str, token: str) -> str:
        """把 token 注入到 https URL 的 userinfo 部分。

        Args:
            repo_url: 原始 https URL。
            token: 访问令牌。

        Returns:
            形如 ``https://x-access-token:<token>@host/...`` 的 URL。
        """
        parsed = urlparse(repo_url)
        netloc = f"x-access-token:{token}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))

    def _mask_url(self, url: str) -> str:
        """日志中隐藏 URL 的潜在 token，避免凭据泄漏到日志。"""
        if "@" in url and "://" in url:
            scheme, rest = url.split("://", 1)
            if "@" in rest:
                _, host_part = rest.split("@", 1)
                return f"{scheme}://***@{host_part}"
        return url

    def _get_head_commit(self, repo_path: Path) -> str:
        """获取仓库当前 HEAD commit hash。

        Args:
            repo_path: 仓库本地路径。

        Returns:
            HEAD commit 的完整 hash 字符串。

        Raises:
            GitCloneError: git 命令执行失败。
        """
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise GitCloneError(f"failed to read HEAD commit: {e}") from e
        return result.stdout.strip()
