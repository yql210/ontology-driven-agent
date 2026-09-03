from __future__ import annotations

import re

from ontoagent.parsing.service_graph.models import (
    DetectorFacts,
    Evidence,
    MessageEndpoint,
    RepositorySnapshot,
    UnresolvedFact,
)


class MessagingDetector:
    """Read-only detector for the frozen Kafka and RabbitMQ Java shapes."""

    id = "messaging"
    version = "1"
    supported_languages = frozenset({"java", "yaml"})
    _STRING = r'"([^"\\]*(?:\\.[^"\\]*)*)"'

    def detect(self, snapshot: RepositorySnapshot) -> DetectorFacts:
        evidences: list[Evidence] = []
        endpoints: list[MessageEndpoint] = []
        unresolved: list[UnresolvedFact] = []
        for path in sorted(snapshot.root_path.rglob("*.java")):
            text = path.read_text(encoding="utf-8")
            relative = path.relative_to(snapshot.root_path).as_posix()
            self._annotations(snapshot, text, relative, evidences, endpoints, unresolved)
            self._producers(snapshot, text, relative, evidences, endpoints, unresolved)
        return DetectorFacts(
            self.id,
            self.version,
            snapshot.repo_id,
            snapshot.source_revision,
            (),
            (),
            tuple(evidences),
            tuple(unresolved),
            message_endpoints=tuple(endpoints),
        )

    def _annotations(
        self,
        snapshot: RepositorySnapshot,
        text: str,
        path: str,
        evidences: list[Evidence],
        endpoints: list[MessageEndpoint],
        unresolved: list[UnresolvedFact],
    ) -> None:
        pattern = re.compile(r"@(?P<kind>KafkaListener|RabbitListener)\b(?:\s*\((?P<args>[^)]*)\))?")
        for match in pattern.finditer(text):
            args = match.group("args") or ""
            broker = "kafka" if match.group("kind") == "KafkaListener" else "rabbitmq"
            target_name = "topics" if broker == "kafka" else "queues"
            group_name = "groupId" if broker == "kafka" else "group"
            target_expr = self._named(args, target_name)
            targets = self._string_values(target_expr) if target_expr is not None else []
            line = self._line(text, match.start())
            if not targets:
                reason = (
                    "DYNAMIC_URL"
                    if target_expr is not None and target_expr.strip() not in {"{}", ""}
                    else "UNSUPPORTED_CALL_SHAPE"
                )
                self._unresolved(snapshot, path, line, match.group(0), reason, evidences, unresolved)
                continue
            group_expr = self._named(args, group_name)
            group_values = self._string_values(group_expr) if group_expr is not None else ["-"]
            group = group_values[0] if len(group_values) == 1 else "-"
            for target in targets:
                self._endpoint(
                    snapshot, path, line, broker, "consumer", target, group, match.group(0), evidences, endpoints
                )

    def _producers(
        self,
        snapshot: RepositorySnapshot,
        text: str,
        path: str,
        evidences: list[Evidence],
        endpoints: list[MessageEndpoint],
        unresolved: list[UnresolvedFact],
    ) -> None:
        declarations = {
            name: typ
            for typ, name in re.findall(
                r"\b(?P<type>(?:KafkaTemplate|RabbitTemplate)(?:\s*<[^;{}>]+>)?)\s+(?P<name>[A-Za-z_]\w*)\s*(?:=|;)",
                text,
            )
        }
        calls = re.compile(
            r"\b(?P<receiver>[A-Za-z_]\w*)\s*\.\s*(?P<method>send|convertAndSend)\s*\((?P<args>[^;\n]*)\)"
        )
        for match in calls.finditer(text):
            receiver, method = match.group("receiver"), match.group("method")
            typ = declarations.get(receiver, "")
            broker = (
                "kafka"
                if typ.startswith("KafkaTemplate") and method == "send"
                else "rabbitmq"
                if typ.startswith("RabbitTemplate") and method == "convertAndSend"
                else None
            )
            if broker is None:
                continue
            args = self._split_args(match.group("args"))
            line = self._line(text, match.start())
            expected = 2 if broker == "kafka" else 3
            if len(args) != expected:
                self._unresolved(snapshot, path, line, match.group(0), "UNSUPPORTED_CALL_SHAPE", evidences, unresolved)
                continue
            target = self._literal(args[0])
            if target is None:
                reason = "DYNAMIC_URL" if args[0].strip() else "UNSUPPORTED_CALL_SHAPE"
                self._unresolved(snapshot, path, line, match.group(0), reason, evidences, unresolved)
                continue
            self._endpoint(snapshot, path, line, broker, "producer", target, "-", match.group(0), evidences, endpoints)

    @classmethod
    def _named(cls, args: str, name: str) -> str | None:
        match = re.search(rf"\b{name}\s*=\s*(?P<value>\{{[^}}]*\}}|{cls._STRING}|[^,]+)", args)
        return match.group("value").strip() if match else None

    @classmethod
    def _string_values(cls, expression: str | None) -> list[str]:
        if expression is None:
            return []
        return [m.group(1) for m in re.finditer(cls._STRING, expression)]

    @classmethod
    def _literal(cls, expression: str) -> str | None:
        match = re.fullmatch(rf"\s*{cls._STRING}\s*", expression)
        return match.group(1) if match else None

    @staticmethod
    def _split_args(args: str) -> list[str]:
        parts, start, depth, quoted = [], 0, 0, False
        for index, char in enumerate(args):
            if char == '"' and (index == 0 or args[index - 1] != "\\"):
                quoted = not quoted
            elif not quoted and char in "([{":
                depth += 1
            elif not quoted and char in ")]}":
                depth -= 1
            elif not quoted and char == "," and depth == 0:
                parts.append(args[start:index].strip())
                start = index + 1
        if args.strip():
            parts.append(args[start:].strip())
        return parts

    @staticmethod
    def _line(text: str, index: int) -> int:
        return text.count("\n", 0, index) + 1

    @staticmethod
    def _evidence(
        snapshot: RepositorySnapshot, path: str, line: int, subject: str, evidences: list[Evidence]
    ) -> Evidence:
        evidence = Evidence(
            snapshot.repo_id, snapshot.source_revision, path, line, line, "messaging", "1", "messaging", subject, 1.0
        )
        evidences.append(evidence)
        return evidence

    def _endpoint(
        self,
        snapshot: RepositorySnapshot,
        path: str,
        line: int,
        broker: str,
        role: str,
        target: str,
        group: str,
        raw: str,
        evidences: list[Evidence],
        endpoints: list[MessageEndpoint],
    ) -> None:
        evidence = self._evidence(snapshot, path, line, f"{broker}|{role}|{target}|{group}", evidences)
        endpoints.append(MessageEndpoint(snapshot.repo_id, broker, role, target, group, path, evidence.id, raw))

    def _unresolved(
        self,
        snapshot: RepositorySnapshot,
        path: str,
        line: int,
        raw: str,
        reason: str,
        evidences: list[Evidence],
        unresolved: list[UnresolvedFact],
    ) -> None:
        evidence = self._evidence(snapshot, path, line, f"unresolved|{reason}|{raw}", evidences)
        unresolved.append(UnresolvedFact(snapshot.repo_id, path, evidence.id, reason, raw))
