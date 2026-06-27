from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from ontoagent.butler.event_bus import ButlerEvent
from ontoagent.butler.handlers.base import BaseHandler, HandlerContext, HandlerResult

if TYPE_CHECKING:
    pass


class KnowledgeUpdateHandler(BaseHandler):
    """处理代码变更事件，执行增量知识更新。"""

    handler_id = "knowledge.update"
    event_types = ["code.changed"]

    async def handle(self, event: ButlerEvent, ctx: HandlerContext) -> HandlerResult:
        """处理 code.changed 事件，调用 IncrementalUpdater。

        Args:
            event: ButlerEvent，payload 应包含 since, repo_path, full_scan。
            ctx: HandlerContext。

        Returns:
            HandlerResult，包含 UpdateReport 数据或错误信息。
        """
        from ontoagent.pipeline.incremental_updater import IncrementalUpdater

        payload = event.payload
        since = payload.get("since", "HEAD~1")
        repo_path_str = payload.get("repo_path", "")
        full_scan = payload.get("full_scan", False)

        repo_path = Path(repo_path_str) if repo_path_str else None

        try:
            # 创建 updater
            updater = IncrementalUpdater(config=ctx.config, repo_path=repo_path)

            # 用 asyncio.to_thread 包裹同步调用
            report = await asyncio.to_thread(updater.update, since=since, full_scan=full_scan)

            # 关闭 updater
            updater.close()

            # 记录审计日志（无论成功/失败）
            if ctx.guard:
                await ctx.guard.log_operation(
                    op="knowledge_update",
                    target_type="repo",
                    target_id=repo_path_str or "cwd",
                    before=None,
                    after={"changeset_id": report.changeset_id, "changes_detected": report.changes_detected},
                    operator=self.handler_id,
                )

            return HandlerResult(success=True, data=report.to_dict())

        except Exception as e:
            error_msg = str(e)

            # 记录失败审计日志
            if ctx.guard:
                await ctx.guard.log_operation(
                    op="knowledge_update",
                    target_type="repo",
                    target_id=repo_path_str or "cwd",
                    before=None,
                    after={"error": error_msg},
                    operator=self.handler_id,
                )

            return HandlerResult(success=False, error=error_msg)


class FullBuildHandler(BaseHandler):
    """处理全量构建事件，调用 OntoAgentBuilder。"""

    handler_id = "knowledge.full_build"
    event_types = ["build.full"]

    async def handle(self, event: ButlerEvent, ctx: HandlerContext) -> HandlerResult:
        """处理 build.full 事件，调用 OntoAgentBuilder.full_build。

        Args:
            event: ButlerEvent，payload 应包含 repo_path。
            ctx: HandlerContext。

        Returns:
            HandlerResult，包含 BuildResult 数据或错误信息。
        """
        from ontoagent.pipeline.builder import OntoAgentBuilder

        payload = event.payload
        repo_path_str = payload.get("repo_path", "")

        repo_path = Path(repo_path_str) if repo_path_str else Path.cwd()

        try:
            # 创建 builder
            builder = OntoAgentBuilder(config=ctx.config)

            # 用 asyncio.to_thread 包裹同步调用
            result = await asyncio.to_thread(builder.build, repo_path)

            # 关闭 builder
            builder.close()

            # 记录审计日志
            if ctx.guard:
                await ctx.guard.log_operation(
                    op="full_build",
                    target_type="repo",
                    target_id=str(repo_path),
                    before=None,
                    after={"files_scanned": result.files_scanned, "entities_created": result.entities_created},
                    operator=self.handler_id,
                )

            return HandlerResult(success=True, data=result.to_dict())

        except Exception as e:
            error_msg = str(e)

            # 记录失败审计日志
            if ctx.guard:
                await ctx.guard.log_operation(
                    op="full_build",
                    target_type="repo",
                    target_id=str(repo_path),
                    before=None,
                    after={"error": error_msg},
                    operator=self.handler_id,
                )

            return HandlerResult(success=False, error=error_msg)
