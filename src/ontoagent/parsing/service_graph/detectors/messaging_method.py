"""Method-level Kafka and RabbitMQ detector for the supported Java source shapes."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ontoagent.parsing.service_graph.detector_sdk import DetectorCapability, DetectorMetadata, MethodDetectionContext
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
    parameters: tuple[str, ...]
    return_type: str
    start: int
    end: int
    annotations: str
    body: str


@dataclass(frozen=True)
class _Class:
    fqcn: str
    methods: tuple[_Method, ...]


class MessagingMethodDetector:
    """Extract literal Kafka/Rabbit producers and listener implementation methods."""

    metadata = DetectorMetadata(
        detector_id="messaging-method",
        detector_version="1",
        supported_languages=frozenset({"java"}),
        capabilities=(DetectorCapability("messaging-methods", "1"),),
    )
    _CLASS = re.compile(r"\bclass\s+(?P<name>\w+)[^{]*\{")
    _METHOD = re.compile(
        r"(?P<annotations>(?:\s*@[\w.]+\s*(?:\([^)]*\))?\s*)*)"
        r"(?:public|protected|private)?\s*(?:static\s+)?"
        r"(?P<return>[\w.$<>\[\]?]+)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*\{",
        re.DOTALL,
    )
    _LISTENER = re.compile(r"@(?P<kind>KafkaListener|RabbitListener)\s*\((?P<args>[^)]*)\)", re.DOTALL)
    _CALL = re.compile(r"\b(?P<receiver>[A-Za-z_]\w*)\s*\.\s*(?P<method>send|convertAndSend)\s*\((?P<args>[^;]*)\)")
    _STRING = r'"([^"\\]*(?:\\.[^"\\]*)*)"'

    def detect_methods(self, snapshot: RepositorySnapshot, context: MethodDetectionContext) -> MethodFacts:
        if (snapshot.repo_id, snapshot.source_revision) != (context.repo_id, context.source_revision):
            raise ValueError("method detection context must match repository snapshot")
        evidences: list[MethodEvidence] = []
        operations: list[ServiceOperation] = []
        implementations: list[ImplementationMethod] = []
        calls: list[ConsumerMethodCall] = []
        bindings: list[OperationBinding] = []
        unresolved: list[MethodUnresolved] = []
        for path in sorted(snapshot.root_path.rglob("*.java")):
            relative = path.relative_to(snapshot.root_path).as_posix()
            text = path.read_text(encoding="utf-8")
            declarations = self._template_declarations(text)
            methods = tuple(method for item in self._classes(text) for method in item.methods)
            ranges = tuple((method.start, method.end) for method in methods)
            for item in self._classes(text):
                for method in item.methods:
                    implementation = self._implementation(context, relative, item.fqcn, method, evidences)
                    implementations.append(implementation)
                    self._listeners(
                        context,
                        relative,
                        item.fqcn,
                        method,
                        implementation,
                        evidences,
                        operations,
                        bindings,
                        unresolved,
                    )
                    self._calls(context, relative, method, implementation, declarations, evidences, calls, unresolved)
            self._orphan_calls(context, relative, text, ranges, declarations, evidences, unresolved)
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
            self._coalesce(unresolved),
        )

    def _listeners(
        self,
        context: MethodDetectionContext,
        path: str,
        fqcn: str,
        method: _Method,
        implementation: ImplementationMethod,
        evidences: list[MethodEvidence],
        operations: list[ServiceOperation],
        bindings: list[OperationBinding],
        unresolved: list[MethodUnresolved],
    ) -> None:
        for match in self._LISTENER.finditer(method.annotations):
            broker = "kafka" if match.group("kind") == "KafkaListener" else "rabbitmq"
            destination_name, group_name = ("topics", "groupId") if broker == "kafka" else ("queues", "group")
            destination_expression = self._named(match.group("args"), destination_name)
            destinations = self._literal_values(destination_expression)
            line = method.start + method.annotations.count("\n", 0, match.start())
            if not destinations:
                reason = (
                    "DYNAMIC_TARGET"
                    if destination_expression is not None and destination_expression.strip() not in {"", "{}"}
                    else "UNSUPPORTED_TARGET_SHAPE"
                )
                self._unresolved(context, path, line, match.group(0), reason, evidences, unresolved)
                continue
            groups = self._literal_values(self._named(match.group("args"), group_name))
            if len(groups) > 1:
                self._unresolved(context, path, line, match.group(0), "UNSUPPORTED_TARGET_SHAPE", evidences, unresolved)
                continue
            group = groups[0] if groups else "-"
            for destination in destinations:
                reference = self._reference(broker, destination, group)
                evidence = self._evidence(context, path, line, "listener_declaration", reference, evidences)
                operation = ServiceOperation(
                    context.repo_id,
                    context.module_id,
                    context.service_id,
                    context.source_revision,
                    context.generation_id,
                    "provider",
                    reference,
                    method.name,
                    self._signature(fqcn, method),
                    (evidence.id,),
                    group=group,
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

    def _calls(
        self,
        context: MethodDetectionContext,
        path: str,
        method: _Method,
        implementation: ImplementationMethod,
        declarations: dict[str, str],
        evidences: list[MethodEvidence],
        calls: list[ConsumerMethodCall],
        unresolved: list[MethodUnresolved],
    ) -> None:
        for match in self._CALL.finditer(method.body):
            broker = self._broker(declarations.get(match.group("receiver")), match.group("method"))
            if broker is None:
                continue
            line = method.start + method.body.count("\n", 0, match.start())
            destination = self._destination(broker, self._split_args(match.group("args")))
            if destination is None:
                self._unresolved(context, path, line, match.group(0), "DYNAMIC_TARGET", evidences, unresolved)
                continue
            evidence = self._evidence(context, path, line, "producer_method_call", destination, evidences)
            calls.append(
                ConsumerMethodCall(
                    context.repo_id,
                    context.module_id,
                    context.service_id,
                    context.source_revision,
                    context.generation_id,
                    implementation.id,
                    self._reference(broker, destination),
                    "operation",
                    (evidence.id,),
                )
            )

    def _orphan_calls(
        self,
        context: MethodDetectionContext,
        path: str,
        text: str,
        ranges: tuple[tuple[int, int], ...],
        declarations: dict[str, str],
        evidences: list[MethodEvidence],
        unresolved: list[MethodUnresolved],
    ) -> None:
        for match in self._CALL.finditer(text):
            if self._broker(declarations.get(match.group("receiver")), match.group("method")) is None:
                continue
            line = text.count("\n", 0, match.start()) + 1
            if any(start <= line <= end for start, end in ranges):
                continue
            self._unresolved(context, path, line, match.group(0), "MISSING_IMPLEMENTATION", evidences, unresolved)

    def _implementation(
        self, context: MethodDetectionContext, path: str, fqcn: str, method: _Method, evidences: list[MethodEvidence]
    ) -> ImplementationMethod:
        evidence = self._evidence(context, path, method.start, "implementation_method", method.name, evidences)
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

    @classmethod
    def _classes(cls, text: str) -> tuple[_Class, ...]:
        package = re.search(r"\bpackage\s+([\w.]+)\s*;", text)
        prefix = f"{package.group(1)}." if package else ""
        result: list[_Class] = []
        for match in cls._CLASS.finditer(text):
            opening = text.find("{", match.start(), match.end())
            closing = cls._matching_brace(text, opening)
            if closing < 0:
                continue
            result.append(_Class(prefix + match.group("name"), tuple(cls._methods(text, opening + 1, closing))))
        return tuple(result)

    @classmethod
    def _methods(cls, text: str, start: int, end: int) -> list[_Method]:
        body, result = text[start:end], []
        for match in cls._METHOD.finditer(body):
            opening = start + match.end() - 1
            closing = cls._matching_brace(text, opening)
            if closing < 0 or closing > end:
                continue
            params = tuple(
                cls._type(value.strip().split()[0]) for value in match.group("params").split(",") if value.strip()
            )
            line = text.count("\n", 0, start + match.start()) + 1
            result.append(
                _Method(
                    match.group("name"),
                    params,
                    cls._type(match.group("return")),
                    line,
                    text.count("\n", 0, closing) + 1,
                    match.group("annotations"),
                    text[opening + 1 : closing],
                )
            )
        return result

    @classmethod
    def _template_declarations(cls, text: str) -> dict[str, str]:
        return {
            name: typ
            for typ, name in re.findall(r"\b(KafkaTemplate|RabbitTemplate)(?:\s*<[^;{}>]+>)?\s+(\w+)\s*(?:=|;)", text)
        }

    @classmethod
    def _destination(cls, broker: str, args: list[str]) -> str | None:
        expected = 2 if broker == "kafka" else 3
        if len(args) != expected or (broker == "rabbitmq" and cls._literal(args[1]) is None):
            return None
        return cls._literal(args[0])

    @staticmethod
    def _broker(declaration: str | None, method: str) -> str | None:
        if declaration == "KafkaTemplate" and method == "send":
            return "kafka"
        if declaration == "RabbitTemplate" and method == "convertAndSend":
            return "rabbitmq"
        return None

    @classmethod
    def _named(cls, args: str, name: str) -> str | None:
        match = re.search(rf"\b{name}\s*=\s*(?P<value>\{{[^}}]*\}}|{cls._STRING}|[^,]+)", args)
        return match.group("value").strip() if match else None

    @classmethod
    def _literal_values(cls, expression: str | None) -> list[str]:
        if expression is None:
            return []
        if re.fullmatch(rf"\s*{cls._STRING}\s*", expression):
            value = cls._literal(expression)
            return [value] if value is not None and not cls._dynamic(value) else []
        if re.fullmatch(r"\s*\{\s*(?:\"[^\"]*\"\s*,?\s*)+\}\s*", expression):
            return [value for value in re.findall(cls._STRING, expression) if not cls._dynamic(value)]
        return []

    @staticmethod
    def _dynamic(value: str) -> bool:
        return "${" in value or "#{" in value

    @classmethod
    def _literal(cls, expression: str) -> str | None:
        match = re.fullmatch(rf"\s*{cls._STRING}\s*", expression)
        return match.group(1) if match else None

    @staticmethod
    def _split_args(value: str) -> list[str]:
        parts, start, depth, quoted = [], 0, 0, False
        for index, char in enumerate(value):
            if char == '"' and (index == 0 or value[index - 1] != "\\"):
                quoted = not quoted
            elif not quoted and char in "([{":
                depth += 1
            elif not quoted and char in ")]}":
                depth -= 1
            elif not quoted and char == "," and depth == 0:
                parts.append(value[start:index].strip())
                start = index + 1
        return [*parts, value[start:].strip()] if value.strip() else parts

    @staticmethod
    def _matching_brace(text: str, opening: int) -> int:
        depth = 0
        for index in range(opening, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}" and (depth := depth - 1) == 0:
                return index
        return -1

    @staticmethod
    def _type(value: str) -> str:
        return {"String": "java.lang.String", "Object": "java.lang.Object", "void": "void"}.get(
            value.strip(), value.strip()
        )

    @classmethod
    def _signature(cls, fqcn: str, method: _Method) -> str:
        return f"{fqcn}#{method.name}({','.join(method.parameters)}):{method.return_type}"

    @staticmethod
    def _reference(broker: str, destination: str, group: str | None = None) -> str:
        base = f"messaging-operation:{broker}|destination={destination}"
        return base if group is None else f"{base}|group={group}"

    def _evidence(
        self,
        context: MethodDetectionContext,
        path: str,
        line: int,
        kind: str,
        subject: str,
        items: list[MethodEvidence],
    ) -> MethodEvidence:
        evidence = MethodEvidence(
            context.repo_id,
            context.module_id,
            context.service_id,
            context.source_revision,
            context.generation_id,
            path,
            line,
            line,
            self.metadata.detector_id,
            self.metadata.detector_version,
            kind,
            subject,
            1.0,
        )
        items.append(evidence)
        return evidence

    def _unresolved(
        self,
        context: MethodDetectionContext,
        path: str,
        line: int,
        subject: str,
        reason: str,
        evidences: list[MethodEvidence],
        unresolved: list[MethodUnresolved],
    ) -> None:
        evidence = self._evidence(context, path, line, "unresolved_method_target", subject, evidences)
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
        grouped: dict[tuple[str, str], list[str]] = {}
        representatives: dict[tuple[str, str], MethodUnresolved] = {}
        for item in items:
            key = (item.reason_code, item.subject)
            grouped.setdefault(key, []).extend(item.evidence_ids)
            representatives.setdefault(key, item)
        return tuple(
            MethodUnresolved(
                item.repo_id,
                item.module_id,
                item.service_id,
                item.source_revision,
                item.generation_id,
                reason,
                subject,
                tuple(sorted(set(ids))),
            )
            for (reason, subject), ids in sorted(grouped.items())
            for item in (representatives[(reason, subject)],)
        )
