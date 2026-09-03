from __future__ import annotations

import re

from ontoagent.parsing.service_graph.models import (
    DetectorFacts,
    Evidence,
    RepositorySnapshot,
    RpcEndpoint,
    UnresolvedFact,
)


class DubboDetector:
    """Deterministic, deliberately small Dubbo source detector."""

    id = "dubbo"
    version = "1"
    supported_languages = frozenset({"java", "xml"})
    _STRING = r'"([^"\\]*(?:\\.[^"\\]*)*)"'

    def detect(self, snapshot: RepositorySnapshot) -> DetectorFacts:
        evidences: list[Evidence] = []
        endpoints: list[RpcEndpoint] = []
        unresolved: list[UnresolvedFact] = []
        paths = sorted((*snapshot.root_path.rglob("*.java"), *snapshot.root_path.rglob("*.xml")))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            relative = path.relative_to(snapshot.root_path).as_posix()
            if path.suffix == ".java":
                self._java(snapshot, text, relative, evidences, endpoints, unresolved)
            else:
                self._xml(snapshot, text, relative, evidences, endpoints, unresolved)
        return DetectorFacts(
            self.id,
            self.version,
            snapshot.repo_id,
            snapshot.source_revision,
            (),
            (),
            tuple(evidences),
            tuple(unresolved),
            rpc_endpoints=tuple(endpoints),
        )

    def _java(
        self,
        snapshot: RepositorySnapshot,
        text: str,
        path: str,
        evidences: list[Evidence],
        endpoints: list[RpcEndpoint],
        unresolved: list[UnresolvedFact],
    ) -> None:
        package_match = re.search(r"\bpackage\s+([\w.]+)\s*;", text)
        package = package_match.group(1) if package_match else ""
        class_pattern = re.compile(
            r"(?P<annotation>@DubboService\b(?:\s*\((?P<args>[^)]*)\))?)\s*"
            r"(?:public\s+)?class\s+(?P<name>\w+)"
            r"(?:\s+implements\s+(?P<implements>[\w., ]+))?",
        )
        for match in class_pattern.finditer(text):
            args = match.group("args") or ""
            implemented = (match.group("implements") or "").split(",", 1)[0].strip()
            interface = self._class_arg(args) or implemented
            line = self._line(text, match.start("annotation"))
            if not interface:
                self._unresolved(snapshot, path, line, match.group("annotation"), evidences, unresolved)
                continue
            interface = self._fqcn(interface, package)
            group, version = self._settings(args)
            body = self._class_body(text, match.end())
            methods = self._public_methods(body)
            if not methods:
                self._unresolved(snapshot, path, line, f"{match.group('name')}.<public-method>", evidences, unresolved)
            for method, offset in methods:
                self._endpoint(
                    snapshot,
                    path,
                    self._line(text, match.end() + offset),
                    "provider",
                    "provider_service",
                    interface,
                    method,
                    group,
                    version,
                    evidences,
                    endpoints,
                )

        fields: dict[str, tuple[str, str, str]] = {}
        field_pattern = re.compile(
            r"(?P<annotation>@DubboReference\b(?:\s*\((?P<args>[^)]*)\))?)\s*"
            r"(?:private|protected|public)?\s*(?P<type>[\w.<>]+)\s+(?P<name>\w+)\s*;",
        )
        for match in field_pattern.finditer(text):
            args = match.group("args") or ""
            interface = self._class_arg(args) or match.group("type")
            line = self._line(text, match.start("annotation"))
            if not interface:
                self._unresolved(snapshot, path, line, match.group("annotation"), evidences, unresolved)
                continue
            group, version = self._settings(args)
            fields[match.group("name")] = (self._fqcn(interface, package), group, version)

        call_pattern = re.compile(r"\b(?P<receiver>[A-Za-z_]\w*)\s*\.\s*(?P<method>[A-Za-z_]\w*)\s*\(")
        for match in call_pattern.finditer(text):
            receiver = match.group("receiver")
            method = match.group("method")
            if receiver in {"if", "for", "while", "switch", "new", "this", "super"}:
                continue
            line = self._line(text, match.start())
            if receiver not in fields:
                self._unresolved(snapshot, path, line, match.group(0).strip(), evidences, unresolved)
                continue
            interface, group, version = fields[receiver]
            self._endpoint(
                snapshot,
                path,
                line,
                "consumer",
                "consumer_call",
                interface,
                method,
                group,
                version,
                evidences,
                endpoints,
            )

    def _xml(
        self,
        snapshot: RepositorySnapshot,
        text: str,
        path: str,
        evidences: list[Evidence],
        endpoints: list[RpcEndpoint],
        unresolved: list[UnresolvedFact],
    ) -> None:
        for match in re.finditer(r"<dubbo:(service|reference)\b([^>]*)/?>", text):
            kind, attrs = match.groups()
            line = self._line(text, match.start())
            interface_match = re.search(rf"\binterface\s*=\s*{self._STRING}", attrs)
            if not interface_match:
                self._unresolved(snapshot, path, line, match.group(0), evidences, unresolved)
                continue
            group = self._xml_attr(attrs, "group", "-")
            version = self._xml_attr(attrs, "version", "-")
            if group is None or version is None:
                self._unresolved(snapshot, path, line, match.group(0), evidences, unresolved)
                continue
            self._endpoint(
                snapshot,
                path,
                line,
                "provider" if kind == "service" else "consumer",
                f"xml_{kind}",
                interface_match.group(1),
                "*",
                group,
                version,
                evidences,
                endpoints,
            )

    @staticmethod
    def _class_arg(args: str) -> str | None:
        match = re.search(r"\binterfaceClass\s*=\s*([\w.]+)\.class\b", args)
        return match.group(1) if match else None

    @classmethod
    def _settings(cls, args: str) -> tuple[str, str]:
        def value(key: str) -> str:
            match = re.search(rf"\b{key}\s*=\s*{cls._STRING}", args)
            return match.group(1) if match else "-"

        return value("group"), value("version")

    @classmethod
    def _xml_attr(cls, attrs: str, key: str, default: str) -> str | None:
        match = re.search(rf"\b{key}\s*=\s*{cls._STRING}", attrs)
        if match:
            return match.group(1) or default
        if re.search(rf"\b{key}\s*=", attrs):
            return None
        return default

    @staticmethod
    def _fqcn(value: str, package: str) -> str:
        return value if "." in value or not package else f"{package}.{value}"

    @staticmethod
    def _class_body(text: str, start: int) -> str:
        opening = text.find("{", start)
        if opening < 0:
            return ""
        depth = 0
        for index in range(opening, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return text[opening + 1 : index]
        return text[opening + 1 :]

    @staticmethod
    def _public_methods(body: str) -> list[tuple[str, int]]:
        pattern = re.compile(r"\bpublic\s+(?:static\s+)?[\w<>\[\], ?]+\s+(\w+)\s*\(")
        return [(match.group(1), match.start()) for match in pattern.finditer(body)]

    @staticmethod
    def _line(text: str, index: int) -> int:
        return text.count("\n", 0, index) + 1

    @staticmethod
    def _evidence(
        snapshot: RepositorySnapshot, path: str, line: int, subject: str, evidences: list[Evidence]
    ) -> Evidence:
        evidence = Evidence(
            snapshot.repo_id, snapshot.source_revision, path, line, line, "dubbo", "1", "dubbo", subject, 1.0
        )
        evidences.append(evidence)
        return evidence

    def _endpoint(
        self,
        snapshot: RepositorySnapshot,
        path: str,
        line: int,
        role: str,
        fact_kind: str,
        interface: str,
        method: str,
        group: str,
        version: str,
        evidences: list[Evidence],
        endpoints: list[RpcEndpoint],
    ) -> None:
        evidence = self._evidence(snapshot, path, line, f"{role}|{interface}|{method}", evidences)
        endpoints.append(
            RpcEndpoint(
                snapshot.repo_id,
                f"{group or '-'}:{interface}",
                role,
                fact_kind,
                interface,
                method,
                group,
                version,
                path,
                evidence.id,
                method,
            )
        )

    def _unresolved(
        self,
        snapshot: RepositorySnapshot,
        path: str,
        line: int,
        raw: str,
        evidences: list[Evidence],
        unresolved: list[UnresolvedFact],
    ) -> None:
        evidence = self._evidence(snapshot, path, line, f"unresolved|{raw}", evidences)
        unresolved.append(UnresolvedFact(snapshot.repo_id, path, evidence.id, "UNSUPPORTED_CALL_SHAPE", raw))
