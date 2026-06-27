"""Git repository watcher for detecting code changes."""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ontoagent.butler.event_bus import ButlerEvent

if TYPE_CHECKING:
    from ontoagent.butler.event_bus import EventBus


class GitWatcher:
    """Git 仓库变更监视器。

    定时检测 Git 仓库的 HEAD 变更，发布 code.changed 事件到 EventBus。
    """

    def __init__(
        self,
        repo_path: Path,
        bus: EventBus,
        poll_interval: float = 30.0,
        initial_scan: bool = False,
    ) -> None:
        """初始化 GitWatcher。

        Args:
            repo_path: Git 仓库路径。
            bus: EventBus 实例，用于发布事件。
            poll_interval: 轮询间隔（秒）。
            initial_scan: 是否在首次轮询时发布 full_scan 事件。
        """
        self._repo_path = repo_path
        self._bus = bus
        self._poll_interval = poll_interval
        self._initial_scan = initial_scan
        self._last_ref: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """启动定时轮询。

        如果 initial_scan=True，首次轮询时会发布事件。
        """
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """停止轮询。"""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _poll_loop(self) -> None:
        """轮询循环。"""
        while self._running:
            try:
                await self._poll()
            except asyncio.CancelledError:
                break
            except Exception:
                # 静默失败，避免轮询异常影响主循环
                pass

            # 使用 sleep 而不是 asyncio.sleep，这样可以更快响应 stop
            await asyncio.sleep(self._poll_interval)

    async def _poll(self) -> None:
        """一次轮询：获取当前 HEAD → 比较 → 发布事件。"""
        try:
            # 使用 asyncio.to_thread 运行同步的 subprocess 调用
            current_ref = await asyncio.to_thread(self._get_head_ref)
        except (FileNotFoundError, subprocess.CalledProcessError, RuntimeError):
            # 仓库不存在或不是 Git 仓库，跳过
            return

        if current_ref == self._last_ref:
            # 没有变更
            return

        if self._last_ref is None:
            # 首次轮询
            if self._initial_scan:
                # 发布 full_scan 事件
                event = ButlerEvent(
                    event_type="code.changed",
                    payload={
                        "since": "",
                        "full_scan": True,
                        "repo_path": str(self._repo_path),
                        "file_extension": "",
                    },
                    source="git_watcher",
                )
                await self._bus.publish(event)
        else:
            # 检测到变更
            event = ButlerEvent(
                event_type="code.changed",
                payload={
                    "since": self._last_ref,
                    "full_scan": False,
                    "repo_path": str(self._repo_path),
                    "file_extension": "",
                },
                source="git_watcher",
            )
            await self._bus.publish(event)

        # 更新最后引用
        self._last_ref = current_ref

    def _get_head_ref(self) -> str:
        """获取当前 HEAD commit hash。

        Returns:
            HEAD commit hash，或空字符串。

        Raises:
            FileNotFoundError: git 命令不存在。
            subprocess.CalledProcessError: git 命令执行失败。
            RuntimeError: 仓库路径无效。
        """
        if not self._repo_path.exists():
            raise RuntimeError(f"Repository path does not exist: {self._repo_path}")

        # 运行 git rev-parse HEAD
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self._repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

        return result.stdout.strip()

    async def trigger(self, since: str | None = None) -> None:
        """手动触发一次变更检测（用于测试和 CLI 调用）。

        Args:
            since: 可选的起始 ref，如果不提供则使用当前 _last_ref。
        """
        trigger_ref = since if since is not None else self._last_ref

        event = ButlerEvent(
            event_type="code.changed",
            payload={
                "since": trigger_ref or "",
                "full_scan": not bool(trigger_ref),
                "repo_path": str(self._repo_path),
                "file_extension": "",
            },
            source="git_watcher.manual",
        )
        await self._bus.publish(event)
