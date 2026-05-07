from __future__ import annotations

import logging
from pathlib import Path

import click

from layerkg.builder import LayerKGBuilder
from layerkg.config import LayerKGConfig
from layerkg.incremental_updater import IncrementalUpdater


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="详细输出")
def main(verbose: bool) -> None:
    """LayerKG — 本体驱动的可更新知识图谱引擎。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
def build(repo_path: str) -> None:
    """全量构建知识图谱。

    扫描指定目录下的所有 Python 文件，解析代码结构，
    将实体和关系存储到 Neo4j 和 ChromaDB。
    """
    config = LayerKGConfig.from_env()
    with LayerKGBuilder(config) as builder:
        result = builder.build(Path(repo_path))
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
