"""gRPC Java method detector for generated service and stub source shapes."""

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
from ontoagent.parsing.service_graph.models import RepositorySnapshot


@dataclass(frozen=True)
class _Method:
    name: str
    parameters: tuple[tuple[str, str], ...]
    return_type: str
    start: int
    end: int
    body: str
    annotations: str


@dataclass(frozen=True)
class _Class:
    name: str
    fqcn: str
    extends: str | None
    body: str
    methods: tuple[_Method, ...]


class GrpcMethodDetector:
    """Extract gRPC provider RPC bindings and statically resolvable generated-stub calls."""

    metadata = DetectorMetadata(
        detector_id="grpc-method",
        detector_version="1",
        supported_languages=frozenset({"java"}),
        capabilities=(DetectorCapability("grpc-methods", "1"),),
    )

    _CLASS = re.compile(
        r"(?P<annotations>(?:\s*@[\w.]+(?:\s*\([^)]*\))?\s*)*)"
        r"(?:public\s+|protected\s+|private\s+|abstract\s+|final\s+|static\s+)*"
        r"(?:class|interface)\s+(?P<name>\w+)(?:\s+extends\s+(?P<extends>[\w.$<>]+))?[^\{]*\{",
        re.DOTALL,
    )
    _METHOD = re.compile(
        r"(?P<annotations>(?:\s*@[\w.]+(?:\s*\([^)]*\))?\s*)*)"
        r"(?:public\s+|protected\s+|private\s+|static\s+|final\s+|abstract\s+|synchronized\s+)*"
        r"(?P<return>[\w.$<>\[\]?]+)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*"
        r"(?P<terminator>\{|;)",
        re.DOTALL,
    )
    _CALL = re.compile(r"\b(?P<receiver>[A-Za-z_]\w*)\s*\.\s*(?P<method>[A-Za-z_]\w*)\s*\((?P<args>[^()]*)\)")
    _VARIABLE = re.compile(
        r"(?:\b(?:final|private|protected|public|static)\s+)*(?P<type>[\w.$<>]+)\s+(?P<name>[A-Za-z_]\w*)\s*(?:=|;)"
    )
    _FACTORY = re.compile(r"(?P<service>[\w.]+Grpc)\s*\.\s*new(?:Blocking|Future)?Stub\s*\((?P<channel>[^)]*)\)")

    def detect_methods(self, snapshot: RepositorySnapshot, context: MethodDetectionContext) -> MethodFacts:
        if (snapshot.repo_id, snapshot.source_revision) != (context.repo_id, context.source_revision):
            raise ValueError("method detection context must match repository snapshot")
        parsed: list[tuple[str, _Class, dict[str, str], str]] = []
        for path in sorted(snapshot.root_path.rglob("*.java")):
            relative = path.relative_to(snapshot.root_path).as_posix()
            text = path.read_text(encoding="utf-8")
            imports = self._imports(text)
            parsed.extend((relative, item, imports, text) for item in self._classes(text, imports))

        operations: list[ServiceOperation] = []
        implementations: list[ImplementationMethod] = []
        calls: list[ConsumerMethodCall] = []
        bindings: list[OperationBinding] = []
        evidences: list[MethodEvidence] = []
        unresolved: list[MethodUnresolved] = []
        stub_returns = self._stub_returns(parsed)

        for path, cls, imports, _ in parsed:
            service = self._provider_service(cls.extends, cls.fqcn, imports)
            if service is None:
                continue
            for method in cls.methods:
                if "@Override" not in method.annotations or len(method.parameters) < 2:
                    continue
                request = self._qualified(method.parameters[0][0], cls.fqcn, imports)
                response = self._observer_response(method.parameters[1][0], cls.fqcn, imports)
                if response is None:
                    self._unresolved(
                        context, path, method.start, "UNSUPPORTED_TARGET_SHAPE", method.name, evidences, unresolved
                    )
                    continue
                signature = self._signature(service, method.name, request, response)
                evidence = self._evidence(context, path, method.start, method.end, "grpc_provider_rpc", signature)
                implementation = self._implementation(context, cls.fqcn, method, path, evidence.id)
                operation = ServiceOperation(
                    context.repo_id,
                    context.module_id,
                    context.service_id,
                    context.source_revision,
                    context.generation_id,
                    "provider",
                    service,
                    self._rpc_name(method.name),
                    signature,
                    (evidence.id,),
                )
                operations.append(operation)
                implementations.append(implementation)
                bindings.append(
                    OperationBinding(
                        context.repo_id,
                        context.module_id,
                        context.service_id,
                        context.source_revision,
                        context.generation_id,
                        f"grpc-provider:{signature}",
                        operation.id,
                        implementation.id,
                        (evidence.id,),
                    )
                )
                evidences.append(evidence)

        for path, cls, imports, _ in parsed:
            variables = self._variables(cls, imports)
            for method in cls.methods:
                for match in self._CALL.finditer(method.body):
                    receiver, rpc_name, raw_args = match.group("receiver", "method", "args")
                    stub_type = variables.get(receiver)
                    if stub_type is None or rpc_name.startswith("new"):
                        continue
                    service = self._stub_service(stub_type)
                    line = method.start + method.body.count("\n", 0, match.start())
                    if service is None:
                        if stub_type.rsplit(".", 1)[-1].endswith("Stub"):
                            self._unresolved(
                                context,
                                path,
                                line,
                                "UNSUPPORTED_TARGET_SHAPE",
                                f"{receiver}.{rpc_name}",
                                evidences,
                                unresolved,
                            )
                        continue
                    factories = self._factories_for(receiver, cls.body, service)
                    factory_services = {factory_service for factory_service, _ in factories}
                    if len(factory_services) > 1:
                        self._unresolved(
                            context, path, line, "AMBIGUOUS_TARGET", f"{receiver}.{rpc_name}", evidences, unresolved
                        )
                        continue
                    if not factories or service not in factory_services:
                        self._unresolved(
                            context,
                            path,
                            line,
                            "UNSUPPORTED_TARGET_SHAPE",
                            f"{receiver}.{rpc_name}",
                            evidences,
                            unresolved,
                        )
                        continue
                    factory = factories[0][1]
                    if not re.fullmatch(r"[A-Za-z_]\w*", factory.group("channel").strip()):
                        self._unresolved(
                            context, path, line, "DYNAMIC_TARGET", f"{receiver}.{rpc_name}", evidences, unresolved
                        )
                        continue
                    argument_types = self._argument_types(raw_args, method, cls.fqcn, imports)
                    response = self._response_type(stub_type, rpc_name, len(argument_types), stub_returns)
                    if len(argument_types) < 1 or response is None:
                        self._unresolved(
                            context,
                            path,
                            line,
                            "UNSUPPORTED_TARGET_SHAPE",
                            f"{receiver}.{rpc_name}",
                            evidences,
                            unresolved,
                        )
                        continue
                    signature = self._signature(service, rpc_name, argument_types[0], response)
                    evidence = self._evidence(context, path, line, line, "grpc_consumer_call", signature)
                    implementation = self._implementation(context, cls.fqcn, method, path, evidence.id)
                    implementations.append(implementation)
                    calls.append(
                        ConsumerMethodCall(
                            context.repo_id,
                            context.module_id,
                            context.service_id,
                            context.source_revision,
                            context.generation_id,
                            implementation.id,
                            f"grpc-operation:{signature}",
                            "operation",
                            (evidence.id,),
                        )
                    )
                    evidences.append(evidence)
            self._outside_method_calls(context, path, cls, variables, evidences, unresolved)

        return MethodFacts(
            self.metadata.detector_id,
            self.metadata.detector_version,
            context.repo_id,
            context.source_revision,
            context.generation_id,
            tuple(self._unique(operations)),
            tuple(self._unique(implementations)),
            tuple(self._unique(calls)),
            tuple(self._unique(bindings)),
            tuple(self._unique(evidences)),
            tuple(self._coalesce(unresolved)),
        )

    def _classes(self, text: str, imports: dict[str, str]) -> tuple[_Class, ...]:
        package = self._package(text)
        result: list[_Class] = []
        for match in self._CLASS.finditer(text):
            closing = self._brace(text, match.end() - 1)
            if closing < 0:
                continue
            name = match.group("name")
            fqcn = f"{package}.{name}" if package else name
            body = text[match.end() : closing]
            result.append(
                _Class(name, fqcn, match.group("extends"), body, tuple(self._methods(body, match.end(), text)))
            )
        return tuple(result)

    def _methods(self, body: str, offset: int, text: str) -> list[_Method]:
        result: list[_Method] = []
        for match in self._METHOD.finditer(body):
            opening = offset + match.end() - 1
            closing = self._brace(text, opening) if match.group("terminator") == "{" else opening
            if closing < 0:
                continue
            params = tuple(self._parameter(item) for item in match.group("params").split(",") if item.strip())
            result.append(
                _Method(
                    match.group("name"),
                    params,
                    match.group("return"),
                    text.count("\n", 0, offset + match.start()) + 1,
                    text.count("\n", 0, closing) + 1,
                    text[opening + 1 : closing] if opening < closing else "",
                    match.group("annotations"),
                )
            )
        return result

    def _variables(self, cls: _Class, imports: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for method in cls.methods:
            result.update(
                {name: self._qualified(type_name, cls.fqcn, imports) for type_name, name in method.parameters}
            )
        for match in self._VARIABLE.finditer(cls.body):
            result[match.group("name")] = self._qualified(match.group("type"), cls.fqcn, imports)
        return result

    def _stub_returns(self, parsed: list[tuple[str, _Class, dict[str, str], str]]) -> dict[tuple[str, str, int], str]:
        result: dict[tuple[str, str, int], str] = {}
        for _, cls, imports, _ in parsed:
            if not cls.name.endswith("Stub"):
                continue
            for method in cls.methods:
                if method.return_type == "void":
                    response = (
                        self._observer_response(method.parameters[-1][0], cls.fqcn, imports)
                        if method.parameters
                        else None
                    )
                else:
                    response = self._qualified(method.return_type, cls.fqcn, imports)
                if response:
                    result[(cls.name, method.name, len(method.parameters))] = response
        return result

    def _outside_method_calls(
        self,
        context: MethodDetectionContext,
        path: str,
        cls: _Class,
        variables: dict[str, str],
        evidences: list[MethodEvidence],
        unresolved: list[MethodUnresolved],
    ) -> None:
        outside = cls.body
        for method in cls.methods:
            outside = outside.replace(method.body, "", 1)
        for match in self._CALL.finditer(outside):
            receiver, rpc_name = match.group("receiver", "method")
            if self._stub_service(variables.get(receiver, "")) is None:
                continue
            line = outside.count("\n", 0, match.start()) + 1
            self._unresolved(
                context,
                path,
                line,
                "MISSING_IMPLEMENTATION",
                f"{receiver}.{rpc_name}",
                evidences,
                unresolved,
            )

    def _factories_for(self, receiver: str, body: str, service: str) -> tuple[tuple[str, re.Match[str]], ...]:
        result: list[tuple[str, re.Match[str]]] = []
        for match in self._FACTORY.finditer(body):
            if re.search(rf"(?:this\.)?{re.escape(receiver)}\s*=\s*{re.escape(match.group(0))}", body):
                result.append((self._qualified_service(match.group("service"), service), match))
        return tuple(result)

    @staticmethod
    def _provider_service(extends: str | None, current: str, imports: dict[str, str]) -> str | None:
        if extends is None or not extends.endswith("ImplBase") or "Grpc." not in extends:
            return None
        value = extends.rsplit(".", 1)[0]
        return GrpcMethodDetector._qualified(value, current, imports)

    @staticmethod
    def _stub_service(stub_type: str) -> str | None:
        if "." not in stub_type or not stub_type.rsplit(".", 1)[-1].endswith("Stub"):
            return None
        service = stub_type.rsplit(".", 1)[0]
        return service if service.endswith("Grpc") else None

    @staticmethod
    def _qualified_service(value: str, expected: str) -> str:
        return value if "." in value else f"{expected.rsplit('.', 1)[0]}.{value}"

    @staticmethod
    def _package(text: str) -> str:
        match = re.search(r"\bpackage\s+([\w.]+)\s*;", text)
        return match.group(1) if match else ""

    @staticmethod
    def _imports(text: str) -> dict[str, str]:
        return {item.rsplit(".", 1)[-1]: item for item in re.findall(r"\bimport\s+([\w.]+)\s*;", text)}

    @staticmethod
    def _parameter(value: str) -> tuple[str, str]:
        parts = value.strip().split()
        return (parts[-2], parts[-1]) if len(parts) >= 2 else ("", "")

    @staticmethod
    def _qualified(value: str, current: str, imports: dict[str, str]) -> str:
        raw = value.strip().replace("[]", "")
        if "." in raw:
            head, *tail = raw.split(".")
            if head in imports:
                return f"{imports[head]}.{'.'.join(tail)}"
            if head[:1].isupper() and "." in current:
                return f"{current.rsplit('.', 1)[0]}.{raw}"
            return raw
        if raw in {"void", "String", "Object", "long", "int", "boolean"}:
            return {"String": "java.lang.String", "Object": "java.lang.Object"}.get(raw, raw)
        return imports.get(raw, f"{current.rsplit('.', 1)[0]}.{raw}" if "." in current else raw)

    def _argument_types(self, args: str, method: _Method, current: str, imports: dict[str, str]) -> tuple[str, ...]:
        variables = {name: self._qualified(type_name, current, imports) for type_name, name in method.parameters}
        result: list[str] = []
        for arg in (item.strip() for item in args.split(",") if item.strip()):
            if arg not in variables:
                return ()
            result.append(variables[arg])
        return tuple(result)

    def _response_type(self, stub: str, method: str, argc: int, returns: dict[tuple[str, str, int], str]) -> str | None:
        return returns.get((stub.rsplit(".", 1)[-1], method, argc))

    @staticmethod
    def _observer_response(value: str, current: str, imports: dict[str, str]) -> str | None:
        match = re.fullmatch(r"(?:[\w.]+\.)?StreamObserver\s*<\s*([\w.]+)\s*>", value)
        return GrpcMethodDetector._qualified(match.group(1), current, imports) if match else None

    def _signature(self, service: str, method: str, request: str, response: str) -> str:
        return f"{service}#{self._rpc_name(method)}({request}):{response}"

    @staticmethod
    def _rpc_name(method: str) -> str:
        return method[:1].upper() + method[1:]

    def _implementation(
        self, context: MethodDetectionContext, fqcn: str, method: _Method, path: str, evidence_id: str
    ) -> ImplementationMethod:
        return ImplementationMethod(
            context.repo_id,
            context.module_id,
            context.service_id,
            context.source_revision,
            context.generation_id,
            fqcn,
            method.name,
            f"{fqcn}#{method.name}({','.join(item[0] for item in method.parameters)}):{method.return_type}",
            path,
            (evidence_id,),
        )

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

    def _unresolved(
        self,
        context: MethodDetectionContext,
        path: str,
        line: int,
        reason: str,
        subject: str,
        evidences: list[MethodEvidence],
        unresolved: list[MethodUnresolved],
    ) -> None:
        evidence = self._evidence(context, path, line, line, "grpc_unresolved", subject)
        evidences.append(evidence)
        unresolved.append(
            MethodUnresolved(
                context.repo_id,
                context.module_id,
                context.service_id,
                context.source_revision,
                context.generation_id,
                reason,
                subject,
                (evidence.id,),
            )
        )

    @staticmethod
    def _coalesce(items: list[MethodUnresolved]) -> tuple[MethodUnresolved, ...]:
        grouped: dict[tuple[str, str], set[str]] = {}
        representatives: dict[tuple[str, str], MethodUnresolved] = {}
        for item in items:
            key = (item.reason_code, item.subject)
            representatives.setdefault(key, item)
            grouped.setdefault(key, set()).update(item.evidence_ids)
        return tuple(
            MethodUnresolved(
                item.repo_id,
                item.module_id,
                item.service_id,
                item.source_revision,
                item.generation_id,
                item.reason_code,
                item.subject,
                tuple(sorted(grouped[key])),
            )
            for key, item in sorted(representatives.items())
        )

    @staticmethod
    def _unique(items: list[object]) -> list[object]:
        return list({item.id: item for item in items}.values())

    @staticmethod
    def _brace(text: str, opening: int) -> int:
        depth = 0
        for index in range(opening, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return index
        return -1
