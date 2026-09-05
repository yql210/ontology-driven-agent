"""OpenFeign Java method detector for literal Spring MVC client contracts."""

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
    ServiceOperation,
)
from ontoagent.parsing.service_graph.models import RepositorySnapshot, _normalize_path


@dataclass(frozen=True)
class _Method:
    name: str
    parameters: tuple[str, ...]
    return_type: str
    start: int
    end: int
    annotations: str
    body: str


@dataclass(frozen=True)
class _Type:
    fqcn: str
    is_interface: bool
    annotations: str
    methods: tuple[_Method, ...]
    start: int
    end: int
    body: str


@dataclass(frozen=True)
class _FeignContract:
    fqcn: str
    metadata: str | None
    methods: tuple[_Method, ...]
    base_path: str | None
    dynamic: bool


class FeignMethodDetector:
    """Extract literal OpenFeign declarations and calls from enclosing Java methods."""

    metadata = DetectorMetadata(
        detector_id="feign-method",
        detector_version="1",
        supported_languages=frozenset({"java"}),
        capabilities=(DetectorCapability("feign-methods", "1"),),
    )

    _TYPE = re.compile(
        r"(?P<annotations>(?:\s*@[\w.]+(?:\s*\([^)]*\))?\s*)*)"
        r"(?P<kind>class|interface)\s+(?P<name>\w+)[^{]*\{",
        re.DOTALL,
    )
    _METHOD = re.compile(
        r"(?P<annotations>(?:\s*@[\w.]+(?:\s*\([^)]*\))?\s*)*)"
        r"(?:public\s+|protected\s+|private\s+)?(?:static\s+)?(?:default\s+)?"
        r"(?P<return>[\w.$<>\[\]?]+)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*(?P<tail>\{|;)",
        re.DOTALL,
    )
    _FEIGN = re.compile(r"@(?:[\w.]+\.)?FeignClient\s*\((?P<args>[^)]*)\)", re.DOTALL)
    _MAPPING = re.compile(
        r"@(?:[\w.]+\.)?(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)\s*(?:\((?P<args>[^)]*)\))?",
        re.DOTALL,
    )

    def detect_methods(self, snapshot: RepositorySnapshot, context: MethodDetectionContext) -> MethodFacts:
        if (snapshot.repo_id, snapshot.source_revision) != (context.repo_id, context.source_revision):
            raise ValueError("method detection context must match repository snapshot")
        evidences: list[MethodEvidence] = []
        operations: list[ServiceOperation] = []
        implementations: list[ImplementationMethod] = []
        calls: list[ConsumerMethodCall] = []
        unresolved: list[MethodUnresolved] = []
        parsed: list[tuple[str, _Type]] = []
        for path in sorted(snapshot.root_path.rglob("*.java")):
            relative = path.relative_to(snapshot.root_path).as_posix()
            parsed.extend((relative, item) for item in self._types(path.read_text(encoding="utf-8")))
        contracts = self._contracts(parsed)
        for relative, java_type in parsed:
            if java_type.is_interface:
                contract = contracts.get(java_type.fqcn)
                if contract is not None:
                    self._declarations(context, relative, contract, evidences, operations, unresolved)
                continue
            fields = self._proxy_fields(java_type.body, contracts)
            if not fields:
                continue
            self._calls(context, relative, java_type, fields, contracts, evidences, implementations, calls, unresolved)
        return MethodFacts(
            self.metadata.detector_id,
            self.metadata.detector_version,
            context.repo_id,
            context.source_revision,
            context.generation_id,
            tuple(operations),
            tuple(implementations),
            tuple(calls),
            (),
            tuple(evidences),
            self._coalesce(unresolved),
        )

    def _contracts(self, parsed: list[tuple[str, _Type]]) -> dict[str, _FeignContract]:
        result: dict[str, _FeignContract] = {}
        for _, java_type in parsed:
            if not java_type.is_interface:
                continue
            match = self._FEIGN.search(java_type.annotations)
            if match is None:
                continue
            values = {key: value for key, value in self._annotation_values(match.group("args"))}
            metadata_values = [values.get(key) for key in ("name", "value", "contextId", "url", "path")]
            class_paths = self._mapping_paths(java_type.annotations)
            dynamic = any(value is not None and ("${" in value or "#{" in value) for value in metadata_values) or (
                bool(class_paths) and any("${" in value or "#{" in value for value in class_paths)
            )
            name = values.get("name") or values.get("value") or ""
            metadata = None
            base_path = None
            if name and not dynamic:
                feign_path = _normalize_path(values.get("path", "/"))
                if len(class_paths) <= 1:
                    base_path = _normalize_path(f"{feign_path}/{class_paths[0] if class_paths else '/'}")
                metadata = "|".join(
                    (
                        f"name={name}",
                        f"contextId={values.get('contextId', '')}",
                        f"url={values.get('url', '')}",
                        f"path={feign_path}",
                    )
                )
            result[java_type.fqcn] = _FeignContract(
                java_type.fqcn,
                metadata,
                java_type.methods,
                base_path,
                dynamic or base_path is None,
            )
        return result

    def _declarations(
        self,
        context: MethodDetectionContext,
        path: str,
        contract: _FeignContract,
        evidences: list[MethodEvidence],
        operations: list[ServiceOperation],
        unresolved: list[MethodUnresolved],
    ) -> None:
        if contract.dynamic or contract.metadata is None:
            evidence = self._evidence(context, path, 1, 1, "feign_client", contract.fqcn)
            evidences.append(evidence)
            unresolved.append(self._unresolved(context, "DYNAMIC_TARGET", contract.fqcn, evidence.id))
            return
        for method in contract.methods:
            mappings = self._mappings(method.annotations)
            evidence = self._evidence(context, path, method.start, method.end, "feign_method_mapping", method.name)
            evidences.append(evidence)
            if len(mappings) != 1:
                unresolved.append(
                    self._unresolved(context, "AMBIGUOUS_TARGET", f"{contract.fqcn}#{method.name}", evidence.id)
                )
                continue
            verb, method_path = mappings[0]
            endpoint = _normalize_path(f"{contract.base_path}/{method_path}")
            operations.append(
                ServiceOperation(
                    context.repo_id,
                    context.module_id,
                    context.service_id,
                    context.source_revision,
                    context.generation_id,
                    "consumer",
                    f"feign-client:{contract.metadata}|http={verb}|endpoint={endpoint}",
                    method.name,
                    self._signature(contract.fqcn, method),
                    (evidence.id,),
                )
            )

    def _calls(
        self,
        context: MethodDetectionContext,
        path: str,
        java_type: _Type,
        fields: dict[str, _FeignContract],
        contracts: dict[str, _FeignContract],
        evidences: list[MethodEvidence],
        implementations: list[ImplementationMethod],
        calls: list[ConsumerMethodCall],
        unresolved: list[MethodUnresolved],
    ) -> None:
        for method in java_type.methods:
            matches = list(self._proxy_calls(method.body, fields))
            if not matches:
                continue
            implementation = self._implementation(context, path, java_type.fqcn, method, evidences)
            implementations.append(implementation)
            for field, method_name, arguments, offset in matches:
                contract = fields[field]
                line = method.start + method.body.count("\n", 0, offset)
                evidence = self._evidence(context, path, line, line, "feign_proxy_call", f"{field}.{method_name}")
                evidences.append(evidence)
                target = self._target(contract, method_name, arguments)
                if target is None:
                    reason = "DYNAMIC_TARGET" if contract.dynamic else "AMBIGUOUS_TARGET"
                    unresolved.append(self._unresolved(context, reason, f"{field}.{method_name}", evidence.id))
                    continue
                calls.append(
                    ConsumerMethodCall(
                        context.repo_id,
                        context.module_id,
                        context.service_id,
                        context.source_revision,
                        context.generation_id,
                        implementation.id,
                        target,
                        "operation",
                        (evidence.id,),
                    )
                )
        occupied = tuple((method.start, method.end) for method in java_type.methods)
        for field, method_name, _, offset in self._proxy_calls(java_type.body, fields):
            line = java_type.start + java_type.body.count("\n", 0, offset)
            if any(start <= line <= end for start, end in occupied):
                continue
            evidence = self._evidence(context, path, line, line, "feign_proxy_call", f"{field}.{method_name}")
            evidences.append(evidence)
            unresolved.append(
                self._unresolved(context, "MISSING_IMPLEMENTATION", f"{field}.{method_name}", evidence.id)
            )

    def _target(self, contract: _FeignContract, name: str, arguments: str) -> str | None:
        if contract.dynamic:
            return None
        candidates = [
            item
            for item in contract.methods
            if item.name == name and len(item.parameters) == self._argument_count(arguments)
        ]
        if len(candidates) != 1:
            return None
        mappings = self._mappings(candidates[0].annotations)
        if len(mappings) != 1 or contract.metadata is None:
            return None
        verb, method_path = mappings[0]
        return f"feign-http:{verb}:{_normalize_path(f'{contract.base_path}/{method_path}')}"

    def _types(self, text: str) -> tuple[_Type, ...]:
        package = re.search(r"\bpackage\s+([\w.]+)\s*;", text)
        package_name = package.group(1) if package else ""
        result: list[_Type] = []
        for match in self._TYPE.finditer(text):
            opening = text.find("{", match.start(), match.end())
            closing = self._matching_brace(text, opening)
            if closing < 0:
                continue
            body = text[opening + 1 : closing]
            fqcn = f"{package_name}.{match.group('name')}" if package_name else match.group("name")
            result.append(
                _Type(
                    fqcn,
                    match.group("kind") == "interface",
                    match.group("annotations"),
                    tuple(self._methods(body, opening + 1, text)),
                    text.count("\n", 0, match.start()) + 1,
                    text.count("\n", 0, closing) + 1,
                    body,
                )
            )
        return tuple(result)

    def _methods(self, body: str, offset: int, text: str) -> list[_Method]:
        result: list[_Method] = []
        for match in self._METHOD.finditer(body):
            params = tuple(self._parameter_type(item) for item in match.group("params").split(",") if item.strip())
            start = text.count("\n", 0, offset + match.start()) + 1
            if match.group("tail") == ";":
                end, method_body = start, ""
            else:
                opening = offset + match.end() - 1
                closing = self._matching_brace(text, opening)
                if closing < 0:
                    continue
                end, method_body = text.count("\n", 0, closing) + 1, text[opening + 1 : closing]
            result.append(
                _Method(
                    match.group("name"),
                    params,
                    self._type(match.group("return")),
                    start,
                    end,
                    match.group("annotations"),
                    method_body,
                )
            )
        return result

    @staticmethod
    def _annotation_values(args: str) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for key, value in re.findall(r"\b(name|value|contextId|url|path)\s*=\s*\"([^\"]*)\"", args):
            result.append((key, value))
        bare = re.fullmatch(r"\s*\"([^\"]*)\"\s*", args)
        if bare is not None:
            result.append(("value", bare.group(1)))
        return result

    def _mappings(self, annotations: str) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for match in self._MAPPING.finditer(annotations):
            kind, args = match.group(1), match.group("args") or ""
            paths = self._paths(args) or ["/"]
            verbs = (
                re.findall(r"RequestMethod\.(GET|POST|PUT|DELETE)", args)
                if kind == "RequestMapping"
                else [kind.removesuffix("Mapping").upper()]
            )
            result.extend((verb, path) for verb in verbs for path in paths)
        return result

    def _mapping_paths(self, annotations: str) -> list[str]:
        paths: list[str] = []
        for match in self._MAPPING.finditer(annotations):
            paths.extend(self._paths(match.group("args") or "") or ["/"])
        return paths

    @staticmethod
    def _paths(args: str) -> list[str]:
        values = re.search(r"(?:path|value)\s*=\s*(\{[^}]*\}|\"[^\"]*\")", args)
        if values:
            value = values.group(1)
            return re.findall(r'"([^" ]*)"', value) if value.startswith("{") else [value[1:-1]]
        bare = re.match(r"\s*(\"[^\"]*\")", args)
        return [bare.group(1)[1:-1]] if bare else []

    @staticmethod
    def _proxy_fields(body: str, contracts: dict[str, _FeignContract]) -> dict[str, _FeignContract]:
        result: dict[str, _FeignContract] = {}
        for type_name, field in re.findall(r"\b([\w.]+)\s+(\w+)\s*;", body):
            contract = next((item for fqcn, item in contracts.items() if fqcn.endswith(f".{type_name}")), None)
            if contract is not None:
                result[field] = contract
        return result

    @staticmethod
    def _proxy_calls(body: str, fields: dict[str, _FeignContract]):
        for match in re.finditer(r"\b(?P<field>\w+)\.(?P<method>\w+)\s*\((?P<arguments>[^)]*)\)", body):
            field = match.group("field")
            if field in fields:
                yield field, match.group("method"), match.group("arguments"), match.start()

    def _implementation(
        self, context: MethodDetectionContext, path: str, fqcn: str, method: _Method, evidences: list[MethodEvidence]
    ) -> ImplementationMethod:
        evidence = self._evidence(context, path, method.start, method.end, "implementation_method", method.name)
        evidences.append(evidence)
        return ImplementationMethod(
            context.repo_id,
            context.module_id,
            context.service_id,
            context.source_revision,
            context.generation_id,
            fqcn,
            method.name,
            self._signature(fqcn, method),
            path,
            (evidence.id,),
        )

    @staticmethod
    def _argument_count(arguments: str) -> int:
        return 0 if not arguments.strip() else len(arguments.split(","))

    @staticmethod
    def _type(value: str) -> str:
        return {"String": "java.lang.String", "Object": "java.lang.Object", "long": "long", "void": "void"}.get(
            value.strip(), value.strip()
        )

    def _parameter_type(self, value: str) -> str:
        return self._type(value.strip().split()[0])

    @staticmethod
    def _signature(fqcn: str, method: _Method) -> str:
        package = fqcn.rsplit(".", 1)[0] if "." in fqcn else ""

        def qualified(type_name: str) -> str:
            return (
                type_name
                if "." in type_name or type_name in {"long", "void"}
                else f"{package}.{type_name}"
                if package
                else type_name
            )

        return f"{fqcn}#{method.name}({','.join(method.parameters)}):{qualified(method.return_type)}"

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
    def _coalesce(items: list[MethodUnresolved]) -> tuple[MethodUnresolved, ...]:
        grouped: dict[tuple[str, str], list[str]] = {}
        examples: dict[tuple[str, str], MethodUnresolved] = {}
        for item in items:
            key = (item.reason_code, item.subject)
            grouped.setdefault(key, []).extend(item.evidence_ids)
            examples[key] = item
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
            for key, item in sorted(examples.items())
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
