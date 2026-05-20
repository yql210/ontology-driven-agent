from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from layerkg.builder import LayerKGBuilder
from layerkg.config import LayerKGConfig
from layerkg.incremental_updater import IncrementalUpdater
from layerkg.migrations.registry import MigrationRegistry
from layerkg.migrations.runner import MigrationRunner
from layerkg.neo4j_store import Neo4jGraphStore
from layerkg.schema_version import SchemaStatus, check_schema_version, get_current_db_version


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="详细输出")
def main(verbose: bool) -> None:
    """LayerKG — 本体驱动的可更新知识图谱引擎。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)


@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option("--skip-semantic", is_flag=True, help="跳过语义提取 (Stage 3)")
@click.option("--skip-clustering", is_flag=True, help="跳过模块聚类 (Stage 4)")
@click.option("--verbose-build", is_flag=True, help="逐阶段输出详情")
@click.option("--clear", is_flag=True, help="清空数据库后重建")
def build(
    repo_path: str,
    skip_semantic: bool,
    skip_clustering: bool,
    verbose_build: bool,
    clear: bool,
) -> None:
    """全量构建知识图谱。

    扫描指定目录下的所有 Python 文件，解析代码结构，
    将实体和关系存储到 Neo4j 和 ChromaDB。
    """
    config = LayerKGConfig.from_env()
    with LayerKGBuilder(config) as builder:
        result = builder.build(
            Path(repo_path),
            skip_semantic=skip_semantic,
            skip_clustering=skip_clustering,
            clear=clear,
        )
        if verbose_build:
            click.echo(str(result))
        else:
            click.echo(
                f"Build complete: {result.files_scanned} files scanned, "
                f"{result.entities_created} entities created, "
                f"{result.relations_created} relations created"
            )


@main.command()
@click.argument("text")
@click.option("--type", "-t", "entity_type", help="实体类型过滤 (如 function/class/module)")
@click.option("--limit", "-n", default=10, help="返回数量", show_default=True)
def query(text: str, entity_type: str | None, limit: int) -> None:
    """语义搜索代码实体。

    使用向量相似度搜索相关代码。
    """
    config = LayerKGConfig.from_env()
    with LayerKGBuilder(config) as builder:
        results = builder.query(text, n_results=limit, entity_type=entity_type)
        if not results:
            click.echo("No results found.")
            return

        click.echo(f"Found {len(results)} result(s):\n")
        for r in results:
            distance = r.get("distance")
            dist_str = f"{distance:.4f}" if distance is not None else "N/A"
            click.echo(
                f"  [{r['metadata'].get('entity_type', 'N/A')}] "
                f"{r['metadata'].get('name', 'N/A')} "
                f"(distance: {dist_str})"
            )


@main.command()
def info() -> None:
    """显示配置和存储状态。"""
    config = LayerKGConfig.from_env()
    click.echo("Configuration:")
    click.echo(f"  Neo4j: {config.neo4j_uri}")
    click.echo(f"  Ollama: {config.ollama_base_url}")
    click.echo(f"  Model: {config.embedding_model}")
    click.echo(f"  ChromaDB: {config.chroma_persist_dir}")

    with LayerKGBuilder(config) as builder:
        info_data = builder.info()
        click.echo(f"\nEntities in ChromaDB: {info_data.get('chroma_count', 'N/A')}")


@main.command()
def version() -> None:
    """显示 LayerKG 版本信息。"""
    click.echo("LayerKG v0.1.0")
    click.echo(f"Python: {sys.version.split()[0]}")
    click.echo(f"Neo4j URI: {LayerKGConfig.from_env().neo4j_uri}")


@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option("--since", default="HEAD~1", help="Git ref 对比基准", show_default=True)
@click.option("--dry-run", is_flag=True, help="只检测不执行")
@click.option("--full-scan", is_flag=True, help="全量扫描替代 Git diff")
def update(repo_path: str, since: str, dry_run: bool, full_scan: bool) -> None:
    """增量更新知识图谱。"""
    config = LayerKGConfig.from_env()
    with IncrementalUpdater(config, repo_path=Path(repo_path)) as updater:
        report = updater.update(since, dry_run=dry_run, full_scan=full_scan)
        click.echo(f"Update complete: {report.to_dict()}")


@main.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "http"]),
    default="stdio",
    help="MCP 传输协议",
    show_default=True,
)
@click.option("--port", default=8000, type=int, help="HTTP 模式端口", show_default=True)
def serve(transport: str, port: int) -> None:
    """启动 LayerKG MCP Server。"""
    from layerkg.mcp_server import mcp

    if transport == "http":
        click.echo(f"Starting MCP server on http://localhost:{port}")
        mcp.run(transport="http", port=port)
    else:
        click.echo("Starting MCP server on stdio")
        mcp.run()


@main.command()
@click.option("--host", default="0.0.0.0", help="监听地址")
@click.option("--port", default=8000, help="监听端口")
@click.option("--reload", is_flag=True, help="开发模式热重载")
def web(host: str, port: int, reload: bool) -> None:
    """启动 LayerKG Web API Server"""
    import uvicorn

    if reload:
        uvicorn.run(
            "layerkg.web.app:create_app",
            host=host,
            port=port,
            reload=True,
            factory=True,
        )
    else:
        from layerkg.web.app import create_app

        app = create_app()
        uvicorn.run(app, host=host, port=port)


@main.command()
@click.argument("question", required=False)
@click.option("--interactive", "-i", is_flag=True, help="交互式对话模式")
def ask(question: str | None, interactive: bool) -> None:
    """向代码知识图谱提问。示例：layerkg ask "merge_node 被谁调用" """
    import asyncio
    import uuid

    from layerkg.agent.graph import run_query

    if interactive:
        click.echo("🔍 LayerKG 交互模式")
        click.echo("输入问题查询代码知识图谱，输入 quit/exit 退出\n")

        thread_id = str(uuid.uuid4())

        while True:
            try:
                q = click.prompt("", prompt_suffix="> ").strip()
            except (EOFError, KeyboardInterrupt):
                click.echo("\n再见！")
                break
            if q.lower() in ("quit", "exit", "q"):
                click.echo("再见！")
                break
            if not q:
                continue
            try:
                answer = asyncio.run(run_query(q, thread_id=thread_id))
                click.echo(f"\n{answer}\n")
                click.echo("-" * 60)
            except KeyboardInterrupt:
                click.echo("\n中断当前查询")
            except Exception as e:
                click.echo(f"\n错误: {e}\n")
    elif question:
        answer = asyncio.run(run_query(question))
        click.echo(answer)
    else:
        click.echo("请提供问题或使用 -i 进入交互模式")


@main.group()
def butler() -> None:
    """Butler Engine — 事件驱动的知识管理引擎。"""
    pass


@butler.command()
@click.option("--repo", "-r", type=click.Path(exists=True), default=".", help="仓库路径", show_default=True)
@click.option("--poll-interval", "-p", type=float, default=30.0, help="轮询间隔（秒）", show_default=True)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="日志级别",
    show_default=True,
)
def serve(repo: str, poll_interval: float, log_level: str) -> None:  # noqa: F811
    """启动 Butler Engine，监控仓库变更。"""
    import asyncio
    import logging

    from layerkg.butler.engine import ButlerEngine
    from layerkg.butler.handlers.knowledge_update import FullBuildHandler, KnowledgeUpdateHandler
    from layerkg.butler.handlers.reflection import ReflectionHandler
    from layerkg.butler.watchers.git_watcher import GitWatcher

    # 配置日志
    logging.basicConfig(
        level=getattr(logging, log_level.upper()), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logger = logging.getLogger("butler.serve")

    async def _serve() -> None:
        config = LayerKGConfig.from_env()
        engine = ButlerEngine(config)

        # 注册所有 Handlers
        engine.register_handler(KnowledgeUpdateHandler())
        engine.register_handler(FullBuildHandler())
        engine.register_handler(ReflectionHandler())

        repo_path = Path(repo)

        async with engine:
            # 创建并启动 GitWatcher（initial_scan=False 避免启动时自动全量构建）
            watcher = GitWatcher(repo_path, engine._bus, poll_interval=poll_interval, initial_scan=False)
            await watcher.start()

            logger.info(f"Butler Engine started, monitoring {repo_path} (poll: {poll_interval}s)")
            click.echo(f"Butler Engine started, monitoring {repo_path} (poll: {poll_interval}s)")
            click.echo("Press Ctrl+C to stop")

            try:
                # 运行直到收到中断信号
                while engine._running:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                click.echo("\nShutting down...")
            finally:
                await watcher.stop()
                logger.info("Butler Engine stopped")
                click.echo("Butler Engine stopped")

    asyncio.run(_serve())


@butler.command()
@click.option("--repo", "-r", type=click.Path(exists=True), default=".", help="仓库路径", show_default=True)
@click.option("--since", "-s", default="HEAD~1", help="Git ref 对比基准", show_default=True)
def update(repo: str, since: str) -> None:  # noqa: F811
    """手动触发增量更新。"""
    import asyncio
    import json

    from layerkg.butler.engine import ButlerEngine
    from layerkg.butler.event_bus import ButlerEvent
    from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler

    async def _update() -> None:
        config = LayerKGConfig.from_env()
        engine = ButlerEngine(config)
        engine.register_handler(KnowledgeUpdateHandler())

        async with engine:
            event = ButlerEvent(
                event_type="code.changed",
                payload={
                    "since": since,
                    "full_scan": False,
                    "repo_path": str(Path(repo).resolve()),
                    "file_extension": "",
                },
                source="cli",
            )
            results = await engine.submit_event(event)

            for result in results:
                if result.success:
                    click.echo(json.dumps(result.data, indent=2, ensure_ascii=False))
                else:
                    click.echo(f"Error: {result.error}", err=True)
                    raise click.Abort()

    asyncio.run(_update())


@butler.command()
@click.option("--repo", "-r", type=click.Path(exists=True), default=".", help="仓库路径", show_default=True)
def build(repo: str) -> None:  # noqa: F811
    """手动触发全量构建。"""
    import asyncio
    import json

    from layerkg.butler.engine import ButlerEngine
    from layerkg.butler.event_bus import ButlerEvent
    from layerkg.butler.handlers.knowledge_update import FullBuildHandler

    async def _build() -> None:
        config = LayerKGConfig.from_env()
        engine = ButlerEngine(config)
        engine.register_handler(FullBuildHandler())

        async with engine:
            event = ButlerEvent(
                event_type="build.full",
                payload={
                    "repo_path": str(Path(repo).resolve()),
                },
                source="cli",
            )
            results = await engine.submit_event(event)

            for result in results:
                if result.success:
                    click.echo(json.dumps(result.data, indent=2, ensure_ascii=False))
                else:
                    click.echo(f"Error: {result.error}", err=True)
                    raise click.Abort()

    asyncio.run(_build())


@butler.command()
def status() -> None:
    """显示 Butler Engine 状态。"""
    import asyncio
    import json

    from layerkg.butler.engine import ButlerEngine

    async def _status() -> None:
        config = LayerKGConfig.from_env()
        engine = ButlerEngine(config)

        # 不启动引擎，只查询初始状态
        status_dict = await engine.status()
        click.echo(json.dumps(status_dict, indent=2, ensure_ascii=False))

    asyncio.run(_status())


@main.command()
@click.option("--target", default=None, help="目标版本（用于回滚）")
def migrate(target: str | None) -> None:
    """运行 schema 迁移。"""
    config = LayerKGConfig.from_env()
    store = Neo4jGraphStore(config.neo4j_uri, config.neo4j_user, config.neo4j_password)
    registry = MigrationRegistry()
    runner = MigrationRunner(store, registry)

    if target:
        rolled = runner.rollback(target)
        if rolled:
            click.echo(f"Rolled back: {rolled}")
        else:
            click.echo("Already at target version.")
    else:
        status = check_schema_version(store)
        click.echo(f"Current status: {status.value}")
        applied = runner.run_pending()
        if applied:
            click.echo(f"Applied {len(applied)} migrations: {applied}")
        else:
            click.echo("No pending migrations.")
    store.close()
