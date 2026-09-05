"""Protocol-neutral Dubbo method detector for supported Java source shapes."""

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
    parameters: tuple[str, ...]
    return_type: str
    start: int
    end: int
    body: str


@dataclass(frozen=True)
class _Class:
    fqcn: str
    name: str
    is_interface: bool
    interfaces: tuple[str, ...]
    annotation_text: str
    body: str
    body_offset: int
    methods: tuple[_Method, ...]


@dataclass(frozen=True)
class _Proxy:
    interface: str
    group: str | None
    version: str | None
    alias: str | None
    dynamic: bool


class DubboMethodDetector:
    """Extract Dubbo provider operations and statically resolvable proxy calls."""

    metadata = DetectorMetadata(
        detector_id="dubbo-method",
        detector_version="1",
        supported_languages=frozenset({"java"}),
        capabilities=(DetectorCapability("dubbo-methods", "1"),),
    )

    _CLASS = re.compile(
        r"(?P<annotations>(?:\s*@[\w.]+(?:\s*\([^)]*\))?\s*)*)"
        r"(?:public\s+|protected\s+|private\s+|abstract\s+|final\s+)*"
        r"(?P<kind>class|interface)\s+(?P<name>\w+)"
        r"(?:\s+implements\s+(?P<interfaces>[^\{]+))?\s*\{",
        re.DOTALL,
    )
    _METHOD = re.compile(
        r"(?:@[\w.]+(?:\s*\([^)]*\))?\s*)*"
        r"(?:public\s+|protected\s+|private\s+|static\s+|final\s+|abstract\s+)*"
        r"(?P<return>[\w.$<>\[\]?]+)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*"
        r"(?P<terminator>\{|;)",
        re.DOTALL,
    )
    _REFERENCE = re.compile(
        r"@DubboReference\b(?:\s*\((?P<args>[^)]*)\))?\s*"
        r"(?:(?:public|protected|private|final|static)\s+)*(?P<type>[\w.$<>]+)\s+(?P<name>\w+)\b"
    )
    _CALL = re.compile(r"\b(?P<receiver>[A-Za-z_]\w*)\s*\.\s*(?P<method>[A-Za-z_]\w*)\s*\((?P<args>[^)]*)\)")

    def detect_methods(self, snapshot: RepositorySnapshot, context: MethodDetectionContext) -> MethodFacts:
        if (snapshot.repo_id, snapshot.source_revision) != (context.repo_id, context.source_revision):
            raise ValueError("method detection context must match repository snapshot")
        parsed: list[tuple[str, _Class, dict[str, str]]] = []
        for path in sorted(snapshot.root_path.rglob("*.java")):
            relative = path.relative_to(snapshot.root_path).as_posix()
            text = path.read_text(encoding="utf-8")
            imports = self._imports(text)
            parsed.extend((relative, item, imports) for item in self._classes(text, imports))
        contracts = {item.fqcn: item for _, item, _ in parsed if item.is_interface}
        evidences: list[MethodEvidence] = []
        operations: list[ServiceOperation] = []
        implementations: list[ImplementationMethod] = []
        calls: list[ConsumerMethodCall] = []
        bindings: list[OperationBinding] = []
        unresolved: list[MethodUnresolved] = []
        for relative, java_class, imports in parsed:
            if java_class.is_interface:
                continue
            implementation_by_method: dict[_Method, ImplementationMethod] = {}
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
            service_args = self._annotation_args(java_class.annotation_text, "DubboService")
            if service_args is not None:
                self._provider_facts(
                    context,
                    relative,
                    java_class,
                    imports,
                    contracts,
                    service_args,
                    implementation_by_method,
                    evidences,
                    operations,
                    bindings,
                    unresolved,
                )
            proxies = self._proxies(java_class, imports)
            for method, implementation in implementation_by_method.items():
                self._proxy_calls(
                    context, relative, method, implementation, proxies, contracts, evidences, calls, unresolved
                )
            self._orphan_proxy_calls(context, relative, java_class, proxies, evidences, unresolved)
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

    def _provider_facts(
        self,
        context: MethodDetectionContext,
        path: str,
        java_class: _Class,
        imports: dict[str, str],
        contracts: dict[str, _Class],
        args: str,
        implementations: dict[_Method, ImplementationMethod],
        evidences: list[MethodEvidence],
        operations: list[ServiceOperation],
        bindings: list[OperationBinding],
        unresolved: list[MethodUnresolved],
    ) -> None:
        interface_value = self._class_value(args) or (java_class.interfaces[0] if java_class.interfaces else None)
        settings, dynamic = self._settings(args)
        if interface_value is None or dynamic:
            self._unresolved(
                context,
                path,
                java_class.body_offset,
                "DYNAMIC_TARGET" if dynamic else "MISSING_DECLARATION",
                java_class.fqcn,
                evidences,
                unresolved,
            )
            return
        interface = self._fqcn(interface_value, java_class.fqcn, imports)
        contract = contracts.get(interface)
        if contract is None:
            self._unresolved(
                context, path, java_class.body_offset, "MISSING_DECLARATION", interface, evidences, unresolved
            )
            return
        contract_methods = {(item.name, item.parameters): item for item in contract.methods}
        for method, implementation in implementations.items():
            declaration = contract_methods.get((method.name, method.parameters))
            if declaration is None:
                continue
            evidence = self._evidence(context, path, method.start, method.end, "dubbo_provider_method", method.name)
            evidences.append(evidence)
            signature = self._signature(interface, declaration)
            operation = ServiceOperation(
                context.repo_id,
                context.module_id,
                context.service_id,
                context.source_revision,
                context.generation_id,
                "provider",
                interface,
                method.name,
                signature,
                (evidence.id,),
                *settings,
            )
            operations.append(operation)
            bindings.append(
                OperationBinding(
                    context.repo_id,
                    context.module_id,
                    context.service_id,
                    context.source_revision,
                    context.generation_id,
                    self._reference(signature, *settings),
                    operation.id,
                    implementation.id,
                    (evidence.id,),
                )
            )

    def _proxy_calls(
        self,
        context: MethodDetectionContext,
        path: str,
        method: _Method,
        implementation: ImplementationMethod,
        proxies: dict[str, _Proxy],
        contracts: dict[str, _Class],
        evidences: list[MethodEvidence],
        calls: list[ConsumerMethodCall],
        unresolved: list[MethodUnresolved],
    ) -> None:
        for match in self._CALL.finditer(method.body):
            proxy = proxies.get(match.group("receiver"))
            if proxy is None:
                continue
            line = method.start + method.body.count("\n", 0, match.start())
            subject = match.group(0).strip()
            evidence = self._evidence(context, path, line, line, "dubbo_proxy_call", subject)
            evidences.append(evidence)
            if proxy.dynamic:
                unresolved.append(self._make_unresolved(context, "DYNAMIC_TARGET", subject, evidence.id))
                continue
            declaration = self._called_declaration(
                contracts.get(proxy.interface), match.group("method"), match.group("args"), method
            )
            if declaration is None:
                unresolved.append(self._make_unresolved(context, "MISSING_DECLARATION", subject, evidence.id))
                continue
            calls.append(
                ConsumerMethodCall(
                    context.repo_id,
                    context.module_id,
                    context.service_id,
                    context.source_revision,
                    context.generation_id,
                    implementation.id,
                    self._reference(
                        self._signature(proxy.interface, declaration), proxy.group, proxy.version, proxy.alias
                    ),
                    "operation",
                    (evidence.id,),
                )
            )

    def _orphan_proxy_calls(
        self,
        context: MethodDetectionContext,
        path: str,
        java_class: _Class,
        proxies: dict[str, _Proxy],
        evidences: list[MethodEvidence],
        unresolved: list[MethodUnresolved],
    ) -> None:
        covered = tuple((method.start, method.end) for method in java_class.methods)
        for match in self._CALL.finditer(java_class.body):
            if match.group("receiver") not in proxies:
                continue
            line = java_class.body_offset + java_class.body.count("\n", 0, match.start())
            if any(start <= line <= end for start, end in covered):
                continue
            evidence = self._evidence(context, path, line, line, "dubbo_proxy_call", match.group(0).strip())
            evidences.append(evidence)
            unresolved.append(
                self._make_unresolved(context, "MISSING_IMPLEMENTATION", match.group(0).strip(), evidence.id)
            )

    def _proxies(self, java_class: _Class, imports: dict[str, str]) -> dict[str, _Proxy]:
        result: dict[str, _Proxy] = {}
        for match in self._REFERENCE.finditer(java_class.body):
            settings, dynamic = self._settings(match.group("args") or "")
            result[match.group("name")] = _Proxy(
                self._fqcn(match.group("type").split("<", 1)[0], java_class.fqcn, imports), *settings, dynamic
            )
        return result

    def _classes(self, text: str, imports: dict[str, str]) -> tuple[_Class, ...]:
        package_match = re.search(r"\bpackage\s+([\w.]+)\s*;", text)
        package = package_match.group(1) if package_match else ""
        result: list[_Class] = []
        for match in self._CLASS.finditer(text):
            opening = text.find("{", match.start(), match.end())
            closing = self._brace(text, opening)
            if closing < 0:
                continue
            body = text[opening + 1 : closing]
            fqcn = f"{package}.{match.group('name')}" if package else match.group("name")
            interfaces = tuple(
                self._fqcn(item.strip().split("<", 1)[0], fqcn, imports)
                for item in (match.group("interfaces") or "").split(",")
                if item.strip()
            )
            result.append(
                _Class(
                    fqcn,
                    match.group("name"),
                    match.group("kind") == "interface",
                    interfaces,
                    match.group("annotations"),
                    body,
                    text.count("\n", 0, opening) + 1,
                    tuple(self._methods(body, opening + 1, text)),
                )
            )
        return tuple(result)

    def _methods(self, body: str, offset: int, text: str) -> list[_Method]:
        result: list[_Method] = []
        for match in self._METHOD.finditer(body):
            opening = offset + match.end() - 1
            closing = self._brace(text, opening) if match.group("terminator") == "{" else opening
            if closing < 0:
                continue
            params = tuple(self._parameter_type(item) for item in match.group("params").split(",") if item.strip())
            result.append(
                _Method(
                    match.group("name"),
                    params,
                    self._type(match.group("return")),
                    text.count("\n", 0, offset + match.start()) + 1,
                    text.count("\n", 0, closing) + 1,
                    text[opening + 1 : closing] if match.group("terminator") == "{" else "",
                )
            )
        return result

    def _called_declaration(self, contract: _Class | None, name: str, args: str, caller: _Method) -> _Method | None:
        if contract is None:
            return None
        argument_types = tuple(self._argument_type(item.strip(), caller) for item in args.split(",") if item.strip())
        if any(item is None for item in argument_types):
            return None
        matches = [item for item in contract.methods if (item.name, item.parameters) == (name, argument_types)]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _argument_type(value: str, caller: _Method) -> str | None:
        if re.fullmatch(r'"(?:[^"\\]|\\.)*"', value):
            return "java.lang.String"
        if re.fullmatch(r"\d+[lL]?", value):
            return "long"
        return None

    @staticmethod
    def _imports(text: str) -> dict[str, str]:
        return {item.rsplit(".", 1)[-1]: item for item in re.findall(r"\bimport\s+([\w.]+)\s*;", text)}

    @staticmethod
    def _annotation_args(text: str, name: str) -> str | None:
        match = re.search(rf"@{name}\b(?:\s*\(([^)]*)\))?", text)
        return match.group(1) if match else None

    @staticmethod
    def _class_value(args: str) -> str | None:
        match = re.search(r"\binterfaceClass\s*=\s*([\w.]+)\.class\b", args)
        return match.group(1) if match else None

    @staticmethod
    def _settings(args: str) -> tuple[tuple[str | None, str | None, str | None], bool]:
        values: list[str | None] = []
        dynamic = False
        for key in ("group", "version", "alias"):
            match = re.search(rf"\b{key}\s*=\s*\"([^\"]*)\"", args)
            raw = match.group(1) if match else None
            if (raw is not None and ("${" in raw or "#{" in raw)) or (
                re.search(rf"\b{key}\s*=", args) and match is None
            ):
                dynamic = True
            values.append(raw)
        return (values[0], values[1], values[2]), dynamic

    @staticmethod
    def _fqcn(value: str, current_fqcn: str, imports: dict[str, str]) -> str:
        if "." in value:
            return value
        return imports.get(value, f"{current_fqcn.rsplit('.', 1)[0]}.{value}" if "." in current_fqcn else value)

    @staticmethod
    def _type(value: str) -> str:
        return {"String": "java.lang.String", "Object": "java.lang.Object", "long": "long", "void": "void"}.get(
            value.strip(), value.strip()
        )

    def _parameter_type(self, value: str) -> str:
        return self._type(value.strip().split()[0])

    def _signature(self, fqcn: str, method: _Method) -> str:
        package = fqcn.rsplit(".", 1)[0] if "." in fqcn else ""
        return f"{fqcn}#{method.name}({','.join(method.parameters)}):{self._qualify(method.return_type, package)}"

    @staticmethod
    def _qualify(value: str, package: str) -> str:
        if "." in value or value in {"long", "void"}:
            return value
        return f"{package}.{value}" if package else value

    @staticmethod
    def _reference(signature: str, group: str | None, version: str | None, alias: str | None) -> str:
        return f"dubbo-operation:{signature}|group={group or ''}|version={version or ''}|alias={alias or ''}"

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
        evidence = self._evidence(context, path, line, line, "dubbo_unresolved", subject)
        evidences.append(evidence)
        unresolved.append(self._make_unresolved(context, reason, subject, evidence.id))

    @staticmethod
    def _make_unresolved(
        context: MethodDetectionContext, reason: str, subject: str, evidence_id: str
    ) -> MethodUnresolved:
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
        grouped: dict[tuple[str, str], MethodUnresolved] = {}
        evidence_ids: dict[tuple[str, str], set[str]] = {}
        for item in items:
            key = (item.reason_code, item.subject)
            grouped.setdefault(key, item)
            evidence_ids.setdefault(key, set()).update(item.evidence_ids)
        return tuple(
            MethodUnresolved(
                item.repo_id,
                item.module_id,
                item.service_id,
                item.source_revision,
                item.generation_id,
                item.reason_code,
                item.subject,
                tuple(sorted(evidence_ids[key])),
            )
            for key, item in sorted(grouped.items())
        )

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
