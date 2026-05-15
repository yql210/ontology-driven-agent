# Butler 服务化 — serve 模式端到端集成

## 目标
让 `layerkg butler serve` 真正能工作：Git commit → GitWatcher 检测变更 → 自动触发 KnowledgeUpdateHandler → KG 增量演化

## 问题分析
`ButlerEngine._dispatch_event()` 是空操作（只有 docstring，没有实现）。
GitWatcher 发布事件到 EventBus → `_dispatch_event` 被调用 → 什么都不做 → 事件丢失。

`submit_event` 路径是正常的（publish + dispatch + completion），但 GitWatcher 走的是 EventBus publish → subscriber callback 路径。

## 修改范围

### File 1: `src/layerkg/butler/engine.py`
**修改 `_dispatch_event` 方法**：当 GitWatcher 等外部组件通过 EventBus 发布事件时，调用 `self._scheduler.dispatch(event)` 分发到匹配的 Handler。

```python
async def _dispatch_event(self, event: ButlerEvent) -> None:
    """EventBus 回调 — 外部事件（如 GitWatcher）分发到 Scheduler。"""
    if not self._running:
        return
    # 分发到 Scheduler（触发 KnowledgeUpdateHandler 等）
    results = await self._scheduler.dispatch(event)
    # 发布 completion 事件
    for result in results:
        if result.success:
            completion = ButlerEvent(
                event_type="handler.completed",
                payload={
                    "original_event_type": event.event_type,
                    "handler_id": result.handler_id,
                    "success": True,
                    "file_extension": self._extract_file_extension(event),
                    "duration_ms": 0,
                },
                source="butler.engine",
            )
        else:
            completion = ButlerEvent(
                event_type="handler.failed",
                payload={
                    "original_event_type": event.event_type,
                    "handler_id": result.handler_id,
                    "success": False,
                    "error": result.error,
                    "attempts": result.attempts,
                },
                source="butler.engine",
            )
        await self._bus.publish(completion)
        # 注意：不再级联 dispatch（避免 ReflectionHandler 触发循环）
```

**注意**：`submit_event` 已有完整的 dispatch + completion + cascade 逻辑。
`_dispatch_event` 只处理外部来源事件（GitWatcher），**不做级联**（防止 handler.completed → ReflectionHandler → 再次触发更新）。
如果需要 cascade，可通过 `skip_cascade` 标志控制。

### File 2: `src/layerkg/cli.py`
**增强 `serve` 命令**：
1. 添加 `--log-level` 选项（默认 INFO）
2. 注册 ReflectionHandler（监听 handler.completed）
3. 添加状态打印：每隔 N 秒打印引擎状态摘要

```python
@butler.command()
@click.option("--repo", "-r", type=click.Path(exists=True), default=".", help="仓库路径")
@click.option("--poll-interval", "-p", type=float, default=30.0, help="轮询间隔（秒）")
@click.option("--log-level", default="INFO", type=click.Choice(["DEBUG", "INFO", "WARNING"]))
def serve(repo: str, poll_interval: float, log_level: str) -> None:
    """启动 Butler Engine，监控仓库变更。"""
    import asyncio
    import logging

    from layerkg.butler.engine import ButlerEngine
    from layerkg.butler.handlers.knowledge_update import FullBuildHandler, KnowledgeUpdateHandler
    from layerkg.butler.handlers.reflection import ReflectionHandler
    from layerkg.butler.watchers.git_watcher import GitWatcher

    logging.basicConfig(level=getattr(logging, log_level), format="%(asctime)s %(levelname)s %(message)s")
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
            # 创建并启动 GitWatcher
            watcher = GitWatcher(repo_path, engine._bus, poll_interval=poll_interval, initial_scan=False)
            await watcher.start()

            logger.info(f"Butler Engine started, monitoring {repo_path} (poll: {poll_interval}s)")
            logger.info("Press Ctrl+C to stop")

            try:
                while engine._running:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                logger.info("Shutting down...")
            finally:
                await watcher.stop()
                logger.info("Butler Engine stopped")

    asyncio.run(_serve())
```

### File 3: `src/layerkg/butler/handlers/reflection.py`
检查现有 ReflectionHandler 是否监听 `handler.completed` 事件。确保它不会在 `_dispatch_event` 路径下被触发（因为 `_dispatch_event` 不做 cascade）。

**决策**：serve 模式下 ReflectionHandler 暂不级联触发（避免循环），只在 `submit_event` 路径级联。

### File 4: Tests — `tests/unit/test_butler_serve_integration.py`
新增集成测试，验证完整 serve 流程：
1. 启动 ButlerEngine + GitWatcher
2. 模拟 Git commit（修改文件 + commit）
3. 等待 GitWatcher 检测到变更
4. 验证 KnowledgeUpdateHandler 被触发
5. 验证 KG 发生变更

```python
@pytest.mark.asyncio
async def test_serve_detects_git_commit(tmp_path, isolated_config):
    """GitWatcher 检测到 commit → engine._dispatch_event → scheduler.dispatch → handler 执行"""
    # 1. 初始化 git repo
    # 2. 创建 ButlerEngine + GitWatcher
    # 3. 启动引擎 + watcher
    # 4. 创建新文件 + commit
    # 5. 等待 watcher 检测（使用短 poll_interval 如 1s）
    # 6. 验证 handler 被调用（通过 mock 或 status 检查）
```

**不需要**真正的 Neo4j 写入——mock `IncrementalUpdater.update` 即可。

## 测试要求
- 所有新测试必须 pass
- 现有 1032 测试不能 break
- `uv run ruff check src/ tests/` clean

## 验收标准
1. `layerkg butler serve -r . -p 5` 启动后能监听变更
2. Git commit 后 5s 内自动触发增量更新
3. 日志清晰显示完整流程
4. Ctrl+C 优雅退出
