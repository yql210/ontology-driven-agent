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
    """Dockerfile 设置 ENTRYPOINT 为 ontoagent。"""
    dockerfile = Path(__file__).parent.parent.parent / "Dockerfile"
    content = dockerfile.read_text()
    assert 'ENTRYPOINT ["ontoagent"]' in content


def test_dockerfile_has_default_cmd():
    """Dockerfile 默认启动 Web API。"""
    dockerfile = Path(__file__).parent.parent.parent / "Dockerfile"
    content = dockerfile.read_text()
    assert "CMD" in content
    assert "web" in content


def test_docker_compose_exists():
    """docker-compose.yml 存在。"""
    compose_file = Path(__file__).parent.parent.parent / "docker-compose.yml"
    assert compose_file.exists()


def test_docker_compose_yaml_valid():
    """docker-compose.yml 是合法的 YAML。"""
    import yaml

    compose_file = Path(__file__).parent.parent.parent / "docker-compose.yml"
    content = compose_file.read_text()
    yaml.safe_load(content)


def test_docker_compose_has_nebula_services():
    """docker-compose.yml 包含 NebulaGraph 三个服务。"""
    import yaml

    compose_file = Path(__file__).parent.parent.parent / "docker-compose.yml"
    content = compose_file.read_text()
    config = yaml.safe_load(content)

    services = config["services"]
    assert "nebula-metad" in services
    assert "nebula-storaged" in services
    assert "nebula-graphd" in services


def test_docker_compose_web_uses_nebula():
    """ontoagent-web 服务使用 NebulaGraph 后端。"""
    import yaml

    compose_file = Path(__file__).parent.parent.parent / "docker-compose.yml"
    content = compose_file.read_text()
    config = yaml.safe_load(content)

    web_env = config["services"]["ontoagent-web"]["environment"]
    assert web_env["ONTOAGENT_GRAPH_BACKEND"] == "nebula"
    assert web_env["ONTOAGENT_NEBULA_HOST"] == "nebula-graphd"


def test_docker_compose_has_volumes():
    """docker-compose.yml 定义了 volumes 持久化。"""
    import yaml

    compose_file = Path(__file__).parent.parent.parent / "docker-compose.yml"
    content = compose_file.read_text()
    config = yaml.safe_load(content)

    assert "volumes" in config
    assert "chroma_data" in config["volumes"]
    # NebulaGraph volumes
    assert "nebula_meta_data" in config["volumes"]
    assert "nebula_storage_data" in config["volumes"]


def test_dockerignore_exists():
    """.dockerignore 存在。"""
    dockerignore = Path(__file__).parent.parent.parent / ".dockerignore"
    assert dockerignore.exists()


def test_nginx_config_exists():
    """deploy/nginx.conf 存在（前端反代配置）。"""
    nginx_conf = Path(__file__).parent.parent.parent / "deploy" / "nginx.conf"
    assert nginx_conf.exists()
