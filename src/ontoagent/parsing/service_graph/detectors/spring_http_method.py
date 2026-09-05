"""Protocol-neutral Spring MVC method detector for the supported Java source shapes."""

from __future__ import annotations

import re
from dataclasses import dataclass

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


@dataclass(frozen=True)
class _JavaMethod:
    name: str
    parameters: tuple[str, ...]
    return_type: str
    start: int
    end: int
    annotation_text: str
    body: str


@dataclass(frozen=True)
class _JavaClass:
    fqcn: str
    interfaces: tuple[str, ...]
    start: int
    body_start: int
    body_end: int
    annotation_text: str
    methods: tuple[_JavaMethod, ...]


class SpringHttpMethodDetector:
    """Extract Spring MVC provider methods and literal HTTP consumer calls."""

    metadata = DetectorMetadata(
        detector_id="spring-http-method",
        detector_version="1",
        supported_languages=frozenset({"java"}),
        capabilities=(DetectorCapability("spring-http-methods", "1"),),
    )

    _MAPPING = re.compile(
        r"@(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)[ \t]*(?:\((.*?)\))?", re.DOTALL
    )
    _CLASS = re.compile(
        r"(?P<annotations>(?:[ \t]*@[^\n]+\n)*)"
        r"(?:public\s+|protected\s+|private\s+)?(?:class|interface)\s+(?P<name>\w+)"
        r"(?:\s+implements\s+(?P<interfaces>[^\{]+))?\s*\{",
        re.DOTALL,
    )
    _METHOD = re.compile(
        r"(?P<annotations>(?:[ \t]*@[^\n]+\n)*)"
        r"(?:public\s+|protected\s+|private\s+)?(?:static\s+)?"
        r"(?P<return>[\w.$<>\[\]?]+)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*\{",
        re.DOTALL,
    )
    _REST = re.compile(r"\.(?P<kind>getForObject|postForObject|put|delete)\s*\((?P<args>[^;]*?)\)", re.DOTALL)
    _WEB = re.compile(r"\.(?P<kind>get|post|put|delete)\s*\(\s*\)\s*\.\s*uri\s*\((?P<args>[^;]*?)\)", re.DOTALL)

    def detect_methods(self, snapshot: RepositorySnapshot, context: MethodDetectionContext) -> MethodFacts:
        if (snapshot.repo_id, snapshot.source_revision) != (context.repo_id, context.source_revision):
            raise ValueError("method detection context must match repository snapshot")
        evidences: list[MethodEvidence] = []
        operations: list[ServiceOperation] = []
        implementations: list[ImplementationMethod] = []
        calls: list[ConsumerMethodCall] = []
        bindings: list[OperationBinding] = []
        unresolved: list[MethodUnresolved] = []
        interface_methods: dict[str, dict[tuple[str, tuple[str, ...]], _JavaMethod]] = {}
        parsed: list[tuple[str, _JavaClass]] = []
        sources: dict[str, str] = {}
        for path in sorted(snapshot.root_path.rglob("*.java")):
            relative = path.relative_to(snapshot.root_path).as_posix()
            text = path.read_text(encoding="utf-8")
            sources[relative] = text
            for java_class in self._classes(text):
                parsed.append((relative, java_class))
                if java_class.interfaces == ("<declaration>",):
                    interface_methods[java_class.fqcn] = {
                        (method.name, method.parameters): method for method in java_class.methods
                    }
        for relative, java_class in parsed:
            if java_class.interfaces == ("<declaration>",):
                continue
            implementation_by_method: dict[_JavaMethod, ImplementationMethod] = {}
            for method in java_class.methods:
                evidence = self._evidence(
                    context, relative, method.start, method.end, "implementation_method", method.name
                )
                evidences.append(evidence)
                implementation = ImplementationMethod(
                    context.repo_id,
                    context.module_id,
                    context.service_id,
                    context.source_revision,
                    context.generation_id,
                    java_class.fqcn,
                    method.name,
                    self._signature(java_class.fqcn, method),
                    relative,
                    (evidence.id,),
                )
                implementations.append(implementation)
                implementation_by_method[method] = implementation
            class_paths = self._mapping_paths(java_class.annotation_text)
            if class_paths:
                contract_methods = self._contract_methods(java_class, interface_methods)
                for method, implementation in implementation_by_method.items():
                    for http_method, method_path in self._mappings(method.annotation_text):
                        endpoint = self._endpoint_reference(
                            http_method, _normalize_path(f"{class_paths[0]}/{method_path}")
                        )
                        contract = contract_methods.get((method.name, method.parameters), method)
                        evidence = self._evidence(
                            context, relative, method.start, method.start, "provider_method_mapping", endpoint
                        )
                        evidences.append(evidence)
                        operation = ServiceOperation(
                            context.repo_id,
                            context.module_id,
                            context.service_id,
                            context.source_revision,
                            context.generation_id,
                            "provider",
                            endpoint,
                            method.name,
                            self._signature(self._contract_fqcn(java_class), contract),
                            (evidence.id,),
                        )
                        operations.append(operation)
                        binding = OperationBinding(
                            context.repo_id,
                            context.module_id,
                            context.service_id,
                            context.source_revision,
                            context.generation_id,
                            endpoint,
                            operation.id,
                            implementation.id,
                            (evidence.id,),
                        )
                        bindings.append(binding)
            for method, implementation in implementation_by_method.items():
                self._consumer_calls(context, relative, method, implementation, evidences, calls, unresolved)
        for relative, text in sources.items():
            method_lines = tuple(
                (method.start, method.end)
                for path, java_class in parsed
                if path == relative
                for method in java_class.methods
            )
            self._missing_enclosing_calls(context, relative, text, method_lines, evidences, unresolved)
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

    @staticmethod
    def _coalesce_unresolved(unresolved: list[MethodUnresolved]) -> tuple[MethodUnresolved, ...]:
        grouped: dict[tuple[str, str, str, str, str, str, str], list[str]] = {}
        for item in unresolved:
            key = (
                item.repo_id,
                item.module_id,
                item.service_id,
                item.source_revision,
                item.generation_id,
                item.reason_code,
                item.subject,
            )
            grouped.setdefault(key, []).extend(item.evidence_ids)
        return tuple(
            MethodUnresolved(*key, tuple(sorted(set(evidence_ids)))) for key, evidence_ids in sorted(grouped.items())
        )

    def _missing_enclosing_calls(
        self,
        context: MethodDetectionContext,
        path: str,
        text: str,
        method_lines: tuple[tuple[int, int], ...],
        evidences: list[MethodEvidence],
        unresolved: list[MethodUnresolved],
    ) -> None:
        for pattern in (self._REST, self._WEB):
            for match in pattern.finditer(text):
                if not match.group("args").strip():
                    continue
                line = text.count("\n", 0, match.start()) + 1
                if any(start <= line <= end for start, end in method_lines):
                    continue
                subject = match.group("args").split(",", 1)[0].strip()
                evidence = self._evidence(context, path, line, line, "consumer_method_call", subject)
                evidences.append(evidence)
                unresolved.append(
                    MethodUnresolved(
                        context.repo_id,
                        context.module_id,
                        context.service_id,
                        context.source_revision,
                        context.generation_id,
                        "MISSING_IMPLEMENTATION",
                        subject,
                        (evidence.id,),
                    )
                )

    def _consumer_calls(
        self,
        context: MethodDetectionContext,
        path: str,
        method: _JavaMethod,
        implementation: ImplementationMethod,
        evidences: list[MethodEvidence],
        calls: list[ConsumerMethodCall],
        unresolved: list[MethodUnresolved],
    ) -> None:
        patterns = (
            (self._REST, {"getForObject": "GET", "postForObject": "POST", "put": "PUT", "delete": "DELETE"}),
            (self._WEB, {"get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE"}),
        )
        for pattern, verbs in patterns:
            for match in pattern.finditer(method.body):
                args = match.group("args").strip()
                first = args.split(",", 1)[0].strip()
                if not first:
                    continue
                line = method.start + method.body.count("\n", 0, match.start())
                evidence = self._evidence(context, path, line, line, "consumer_method_call", first)
                evidences.append(evidence)
                literal = re.fullmatch(r'"([^"\\]+)"', first)
                if literal is None:
                    unresolved.append(
                        MethodUnresolved(
                            context.repo_id,
                            context.module_id,
                            context.service_id,
                            context.source_revision,
                            context.generation_id,
                            "DYNAMIC_TARGET",
                            first,
                            (evidence.id,),
                        )
                    )
                    continue
                path_part = re.sub(r"^https?://[^/]+", "", literal.group(1)) or "/"
                reference = self._endpoint_reference(verbs[match.group("kind")], _normalize_path(path_part))
                calls.append(
                    ConsumerMethodCall(
                        context.repo_id,
                        context.module_id,
                        context.service_id,
                        context.source_revision,
                        context.generation_id,
                        implementation.id,
                        reference,
                        "operation",
                        (evidence.id,),
                    )
                )

    def _classes(self, text: str) -> tuple[_JavaClass, ...]:
        package = re.search(r"\bpackage\s+([\w.]+)\s*;", text)
        package_name = package.group(1) if package else ""
        result: list[_JavaClass] = []
        for match in self._CLASS.finditer(text):
            opening = text.find("{", match.start(), match.end())
            closing = self._matching_brace(text, opening)
            if closing < 0:
                continue
            body = text[opening + 1 : closing]
            is_interface = "interface" in match.group(0).split("{")[0]
            interfaces = (
                ("<declaration>",)
                if is_interface
                else tuple(
                    item.strip().split(".")[-1] for item in (match.group("interfaces") or "").split(",") if item.strip()
                )
            )
            methods = tuple(self._methods(body, opening + 1, text))
            result.append(
                _JavaClass(
                    f"{package_name}.{match.group('name')}" if package_name else match.group("name"),
                    interfaces,
                    text.count("\n", 0, match.start()) + 1,
                    opening + 1,
                    closing,
                    match.group("annotations"),
                    methods,
                )
            )
        return tuple(result)

    def _methods(self, body: str, offset: int, text: str) -> list[_JavaMethod]:
        result: list[_JavaMethod] = []
        for match in self._METHOD.finditer(body):
            opening = offset + match.end() - 1
            closing = self._matching_brace(text, opening)
            if closing < 0:
                continue
            params = tuple(self._parameter_type(item) for item in match.group("params").split(",") if item.strip())
            line_start = body.rfind("\n", 0, match.start()) + 1
            inline_annotation = body[line_start : match.start()]
            result.append(
                _JavaMethod(
                    match.group("name"),
                    params,
                    self._type(match.group("return")),
                    text.count("\n", 0, offset + match.start()) + 1,
                    text.count("\n", 0, closing) + 1,
                    inline_annotation + match.group("annotations"),
                    text[opening + 1 : closing],
                )
            )
        return result

    def _contract_methods(
        self, java_class: _JavaClass, interfaces: dict[str, dict[tuple[str, tuple[str, ...]], _JavaMethod]]
    ) -> dict[tuple[str, tuple[str, ...]], _JavaMethod]:
        for interface in java_class.interfaces:
            matching = next((items for fqcn, items in interfaces.items() if fqcn.endswith(f".{interface}")), None)
            if matching is not None:
                return matching
        return {}

    @staticmethod
    def _contract_fqcn(java_class: _JavaClass) -> str:
        return next((f"{java_class.fqcn.rsplit('.', 1)[0]}.{item}" for item in java_class.interfaces), java_class.fqcn)

    def _mappings(self, annotation_text: str) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for match in self._MAPPING.finditer(annotation_text):
            kind, args = match.group(1), match.group(2) or ""
            paths = self._paths(args) or ["/"]
            methods = (
                re.findall(r"RequestMethod\.(GET|POST|PUT|DELETE)", args)
                if kind == "RequestMapping"
                else [kind.removesuffix("Mapping").upper()]
            )
            result.extend((method, path) for method in methods for path in paths)
        return result

    def _mapping_paths(self, annotation_text: str) -> list[str]:
        result: list[str] = []
        for match in self._MAPPING.finditer(annotation_text):
            result.extend(self._paths(match.group(2) or ""))
        return result

    @staticmethod
    def _paths(args: str) -> list[str]:
        values = re.search(r"(?:path|value)\s*=\s*(\{[^}]*\}|\"[^\"]*\")", args)
        if values:
            value = values.group(1)
            return re.findall(r'"([^" ]*)"', value) if value.startswith("{") else [value[1:-1]]
        bare = re.match(r"\s*(\"[^\"]*\")", args)
        return [bare.group(1)[1:-1]] if bare else []

    @staticmethod
    def _type(value: str) -> str:
        return {"String": "java.lang.String", "Object": "java.lang.Object", "long": "long", "void": "void"}.get(
            value.strip(), value.strip()
        )

    def _parameter_type(self, value: str) -> str:
        return self._type(value.strip().split()[0])

    @staticmethod
    def _signature(fqcn: str, method: _JavaMethod) -> str:
        package = fqcn.rsplit(".", 1)[0] if "." in fqcn else ""

        def qualified(type_name: str) -> str:
            if "." in type_name or type_name in {"long", "void"}:
                return type_name
            return f"{package}.{type_name}" if package else type_name

        return f"{fqcn}#{method.name}({','.join(method.parameters)}):{qualified(method.return_type)}"

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
    def _matching_brace(text: str, opening: int) -> int:
        depth = 0
        for index in range(opening, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return index
        return -1
