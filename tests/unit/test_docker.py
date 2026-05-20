from __future__ import annotations

from pathlib import Path


def test_dockerfile_exists():
    """Dockerfile 存在。"""
    dockerfile = Path(__file__).parent.parent.parent / "Dockerfile"
    assert dockerfile.exists()


def test_dockerfile_contains_python_version():
    """Dockerfile 使用 python:3.13-slim。"""
    dockerfile = Path(__file__).parent.parent.parent / "Dockerfile"
    content = dockerfile.read_text()
    assert "python:3.13-slim" in content


def test_dockerfile_contains_uv():
    """Dockerfile 使用 uv 安装依赖。"""
    dockerfile = Path(__file__).parent.parent.parent / "Dockerfile"
    content = dockerfile.read_text()
    assert "ghcr.io/astral-sh/uv" in content


def test_dockerfile_contains_entrypoint():
    """Dockerfile 设置 ENTRYPOINT 为 layerkg。"""
    dockerfile = Path(__file__).parent.parent.parent / "Dockerfile"
    content = dockerfile.read_text()
    assert 'ENTRYPOINT ["layerkg"]' in content


def test_docker_compose_exists():
    """docker-compose.yml 存在。"""
    compose_file = Path(__file__).parent.parent.parent / "docker-compose.yml"
    assert compose_file.exists()


def test_docker_compose_yaml_valid():
    """docker-compose.yml 是合法的 YAML。"""
    import yaml

    compose_file = Path(__file__).parent.parent.parent / "docker-compose.yml"
    content = compose_file.read_text()
    # 不应抛出 YAMLError
    yaml.safe_load(content)


def test_docker_compose_neo4j_internal_uri():
    """docker-compose.yml 中 layerkg 服务的 NEO4J_URI 使用内部服务名 neo4j。"""
    import yaml

    compose_file = Path(__file__).parent.parent.parent / "docker-compose.yml"
    content = compose_file.read_text()
    config = yaml.safe_load(content)

    layerkg_env = config["services"]["layerkg"]["environment"]
    assert layerkg_env["NEO4J_URI"] == "bolt://neo4j:7687"
    assert "localhost" not in layerkg_env["NEO4J_URI"]


def test_docker_compose_has_volumes():
    """docker-compose.yml 定义了 volumes 持久化。"""
    import yaml

    compose_file = Path(__file__).parent.parent.parent / "docker-compose.yml"
    content = compose_file.read_text()
    config = yaml.safe_load(content)

    assert "volumes" in config
    assert "neo4j_data" in config["volumes"]
    assert "chroma_data" in config["volumes"]


def test_dockerignore_exists():
    """.dockerignore 存在。"""
    dockerignore = Path(__file__).parent.parent.parent / ".dockerignore"
    assert dockerignore.exists()
