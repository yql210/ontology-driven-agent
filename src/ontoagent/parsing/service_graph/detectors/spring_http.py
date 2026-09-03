from __future__ import annotations

import re
from urllib.parse import urlparse

from ontoagent.parsing.service_graph.models import (
    DetectorFacts,
    Evidence,
    HttpEndpoint,
    RepositorySnapshot,
    ServiceDefinition,
    UnresolvedFact,
    _normalize_path,
)


class SpringHttpDetector:
    id = "spring-http"
    version = "1"
    supported_languages = frozenset({"java", "yaml"})
    _HTTP_METHODS = {"GET", "POST", "PUT", "DELETE"}

    def detect(self, snapshot: RepositorySnapshot) -> DetectorFacts:
        evidences: list[Evidence] = []
        services: list[ServiceDefinition] = []
        endpoints: list[HttpEndpoint] = []
        unresolved: list[UnresolvedFact] = []
        configs = self._read_configs(snapshot, evidences)

        for path in sorted(snapshot.root_path.rglob("*.java")):
            text = path.read_text(encoding="utf-8")
            relative_path = path.relative_to(snapshot.root_path).as_posix()
            self._detect_provider(
                snapshot,
                text,
                relative_path,
                evidences,
                services,
                endpoints,
                unresolved,
            )
            self._detect_consumer(
                snapshot,
                text,
                relative_path,
                configs,
                evidences,
                endpoints,
                unresolved,
            )

        return DetectorFacts(
            detector_id=self.id,
            detector_version=self.version,
            repo_id=snapshot.repo_id,
            source_revision=snapshot.source_revision,
            services=tuple(services),
            http_endpoints=tuple(endpoints),
            evidences=tuple(evidences),
            unresolved=tuple(unresolved),
            evidence_links=tuple(evidence.id for evidence in evidences if evidence.evidence_type == "service_config"),
            endpoint_evidence_links=tuple(
                (
                    endpoint.canonical_key,
                    (configs[endpoint.service_name][1],)
                    if endpoint.role == "consumer" and endpoint.service_name in configs
                    else (),
                )
                for endpoint in sorted(endpoints, key=lambda value: value.canonical_key)
                if endpoint.role == "consumer" and endpoint.service_name in configs
            ),
        )

    def _read_configs(
        self,
        snapshot: RepositorySnapshot,
        evidences: list[Evidence],
    ) -> dict[str, tuple[str, str]]:
        configs: dict[str, tuple[str, str]] = {}
        pattern = re.compile(r"^\s*([\w.-]+\.base-url):\s*['\"]?([^'\"\s]+)", re.MULTILINE)
        for path in sorted(snapshot.root_path.rglob("*.y*ml")):
            text = path.read_text(encoding="utf-8")
            relative_path = path.relative_to(snapshot.root_path).as_posix()
            for match in pattern.finditer(text):
                key = match.group(1)[:-9]
                value = match.group(2)
                line = text.count("\n", 0, match.start()) + 1
                evidence = Evidence(
                    snapshot.repo_id,
                    snapshot.source_revision,
                    relative_path,
                    line,
                    line,
                    self.id,
                    self.version,
                    "service_config",
                    f"config|{key}|{value}",
                    1.0,
                )
                evidences.append(evidence)
                configs[key] = (value, evidence.id)
        return configs

    def _detect_provider(
        self,
        snapshot: RepositorySnapshot,
        text: str,
        relative_path: str,
        evidences: list[Evidence],
        services: list[ServiceDefinition],
        endpoints: list[HttpEndpoint],
        unresolved: list[UnresolvedFact],
    ) -> None:
        package_match = re.search(r"\bpackage\s+([\w.]+)\s*;", text)
        package = package_match.group(1) if package_match else "service"
        class_pattern = re.compile(
            r"(?P<annotation>@RequestMapping\s*(?:\((?P<args>.*?)\))?)"
            r"\s*(?:public\s+)?class\s+(?P<name>\w+)",
            re.DOTALL,
        )
        for class_match in class_pattern.finditer(text):
            class_name = class_match.group("name")
            service_name = f"{package.rsplit('.', 1)[-1]}:{re.sub(r'Controller$', '', class_name)}"
            class_line = text.count("\n", 0, class_match.start("annotation")) + 1
            class_args = class_match.group("args") or ""
            class_paths = self._paths(class_args) or ["/"]
            class_evidence = self._evidence(
                snapshot,
                relative_path,
                class_line,
                class_line,
                "provider_mapping",
                f"provider|{service_name}|class",
                evidences,
            )
            services.append(ServiceDefinition(snapshot.repo_id, service_name, "spring", class_evidence.id))

            body_start = class_match.end()
            body_end = self._matching_brace(text, text.find("{", body_start))
            class_body = text[body_start:body_end] if body_end >= 0 else text[body_start:]
            method_pattern = re.compile(
                r"(?P<annotation>@(?P<kind>RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)"
                r"\s*(?:\((?P<args>.*?)\))?)\s*"
                r"(?:public\s+|private\s+|protected\s+)?[\w<>\[\], ?]+\s+\w+\s*\(",
                re.DOTALL,
            )
            for method_match in method_pattern.finditer(class_body):
                args = method_match.group("args") or ""
                kind = method_match.group("kind")
                paths = self._paths(args) or ["/"]
                methods = self._methods(args)
                if kind != "RequestMapping":
                    methods = [kind.removesuffix("Mapping").upper()]
                if not methods:
                    unresolved_line = text.count("\n", 0, body_start + method_match.start()) + 1
                    unresolved_evidence = self._evidence(
                        snapshot,
                        relative_path,
                        unresolved_line,
                        unresolved_line,
                        "provider_mapping",
                        f"unresolved|AMBIGUOUS_HTTP_METHOD|{kind}",
                        evidences,
                    )
                    unresolved.append(
                        UnresolvedFact(
                            snapshot.repo_id,
                            relative_path,
                            unresolved_evidence.id,
                            "AMBIGUOUS_HTTP_METHOD",
                            kind,
                        )
                    )
                    continue
                line = text.count("\n", 0, body_start + method_match.start("annotation")) + 1
                for class_path in class_paths:
                    for method_path in paths:
                        normalized_path = _normalize_path(f"{class_path}/{method_path}")
                        for method in methods:
                            evidence = self._evidence(
                                snapshot,
                                relative_path,
                                line,
                                line,
                                "provider_mapping",
                                f"provider|{service_name}|{method}|{normalized_path}",
                                evidences,
                            )
                            endpoints.append(
                                HttpEndpoint(
                                    snapshot.repo_id,
                                    service_name,
                                    "provider",
                                    "provider_mapping",
                                    method,
                                    normalized_path,
                                    relative_path,
                                    evidence.id,
                                )
                            )

    def _detect_consumer(
        self,
        snapshot: RepositorySnapshot,
        text: str,
        relative_path: str,
        configs: dict[str, tuple[str, str]],
        evidences: list[Evidence],
        endpoints: list[HttpEndpoint],
        unresolved: list[UnresolvedFact],
    ) -> None:
        call_pattern = re.compile(r"(?P<method>getForObject|postForObject|put|delete)\s*\((?P<args>[^;]*?)\)")
        for match in call_pattern.finditer(text):
            args = match.group("args")
            first_arg = args.split(",", 1)[0].strip()
            if not first_arg:
                continue
            line = text.count("\n", 0, match.start()) + 1
            method = {
                "getForObject": "GET",
                "postForObject": "POST",
                "put": "PUT",
                "delete": "DELETE",
            }[match.group("method")]
            if not (len(first_arg) >= 2 and first_arg[0] == first_arg[-1] == '"'):
                evidence = self._evidence(
                    snapshot,
                    relative_path,
                    line,
                    line,
                    "consumer_call",
                    f"unresolved|DYNAMIC_URL|{first_arg}",
                    evidences,
                )
                unresolved.append(
                    UnresolvedFact(snapshot.repo_id, relative_path, evidence.id, "DYNAMIC_URL", first_arg)
                )
                continue
            self._add_consumer_endpoint(
                snapshot,
                relative_path,
                line,
                method,
                first_arg[1:-1],
                "RestTemplate",
                configs,
                evidences,
                endpoints,
                unresolved,
            )

        webclient_pattern = re.compile(r"\.(?P<method>get|post|put|delete)\s*\(\s*\)\s*\.\s*uri\s*\((?P<args>[^;]*?)\)")
        for match in webclient_pattern.finditer(text):
            args = match.group("args").strip()
            line = text.count("\n", 0, match.start()) + 1
            method = match.group("method").upper()
            if "," in args:
                reason = "UNSUPPORTED_CALL_SHAPE"
                evidence = self._evidence(
                    snapshot,
                    relative_path,
                    line,
                    line,
                    "consumer_call",
                    f"unresolved|{reason}|{args}",
                    evidences,
                )
                unresolved.append(UnresolvedFact(snapshot.repo_id, relative_path, evidence.id, reason, args))
                continue
            if not (len(args) >= 2 and args[0] == args[-1] == '"'):
                evidence = self._evidence(
                    snapshot,
                    relative_path,
                    line,
                    line,
                    "consumer_call",
                    f"unresolved|DYNAMIC_URL|{args}",
                    evidences,
                )
                unresolved.append(UnresolvedFact(snapshot.repo_id, relative_path, evidence.id, "DYNAMIC_URL", args))
                continue
            self._add_consumer_endpoint(
                snapshot,
                relative_path,
                line,
                method,
                args[1:-1],
                "WebClient",
                configs,
                evidences,
                endpoints,
                unresolved,
            )

    def _add_consumer_endpoint(
        self,
        snapshot: RepositorySnapshot,
        relative_path: str,
        line: int,
        method: str,
        url: str,
        client_kind: str,
        configs: dict[str, tuple[str, str]],
        evidences: list[Evidence],
        endpoints: list[HttpEndpoint],
        unresolved: list[UnresolvedFact],
    ) -> None:
        parsed = urlparse(url)
        config = next(
            ((name, value) for name, value in configs.items() if urlparse(value[0]).netloc == parsed.netloc),
            None,
        )
        service_name = config[0] if config else parsed.netloc
        if config is None and not parsed.netloc:
            reason = "UNMAPPED_SERVICE_BASE"
            evidence = self._evidence(
                snapshot,
                relative_path,
                line,
                line,
                "consumer_call",
                f"unresolved|{reason}|{url}",
                evidences,
            )
            unresolved.append(UnresolvedFact(snapshot.repo_id, relative_path, evidence.id, reason, url))
            return
        evidence = self._evidence(
            snapshot,
            relative_path,
            line,
            line,
            "consumer_call",
            f"consumer|{service_name}|{method}|{_normalize_path(parsed.path)}|{client_kind}",
            evidences,
        )
        endpoints.append(
            HttpEndpoint(
                snapshot.repo_id,
                service_name,
                "consumer",
                "consumer_call",
                method,
                parsed.path or "/",
                relative_path,
                evidence.id,
                client_kind,
                url,
            )
        )

    def _evidence(
        self,
        snapshot: RepositorySnapshot,
        file_path: str,
        start_line: int,
        end_line: int,
        evidence_type: str,
        subject: str,
        evidences: list[Evidence],
    ) -> Evidence:
        evidence = Evidence(
            snapshot.repo_id,
            snapshot.source_revision,
            file_path,
            start_line,
            end_line,
            self.id,
            self.version,
            evidence_type,
            subject,
            1.0,
        )
        evidences.append(evidence)
        return evidence

    @staticmethod
    def _paths(args: str) -> list[str]:
        match = re.search(r"(?:path|value)\s*=\s*(\{[^}]*\}|\"[^\"]*\")", args)
        if not match:
            bare = re.match(r"\s*(\"[^\"]*\")", args)
            return [bare.group(1)[1:-1]] if bare else []
        value = match.group(1)
        if value.startswith("{"):
            return re.findall(r'"([^" ]*)"', value)
        return [value[1:-1]]

    def _methods(self, args: str) -> list[str]:
        return re.findall(r"RequestMethod\.(GET|POST|PUT|DELETE)", args)

    @staticmethod
    def _matching_brace(text: str, opening: int) -> int:
        if opening < 0:
            return -1
        depth = 0
        for index in range(opening, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return index
        return -1
