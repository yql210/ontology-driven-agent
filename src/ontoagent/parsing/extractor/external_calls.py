from __future__ import annotations

from urllib.parse import urlparse

from ontoagent.parsing.parser.base import ExtractedRelation


def extract_external_calls_python(root_node, source: bytes, module_name: str, file_path: str) -> list[ExtractedRelation]:
    """扫描 Python AST，提取 HTTP 客户端调用和 MQ producer 调用。

    Args:
        root_node: tree-sitter AST 根节点。
        source: 源码字节流。
        module_name: 模块名称。
        file_path: 文件路径。

    Returns:
        ExtractedRelation 列表。
    """
    relations: list[ExtractedRelation] = []

    def walk(node) -> None:
        if node.type == "call":
            func = node.child_by_field_name("function")
            if func:
                if func.type == "attribute":
                    # requests.post(...) / httpx.AsyncClient.get(...) / kafka_producer.send(...)
                    parts = [c.text.decode() for c in func.children if c.type == "identifier"]
                    attr_path = tuple(parts)

                    # HTTP client detection
                    if len(attr_path) >= 2 and attr_path[-2] in ("requests", "httpx"):
                        args = node.child_by_field_name("arguments")
                        if args:
                            for arg in args.children:
                                if arg.type == "string":
                                    url = arg.text.decode().strip("\"'")
                                    hostname = urlparse(url).hostname or url
                                    relations.append(
                                        ExtractedRelation(
                                            source_name=module_name,
                                            source_type="module",
                                            target_name=hostname,
                                            target_type="ServiceEntity",
                                            relation_type="calls_service",
                                            file_path=file_path,
                                        )
                                    )
                                    break

                    # MQ producer: kafka_producer.send("topic") / rabbitmq.publish(...)
                    elif len(attr_path) >= 2 and attr_path[-1] in ("send", "publish"):
                        args = node.child_by_field_name("arguments")
                        if args:
                            for arg in args.children:
                                if arg.type == "string":
                                    topic = arg.text.decode().strip("\"'")
                                    relations.append(
                                        ExtractedRelation(
                                            source_name=module_name,
                                            source_type="module",
                                            target_name=topic,
                                            target_type="ConceptEntity",
                                            relation_type="publishes_to",
                                            file_path=file_path,
                                        )
                                    )
                                    break

                elif func.type == "identifier":
                    name = func.text.decode()
                    # httpx.get/post standalone
                    if name in ("get", "post", "put", "delete") and any(
                        c.text.decode() == "httpx" for c in node.children if c.type == "identifier"
                    ):
                        args = node.child_by_field_name("arguments")
                        if args:
                            for arg in args.children:
                                if arg.type == "string":
                                    url = arg.text.decode().strip("\"'")
                                    hostname = urlparse(url).hostname or url
                                    relations.append(
                                        ExtractedRelation(
                                            source_name=module_name,
                                            source_type="module",
                                            target_name=hostname,
                                            target_type="ServiceEntity",
                                            relation_type="calls_service",
                                            file_path=file_path,
                                        )
                                    )
                                    break
        for child in node.children:
            walk(child)

    walk(root_node)
    return relations


def extract_external_calls_java(root_node, source: bytes, file_path: str) -> list[ExtractedRelation]:
    """扫描 Java AST，提取 HTTP 客户端调用和 MQ producer 调用。

    Args:
        root_node: tree-sitter AST 根节点。
        source: 源码字节流。
        file_path: 文件路径。

    Returns:
        ExtractedRelation 列表。
    """
    relations: list[ExtractedRelation] = []

    def walk(node) -> None:
        if node.type == "method_invocation":
            # Find object expression (e.g. restTemplate, kafkaTemplate)
            obj_expr = node.child_by_field_name("object")
            # Find method name
            name_node = node.child_by_field_name("name")
            if obj_expr and name_node:
                obj_text = obj_expr.text.decode().lower() if obj_expr.text else ""
                method = name_node.text.decode()

                # HTTP client: restTemplate.postForObject / webClient.get
                if "resttemplate" in obj_text or "webclient" in obj_text:
                    args = node.child_by_field_name("arguments")
                    if args:
                        for arg in args.children:
                            if arg.type == "string_literal":
                                url = arg.text.decode().strip("\"'")
                                hostname = urlparse(url).hostname or url
                                relations.append(
                                    ExtractedRelation(
                                        source_name=file_path,
                                        source_type="file",
                                        target_name=hostname,
                                        target_type="ServiceEntity",
                                        relation_type="calls_service",
                                        file_path=file_path,
                                    )
                                )
                                break

                # MQ producer: kafkaTemplate.send / rabbitTemplate.convertAndSend
                elif ("kafkatemplate" in obj_text and method == "send") or (
                    "rabbittemplate" in obj_text and method == "convertandsend"
                ):
                    args = node.child_by_field_name("arguments")
                    if args:
                        for arg in args.children:
                            if arg.type == "string_literal":
                                topic = arg.text.decode().strip("\"'")
                                relations.append(
                                    ExtractedRelation(
                                        source_name=file_path,
                                        source_type="file",
                                        target_name=topic,
                                        target_type="ConceptEntity",
                                        relation_type="publishes_to",
                                        file_path=file_path,
                                    )
                                )
                                break
        for child in node.children:
            walk(child)

    walk(root_node)
    return relations
