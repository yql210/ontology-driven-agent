"""Protocol-neutral Dubbo method detector for supported Java source shapes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree

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
    RetainedSourceCall,
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
    body_start_column: int


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
    evidence_ids: tuple[str, ...] = ()
    xml_reference_id: str | None = None
    receiver_declaration: str | None = None


@dataclass(frozen=True)
class _XmlDeclaration:
    kind: str
    interface: str | None
    target: str | None
    group: str | None
    version: str | None
    alias: str | None
    dynamic: bool
    path: str
    line: int
    evidence_id: str


class DubboMethodDetector:
    """Extract Dubbo provider operations and statically resolvable proxy calls."""

    metadata = DetectorMetadata(
        detector_id="dubbo-method",
        detector_version="1",
        supported_languages=frozenset({"java", "xml"}),
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
    _FIELD = re.compile(
        r"(?:public|protected|private)?\s*(?:final\s+)?(?P<type>[\w.$<>]+)\s+(?P<name>\w+)\s*(?:=[^;]*)?;"
    )
    _XML_DUBBO = re.compile(r"<dubbo:(?P<kind>service|reference)\b(?P<attrs>[^>]*)/?>", re.DOTALL)
    _XML_BEAN = re.compile(r"<bean\b(?P<attrs>[^>]*)/?>", re.DOTALL)
    _XML_ATTRIBUTE = re.compile(r"\b(?P<key>[\w:-]+)\s*=\s*(['\"])(?P<value>.*?)\2", re.DOTALL)

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
        retained_source_calls: list[RetainedSourceCall] = []
        bindings: list[OperationBinding] = []
        unresolved: list[MethodUnresolved] = []
        xml_declarations, bean_classes = self._xml_declarations(snapshot, context, evidences, unresolved)
        services = tuple(item for item in xml_declarations if item.kind == "service")
        references = tuple(item for item in xml_declarations if item.kind == "reference")
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
            self._xml_provider_facts(
                context,
                relative,
                java_class,
                imports,
                contracts,
                implementation_by_method,
                services,
                bean_classes,
                evidences,
                operations,
                bindings,
                unresolved,
            )
            proxies = self._proxies(context, relative, java_class, imports, evidences)
            proxies.update(self._xml_proxies(context, relative, java_class, imports, references, evidences, unresolved))
            for method, implementation in implementation_by_method.items():
                self._proxy_calls(
                    context,
                    relative,
                    method,
                    implementation,
                    proxies,
                    contracts,
                    evidences,
                    calls,
                    retained_source_calls,
                    unresolved,
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
            tuple(retained_source_calls),
        )

    def _xml_declarations(
        self,
        snapshot: RepositorySnapshot,
        context: MethodDetectionContext,
        evidences: list[MethodEvidence],
        unresolved: list[MethodUnresolved],
    ) -> tuple[tuple[_XmlDeclaration, ...], dict[str, tuple[str, ...]]]:
        declarations: list[_XmlDeclaration] = []
        beans: dict[str, list[str]] = {}
        for source in sorted(snapshot.root_path.rglob("*.xml")):
            path = source.relative_to(snapshot.root_path).as_posix()
            text = source.read_text(encoding="utf-8")
            matches = tuple(self._XML_DUBBO.finditer(text))
            if not matches:
                if "<dubbo:" in text:
                    line = text.count("\n", 0, text.index("<dubbo:")) + 1
                    self._unresolved(
                        context, path, line, "UNSUPPORTED_TARGET_SHAPE", "malformed dubbo XML", evidences, unresolved
                    )
                continue
            try:
                ElementTree.fromstring(text)
            except ElementTree.ParseError:
                line = text.count("\n", 0, text.index("<dubbo:")) + 1
                self._unresolved(
                    context, path, line, "UNSUPPORTED_TARGET_SHAPE", "malformed dubbo XML", evidences, unresolved
                )
                continue
            for bean in self._XML_BEAN.finditer(text):
                attrs = self._xml_attributes(bean.group("attrs"))
                bean_id, class_name = attrs.get("id"), attrs.get("class")
                if bean_id and class_name:
                    beans.setdefault(bean_id, []).append(class_name)
            for match in matches:
                attrs = self._xml_attributes(match.group("attrs"))
                line = text.count("\n", 0, match.start()) + 1
                interface = attrs.get("interface")
                target_key = "ref" if match.group("kind") == "service" else "id"
                target = attrs.get(target_key)
                dynamic = any(self._dynamic(value) for value in attrs.values())
                evidence = self._evidence(
                    context, path, line, line, f"dubbo_xml_{match.group('kind')}", match.group(0).strip()
                )
                evidences.append(evidence)
                declarations.append(
                    _XmlDeclaration(
                        match.group("kind"),
                        interface,
                        target,
                        attrs.get("group"),
                        attrs.get("version"),
                        attrs.get("alias"),
                        dynamic,
                        path,
                        line,
                        evidence.id,
                    )
                )
        return tuple(declarations), {key: tuple(value) for key, value in beans.items()}

    def _xml_provider_facts(
        self,
        context: MethodDetectionContext,
        path: str,
        java_class: _Class,
        imports: dict[str, str],
        contracts: dict[str, _Class],
        implementations: dict[_Method, ImplementationMethod],
        services: tuple[_XmlDeclaration, ...],
        bean_classes: dict[str, tuple[str, ...]],
        evidences: list[MethodEvidence],
        operations: list[ServiceOperation],
        bindings: list[OperationBinding],
        unresolved: list[MethodUnresolved],
    ) -> None:
        for service in services:
            if service.dynamic:
                self._xml_service_unresolved(
                    context,
                    path,
                    java_class,
                    service,
                    "DYNAMIC_TARGET",
                    service.target or "dubbo:service",
                    evidences,
                    unresolved,
                )
                continue
            targets = bean_classes.get(service.target or "", ())
            if len(targets) != 1:
                reason = "AMBIGUOUS_TARGET" if len(targets) > 1 else "MISSING_DECLARATION"
                self._xml_service_unresolved(
                    context, path, java_class, service, reason, service.target or "dubbo:service", evidences, unresolved
                )
                continue
            if targets[0] != java_class.fqcn:
                continue
            if service.interface is None:
                self._append_unresolved(
                    context, "MISSING_DECLARATION", java_class.fqcn, (service.evidence_id,), unresolved
                )
                continue
            self._provider_facts(
                context,
                service.path,
                java_class,
                imports,
                contracts,
                "",
                implementations,
                evidences,
                operations,
                bindings,
                unresolved,
                interface_value=service.interface,
                settings=(service.group, service.version, service.alias),
                source_evidence_ids=(service.evidence_id,),
                xml_service_ref=service.target,
            )

    def _xml_service_unresolved(
        self,
        context: MethodDetectionContext,
        path: str,
        java_class: _Class,
        service: _XmlDeclaration,
        reason: str,
        subject: str,
        evidences: list[MethodEvidence],
        unresolved: list[MethodUnresolved],
    ) -> None:
        evidence_ids = (service.evidence_id,)
        if service.interface in java_class.interfaces:
            evidence = self._evidence(
                context,
                path,
                java_class.body_offset,
                java_class.body_offset,
                "dubbo_xml_service_target",
                java_class.fqcn,
            )
            evidences.append(evidence)
            evidence_ids = (*evidence_ids, evidence.id)
        self._append_unresolved(context, reason, subject, evidence_ids, unresolved)

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
        *,
        interface_value: str | None = None,
        settings: tuple[str | None, str | None, str | None] | None = None,
        source_evidence_ids: tuple[str, ...] = (),
        xml_service_ref: str | None = None,
    ) -> None:
        interface_value = (
            interface_value or self._class_value(args) or (java_class.interfaces[0] if java_class.interfaces else None)
        )
        settings, dynamic = (settings, False) if settings is not None else self._settings(args)
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
            evidence = self._evidence(
                context, implementation.file_path, method.start, method.end, "dubbo_provider_method", method.name
            )
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
                (*source_evidence_ids, evidence.id),
                *settings,
                binding_identity=(f"xml-service-ref:{xml_service_ref}" if xml_service_ref is not None else None),
            )
            operations.append(operation)
            bindings.append(
                OperationBinding(
                    context.repo_id,
                    context.module_id,
                    context.service_id,
                    context.source_revision,
                    context.generation_id,
                    self._reference(signature, *settings, xml_service_ref=xml_service_ref),
                    operation.id,
                    implementation.id,
                    (*source_evidence_ids, evidence.id),
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
        retained_source_calls: list[RetainedSourceCall],
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
            argument_summaries = tuple(item.strip() for item in match.group("args").split(",") if item.strip())
            argument_types = tuple(self._argument_type(item, method) for item in argument_summaries)
            declaration = self._called_declaration(
                contracts.get(proxy.interface), match.group("method"), match.group("args"), method
            )
            argument_evidence_ids: list[tuple[str, ...]] = []
            for index, argument in enumerate(argument_summaries):
                argument_evidence = self._evidence(
                    context, path, line, line, "dubbo_proxy_argument", f"{subject} argument {index}: {argument}"
                )
                evidences.append(argument_evidence)
                argument_evidence_ids.append((argument_evidence.id,))
            resolution_reason = (
                "DYNAMIC_TARGET"
                if proxy.dynamic
                else "CONTRACT_MISSING"
                if contracts.get(proxy.interface) is None
                else "ARGUMENT_TYPE_UNKNOWN"
                if any(argument_type is None for argument_type in argument_types)
                else "METHOD_DECLARATION_MISSING"
                if declaration is None
                else None
            )
            retained_source_calls.append(
                RetainedSourceCall(
                    context.repo_id,
                    context.module_id,
                    context.service_id,
                    context.source_revision,
                    context.generation_id,
                    path,
                    line,
                    self._call_position(method, match.start())[1],
                    line + self._call_position(method, match.end())[0],
                    self._call_position(method, match.end())[1],
                    implementation.id,
                    proxy.receiver_declaration or match.group("receiver"),
                    proxy.interface,
                    None,
                    proxy.evidence_ids,
                    match.group("method"),
                    argument_summaries,
                    argument_types,
                    tuple(argument_evidence_ids),
                    self._protocol_settings(proxy),
                    "SOURCE_CAPTURE",
                    "UNRESOLVED" if resolution_reason is not None else "CAPTURED",
                    resolution_reason,
                    (*proxy.evidence_ids, evidence.id, *(item[0] for item in argument_evidence_ids)),
                )
            )
            if proxy.dynamic:
                self._append_unresolved(
                    context, "DYNAMIC_TARGET", subject, (*proxy.evidence_ids, evidence.id), unresolved
                )
                continue
            if declaration is None:
                self._append_unresolved(
                    context, "MISSING_DECLARATION", subject, (*proxy.evidence_ids, evidence.id), unresolved
                )
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
                        self._signature(proxy.interface, declaration),
                        proxy.group,
                        proxy.version,
                        proxy.alias,
                        xml_reference_id=proxy.xml_reference_id,
                    ),
                    "operation",
                    (*proxy.evidence_ids, evidence.id),
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
            self._append_unresolved(
                context,
                "MISSING_IMPLEMENTATION",
                match.group(0).strip(),
                (*proxies[match.group("receiver")].evidence_ids, evidence.id),
                unresolved,
            )

    def _proxies(
        self,
        context: MethodDetectionContext,
        path: str,
        java_class: _Class,
        imports: dict[str, str],
        evidences: list[MethodEvidence],
    ) -> dict[str, _Proxy]:
        result: dict[str, _Proxy] = {}
        for match in self._REFERENCE.finditer(java_class.body):
            settings, dynamic = self._settings(match.group("args") or "")
            line = java_class.body_offset + java_class.body.count("\n", 0, match.start())
            evidence = self._evidence(context, path, line, line, "dubbo_proxy_receiver", match.group(0).strip())
            evidences.append(evidence)
            result[match.group("name")] = _Proxy(
                self._fqcn(match.group("type").split("<", 1)[0], java_class.fqcn, imports),
                *settings,
                dynamic,
                (evidence.id,),
                receiver_declaration=match.group(0).strip(),
            )
        return result

    def _xml_proxies(
        self,
        context: MethodDetectionContext,
        path: str,
        java_class: _Class,
        imports: dict[str, str],
        references: tuple[_XmlDeclaration, ...],
        evidences: list[MethodEvidence],
        unresolved: list[MethodUnresolved],
    ) -> dict[str, _Proxy]:
        fields = {
            match.group("name"): (
                self._fqcn(match.group("type").split("<", 1)[0], java_class.fqcn, imports),
                match.group(0).strip(),
                java_class.body_offset + java_class.body.count("\n", 0, match.start()),
            )
            for match in self._FIELD.finditer(java_class.body)
        }
        result: dict[str, _Proxy] = {}
        for reference in references:
            if reference.dynamic:
                if reference.target in fields:
                    field_type, declaration, line = fields[reference.target]
                    receiver_evidence = self._evidence(context, path, line, line, "dubbo_proxy_receiver", declaration)
                    evidences.append(receiver_evidence)
                    result[reference.target] = _Proxy(
                        reference.interface or field_type,
                        reference.group,
                        reference.version,
                        reference.alias,
                        True,
                        (reference.evidence_id, receiver_evidence.id),
                        reference.target,
                        declaration,
                    )
                continue
            if reference.interface is None or reference.target is None:
                self._append_unresolved(
                    context,
                    "MISSING_DECLARATION",
                    reference.target or "dubbo:reference",
                    (reference.evidence_id,),
                    unresolved,
                )
                continue
            field = fields.get(reference.target)
            if field is None or field[0] != reference.interface:
                evidence_ids = (reference.evidence_id,)
                if field is not None:
                    evidence = self._evidence(
                        context,
                        path,
                        java_class.body_offset,
                        java_class.body_offset,
                        "dubbo_xml_reference_target",
                        reference.target,
                    )
                    evidences.append(evidence)
                    evidence_ids = (*evidence_ids, evidence.id)
                self._append_unresolved(context, "MISSING_DECLARATION", reference.target, evidence_ids, unresolved)
                continue
            field_type, declaration, line = field
            receiver_evidence = self._evidence(context, path, line, line, "dubbo_proxy_receiver", declaration)
            evidences.append(receiver_evidence)
            result[reference.target] = _Proxy(
                reference.interface,
                reference.group,
                reference.version,
                reference.alias,
                False,
                (reference.evidence_id, receiver_evidence.id),
                reference.target,
                declaration,
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
                    opening - text.rfind("\n", 0, opening) + 1,
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
    def _call_position(method: _Method, offset: int) -> tuple[int, int]:
        prefix = method.body[:offset]
        line_offset = prefix.count("\n")
        if line_offset == 0:
            return 0, method.body_start_column + offset
        return line_offset, len(prefix.rsplit("\n", 1)[-1]) + 1

    @staticmethod
    def _protocol_settings(proxy: _Proxy) -> tuple[tuple[str, str], ...]:
        return tuple(
            (name, value)
            for name, value in (("group", proxy.group), ("version", proxy.version), ("alias", proxy.alias))
            if value is not None
        )

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

    @classmethod
    def _xml_attributes(cls, text: str) -> dict[str, str]:
        return {match.group("key"): match.group("value") for match in cls._XML_ATTRIBUTE.finditer(text)}

    @staticmethod
    def _dynamic(value: str) -> bool:
        return "${" in value or "#{" in value

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
    def _reference(
        signature: str,
        group: str | None,
        version: str | None,
        alias: str | None,
        *,
        xml_reference_id: str | None = None,
        xml_service_ref: str | None = None,
    ) -> str:
        reference = f"dubbo-operation:{signature}|group={group or ''}|version={version or ''}|alias={alias or ''}"
        if xml_reference_id is not None or xml_service_ref is not None:
            reference = f"{reference}|origin=xml"
        if xml_reference_id is not None:
            reference = f"{reference}|xml-reference-id={xml_reference_id}"
        if xml_service_ref is not None:
            reference = f"{reference}|xml-service-ref={xml_service_ref}"
        return reference

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
        self._append_unresolved(context, reason, subject, (evidence.id,), unresolved)

    @classmethod
    def _append_unresolved(
        cls,
        context: MethodDetectionContext,
        reason: str,
        subject: str,
        evidence_ids: tuple[str, ...],
        unresolved: list[MethodUnresolved],
    ) -> None:
        unresolved.append(cls._make_unresolved(context, reason, subject, evidence_ids))

    @staticmethod
    def _make_unresolved(
        context: MethodDetectionContext, reason: str, subject: str, evidence_ids: tuple[str, ...]
    ) -> MethodUnresolved:
        return MethodUnresolved(
            context.repo_id,
            context.module_id,
            context.service_id,
            context.source_revision,
            context.generation_id,
            reason,
            subject,
            tuple(sorted(set(evidence_ids))),
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
