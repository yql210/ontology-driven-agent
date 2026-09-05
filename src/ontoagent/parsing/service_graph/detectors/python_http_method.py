"""Protocol-neutral Python HTTP method detector for FastAPI, Flask, requests, and httpx."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from ontoagent.parsing.service_graph.detector_sdk import (
    DetectorCapability,
    DetectorMetadata,
    MethodDetectionContext,
)
from ontoagent.parsing.service_graph.methods import (
    ConsumerMethodCall,
    ImplementationMethod,
    MethodEvidence,
    MethodFacts,
    MethodUnresolved,
    OperationBinding,
    ServiceOperation,
)
from ontoagent.parsing.service_graph.models import RepositorySnapshot, _normalize_path

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class _PythonFunction:
    node: ast.FunctionDef | ast.AsyncFunctionDef
    qualified_name: str


class PythonHttpMethodDetector:
    """Extract Python HTTP providers and literal consumer calls at function granularity."""

    metadata = DetectorMetadata(
        detector_id="python-http-method",
        detector_version="1",
        supported_languages=frozenset({"python"}),
        capabilities=(DetectorCapability("python-http-methods", "1"),),
    )

    def detect_methods(self, snapshot: RepositorySnapshot, context: MethodDetectionContext) -> MethodFacts:
        if (snapshot.repo_id, snapshot.source_revision) != (context.repo_id, context.source_revision):
            raise ValueError("method detection context must match repository snapshot")
        evidences: list[MethodEvidence] = []
        operations: list[ServiceOperation] = []
        implementations: list[ImplementationMethod] = []
        calls: list[ConsumerMethodCall] = []
        bindings: list[OperationBinding] = []
        unresolved: list[MethodUnresolved] = []
        for source_path in sorted(snapshot.root_path.rglob("*.py")):
            relative = source_path.relative_to(snapshot.root_path).as_posix()
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=relative)
            clients, prefixes = self._framework_declarations(tree)
            functions = self._functions(tree)
            implementations_by_node: dict[int, ImplementationMethod] = {}
            for function in functions:
                implementation = self._implementation(context, relative, function, evidences)
                implementations.append(implementation)
                implementations_by_node[id(function.node)] = implementation
                self._providers(
                    context,
                    relative,
                    function,
                    implementation,
                    clients,
                    prefixes,
                    evidences,
                    operations,
                    bindings,
                    unresolved,
                )
                self._consumer_calls(context, relative, function, implementation, evidences, calls, unresolved)
            self._module_calls(context, relative, tree, evidences, unresolved)
        return MethodFacts(
            self.metadata.detector_id,
            self.metadata.detector_version,
            context.repo_id,
            context.source_revision,
            context.generation_id,
            tuple(operations),
            tuple(implementations),
            tuple(calls),
            tuple(bindings),
            tuple(evidences),
            self._coalesce_unresolved(unresolved),
        )

    def _implementation(
        self, context: MethodDetectionContext, path: str, function: _PythonFunction, evidences: list[MethodEvidence]
    ) -> ImplementationMethod:
        evidence = self._evidence(
            context,
            path,
            function.node.lineno,
            function.node.end_lineno or function.node.lineno,
            "implementation_method",
            function.qualified_name,
        )
        evidences.append(evidence)
        module = Path(path).with_suffix("").as_posix().replace("/", ".")
        signature = f"{module}#{function.qualified_name}()"
        return ImplementationMethod(
            context.repo_id,
            context.module_id,
            context.service_id,
            context.source_revision,
            context.generation_id,
            module,
            function.node.name,
            signature,
            path,
            (evidence.id,),
        )

    def _providers(
        self,
        context: MethodDetectionContext,
        path: str,
        function: _PythonFunction,
        implementation: ImplementationMethod,
        clients: set[str],
        prefixes: dict[str, str],
        evidences: list[MethodEvidence],
        operations: list[ServiceOperation],
        bindings: list[OperationBinding],
        unresolved: list[MethodUnresolved],
    ) -> None:
        for decorator in function.node.decorator_list:
            route = self._route(decorator, clients, prefixes)
            if route is None:
                continue
            methods, route_path, reason = route
            subject = ast.unparse(decorator)
            evidence = self._evidence(
                context,
                path,
                decorator.lineno,
                decorator.end_lineno or decorator.lineno,
                "provider_method_mapping",
                subject,
            )
            evidences.append(evidence)
            if reason is not None:
                unresolved.append(self._unresolved(context, reason, subject, evidence.id))
                continue
            assert methods is not None and route_path is not None
            for method in methods:
                reference = self._endpoint_reference(method, route_path)
                operation = ServiceOperation(
                    context.repo_id,
                    context.module_id,
                    context.service_id,
                    context.source_revision,
                    context.generation_id,
                    "provider",
                    reference,
                    function.node.name,
                    implementation.canonical_signature,
                    (evidence.id,),
                )
                operations.append(operation)
                bindings.append(
                    OperationBinding(
                        context.repo_id,
                        context.module_id,
                        context.service_id,
                        context.source_revision,
                        context.generation_id,
                        reference,
                        operation.id,
                        implementation.id,
                        (evidence.id,),
                    )
                )

    def _consumer_calls(
        self,
        context: MethodDetectionContext,
        path: str,
        function: _PythonFunction,
        implementation: ImplementationMethod,
        evidences: list[MethodEvidence],
        calls: list[ConsumerMethodCall],
        unresolved: list[MethodUnresolved],
    ) -> None:
        clients = self._httpx_client_names(function.node)
        for call in self._body_calls(function.node):
            detected = self._consumer_target(call, clients)
            if detected is None:
                continue
            method, url, reason = detected
            subject = ast.unparse(call)
            evidence = self._evidence(
                context, path, call.lineno, call.end_lineno or call.lineno, "consumer_method_call", subject
            )
            evidences.append(evidence)
            if reason is not None:
                unresolved.append(self._unresolved(context, reason, subject, evidence.id))
                continue
            assert method is not None and url is not None
            calls.append(
                ConsumerMethodCall(
                    context.repo_id,
                    context.module_id,
                    context.service_id,
                    context.source_revision,
                    context.generation_id,
                    implementation.id,
                    self._endpoint_reference(method, self._url_path(url)),
                    "operation",
                    (evidence.id,),
                )
            )

    def _module_calls(
        self,
        context: MethodDetectionContext,
        path: str,
        tree: ast.Module,
        evidences: list[MethodEvidence],
        unresolved: list[MethodUnresolved],
    ) -> None:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for call in ast.walk(node):
                detected = self._consumer_target(call, set()) if isinstance(call, ast.Call) else None
                if detected is None:
                    continue
                _, _, _ = detected
                subject = ast.unparse(call)
                evidence = self._evidence(
                    context, path, call.lineno, call.end_lineno or call.lineno, "consumer_method_call", subject
                )
                evidences.append(evidence)
                unresolved.append(self._unresolved(context, "MISSING_IMPLEMENTATION", subject, evidence.id))

    def _framework_declarations(self, tree: ast.Module) -> tuple[set[str], dict[str, str]]:
        clients: set[str] = set()
        prefixes: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else node.target
            if (
                not isinstance(target, ast.Name)
                or not isinstance(value, ast.Call)
                or not isinstance(value.func, ast.Name)
            ):
                continue
            if value.func.id in {"FastAPI", "Flask", "APIRouter"}:
                clients.add(target.id)
                prefix = self._keyword_literal(value, "prefix") if value.func.id == "APIRouter" else None
                prefixes[target.id] = prefix or "/"
        return clients, prefixes

    def _route(
        self, decorator: ast.expr, clients: set[str], prefixes: dict[str, str]
    ) -> tuple[tuple[str, ...] | None, str | None, str | None] | None:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            return None
        if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id not in clients:
            return None
        kind = decorator.func.attr.lower()
        path = self._literal(self._argument(decorator, 0, "path") or self._argument(decorator, 0, "rule"))
        if path is None:
            return None, None, "UNSUPPORTED_TARGET_SHAPE"
        if kind.upper() in _HTTP_METHODS:
            methods = (kind.upper(),)
        elif kind == "route":
            method_node = self._keyword(decorator, "methods")
            if method_node is None:
                return None, None, "UNSUPPORTED_TARGET_SHAPE"
            methods = self._method_list(method_node)
            if methods is None:
                return None, None, "UNSUPPORTED_TARGET_SHAPE"
        else:
            return None
        return methods, _normalize_path(f"{prefixes.get(decorator.func.value.id, '/')}/{path}"), None

    def _consumer_target(self, call: ast.Call, clients: set[str]) -> tuple[str | None, str | None, str | None] | None:
        if not isinstance(call.func, ast.Attribute) or call.func.attr.upper() not in _HTTP_METHODS | {"REQUEST"}:
            return None
        owner = call.func.value
        known = (
            isinstance(owner, ast.Name) and owner.id in {"requests", "httpx", *clients}
        ) or self._httpx_constructor(owner)
        if not known:
            if isinstance(owner, ast.Name) and call.func.attr.upper() in _HTTP_METHODS | {"REQUEST"}:
                return None, None, "UNSUPPORTED_TARGET_SHAPE"
            return None
        if call.func.attr.lower() == "request":
            method = self._literal(self._argument(call, 0, "method"))
            url = self._literal(self._argument(call, 1, "url"))
            if method is None or method.upper() not in _HTTP_METHODS:
                return None, None, "UNSUPPORTED_TARGET_SHAPE"
            return method.upper(), url, "DYNAMIC_TARGET" if url is None else None
        url = self._literal(self._argument(call, 0, "url"))
        return call.func.attr.upper(), url, "DYNAMIC_TARGET" if url is None else None

    @staticmethod
    def _httpx_constructor(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "httpx"
            and node.func.attr in {"Client", "AsyncClient"}
        )

    def _httpx_client_names(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        names: set[str] = set()
        for node in self._body_nodes(function):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else node.target
                if isinstance(target, ast.Name) and node.value is not None and self._httpx_constructor(node.value):
                    names.add(target.id)
            if isinstance(node, ast.AsyncWith):
                for item in node.items:
                    if isinstance(item.optional_vars, ast.Name) and self._httpx_constructor(item.context_expr):
                        names.add(item.optional_vars.id)
        return names

    @staticmethod
    def _body_nodes(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
        result: list[ast.AST] = []
        for node in ast.walk(function):
            if node is not function and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
            ):
                continue
            result.append(node)
        return result

    def _body_calls(self, function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
        return [node for node in self._body_nodes(function) if isinstance(node, ast.Call)]

    def _functions(self, tree: ast.Module) -> tuple[_PythonFunction, ...]:
        result: list[_PythonFunction] = []

        def visit(nodes: list[ast.stmt], scope: tuple[str, ...] = ()) -> None:
            for node in nodes:
                if isinstance(node, ast.ClassDef):
                    visit(node.body, (*scope, node.name))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result.append(_PythonFunction(node, ".".join((*scope, node.name))))

        visit(tree.body)
        return tuple(result)

    @staticmethod
    def _argument(call: ast.Call, index: int, keyword: str) -> ast.expr | None:
        if len(call.args) > index:
            return call.args[index]
        return PythonHttpMethodDetector._keyword(call, keyword)

    @staticmethod
    def _keyword(call: ast.Call, name: str) -> ast.expr | None:
        return next((item.value for item in call.keywords if item.arg == name), None)

    def _keyword_literal(self, call: ast.Call, name: str) -> str | None:
        return self._literal(self._keyword(call, name))

    @staticmethod
    def _literal(node: ast.expr | None) -> str | None:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    def _method_list(self, node: ast.expr) -> tuple[str, ...] | None:
        if not isinstance(node, (ast.List, ast.Tuple)):
            return None
        values = tuple(self._literal(item) for item in node.elts)
        if not values or any(value is None or value.upper() not in _HTTP_METHODS for value in values):
            return None
        return tuple(value.upper() for value in values if value is not None)

    @staticmethod
    def _url_path(url: str) -> str:
        return _normalize_path(re.sub(r"^https?://[^/]+", "", url) or "/")

    @staticmethod
    def _endpoint_reference(method: str, path: str) -> str:
        return f"spring-http:{method}:{path}"

    def _evidence(
        self, context: MethodDetectionContext, path: str, start: int, end: int, kind: str, subject: str
    ) -> MethodEvidence:
        return MethodEvidence(
            context.repo_id,
            context.module_id,
            context.service_id,
            context.source_revision,
            context.generation_id,
            path,
            start,
            end,
            self.metadata.detector_id,
            self.metadata.detector_version,
            kind,
            subject,
            1.0,
        )

    @staticmethod
    def _unresolved(context: MethodDetectionContext, reason: str, subject: str, evidence_id: str) -> MethodUnresolved:
        return MethodUnresolved(
            context.repo_id,
            context.module_id,
            context.service_id,
            context.source_revision,
            context.generation_id,
            reason,
            subject,
            (evidence_id,),
        )

    @staticmethod
    def _coalesce_unresolved(unresolved: list[MethodUnresolved]) -> tuple[MethodUnresolved, ...]:
        grouped: dict[tuple[str, str], list[str]] = {}
        example: dict[tuple[str, str], MethodUnresolved] = {}
        for item in unresolved:
            key = item.reason_code, item.subject
            grouped.setdefault(key, []).extend(item.evidence_ids)
            example[key] = item
        return tuple(
            MethodUnresolved(
                item.repo_id,
                item.module_id,
                item.service_id,
                item.source_revision,
                item.generation_id,
                item.reason_code,
                item.subject,
                tuple(sorted(set(grouped[key]))),
            )
            for key, item in sorted(example.items())
        )
