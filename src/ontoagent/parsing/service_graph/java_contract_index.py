"""Protocol-neutral, source-pinned Java interface contract indexing."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType


class ContractSourceRole(StrEnum):
    """A source that may declare an API contract, never a service provider."""

    API = "api"
    CLIENT_MODULE = "client_module"
    SHARED_LIBRARY = "shared_library"


class JavaContractVisibilityStatus(StrEnum):
    VISIBLE = "visible"
    NO_EVIDENCE = "no_evidence"
    VERSION_CONFLICT = "version_conflict"
    CONTRACT_CONFLICT = "contract_conflict"


@dataclass(frozen=True)
class JavaSourceRange:
    repo_id: str
    module_id: str
    source_revision: str
    file_path: str
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        for name in ("repo_id", "module_id", "source_revision", "file_path"):
            _require_nonblank(getattr(self, name), name)
        if not _valid_line_range(self.start_line, self.end_line):
            raise ValueError("invalid source range")


@dataclass(frozen=True)
class JavaContractSource:
    """One pinned repository/module snapshot eligible to declare contracts."""

    repo_id: str
    module_id: str
    source_revision: str
    root_path: Path
    role: ContractSourceRole

    def __post_init__(self) -> None:
        for name in ("repo_id", "module_id", "source_revision"):
            _require_nonblank(getattr(self, name), name)
        if not isinstance(self.root_path, Path):
            object.__setattr__(self, "root_path", Path(self.root_path))
        if type(self.role) is not ContractSourceRole:
            raise ValueError("role must be a ContractSourceRole")

    @property
    def is_provider(self) -> bool:
        return False

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.repo_id, self.module_id, self.source_revision


@dataclass(frozen=True)
class ContractSourceMapping:
    """Explicit evidence authorizing one consumer to inspect one pinned source."""

    consumer_repo_id: str
    consumer_module_id: str
    consumer_source_revision: str
    contract_repo_id: str
    contract_module_id: str
    contract_source_revision: str
    version: str
    evidence_file_path: str
    evidence_start_line: int
    evidence_end_line: int

    def __post_init__(self) -> None:
        for name in (
            "consumer_repo_id",
            "consumer_module_id",
            "consumer_source_revision",
            "contract_repo_id",
            "contract_module_id",
            "contract_source_revision",
            "version",
            "evidence_file_path",
        ):
            _require_nonblank(getattr(self, name), name)
        if not _valid_line_range(self.evidence_start_line, self.evidence_end_line):
            raise ValueError("invalid evidence range")

    @property
    def contract_source_identity(self) -> tuple[str, str, str]:
        return self.contract_repo_id, self.contract_module_id, self.contract_source_revision

    @property
    def evidence(self) -> JavaSourceRange:
        return JavaSourceRange(
            self.consumer_repo_id,
            self.consumer_module_id,
            self.consumer_source_revision,
            self.evidence_file_path,
            self.evidence_start_line,
            self.evidence_end_line,
        )


@dataclass(frozen=True)
class JavaContractMethod:
    declaring_interface_fqcn: str
    name: str
    parameter_types: tuple[str, ...]
    return_type: str
    sources: tuple[JavaSourceRange, ...]

    def __post_init__(self) -> None:
        for name in ("declaring_interface_fqcn", "name", "return_type"):
            _require_nonblank(getattr(self, name), name)
        if type(self.parameter_types) is not tuple or any(not _nonblank(item) for item in self.parameter_types):
            raise ValueError("parameter_types must be a tuple of nonblank strings")
        if (
            type(self.sources) is not tuple
            or not self.sources
            or any(type(item) is not JavaSourceRange for item in self.sources)
        ):
            raise ValueError("sources must be a non-empty tuple of JavaSourceRange values")

    @property
    def canonical_signature(self) -> str:
        return f"{self.declaring_interface_fqcn}#{self.name}({','.join(self.parameter_types)}):{self.return_type}"

    @property
    def source(self) -> JavaSourceRange:
        """Return the first deterministic source while retaining all equivalent sources."""
        return self.sources[0]


@dataclass(frozen=True)
class JavaInterfaceContract:
    fqcn: str
    source_role: ContractSourceRole
    type_parameters: tuple[str, ...]
    parent_interfaces: tuple[str, ...]
    methods: tuple[JavaContractMethod, ...]
    sources: tuple[JavaSourceRange, ...]

    def __post_init__(self) -> None:
        _require_nonblank(self.fqcn, "fqcn")
        if type(self.source_role) is not ContractSourceRole:
            raise ValueError("source_role must be a ContractSourceRole")
        for name, values in (("type_parameters", self.type_parameters), ("parent_interfaces", self.parent_interfaces)):
            if type(values) is not tuple or any(not _nonblank(value) for value in values):
                raise ValueError(f"{name} must be a tuple of nonblank strings")
        if type(self.methods) is not tuple or any(type(item) is not JavaContractMethod for item in self.methods):
            raise ValueError("methods must be a tuple of JavaContractMethod values")
        if (
            type(self.sources) is not tuple
            or not self.sources
            or any(type(item) is not JavaSourceRange for item in self.sources)
        ):
            raise ValueError("sources must be a non-empty tuple of JavaSourceRange values")

    @property
    def source(self) -> JavaContractSourceEvidence:
        """Compatibility view of the first deterministic declaration source."""
        return JavaContractSourceEvidence(self.sources[0], self.source_role)


@dataclass(frozen=True)
class JavaContractSourceEvidence:
    """Source metadata with a non-provider role supplied by the index caller."""

    range: JavaSourceRange
    role: ContractSourceRole = ContractSourceRole.API

    @property
    def repo_id(self) -> str:
        return self.range.repo_id

    @property
    def file_path(self) -> str:
        return self.range.file_path

    @property
    def is_provider(self) -> bool:
        return False


@dataclass(frozen=True)
class JavaContractConflict:
    fqcn: str
    sources: tuple[JavaSourceRange, ...]
    signatures_by_source: tuple[tuple[JavaSourceRange, tuple[str, ...]], ...]


@dataclass(frozen=True)
class JavaContractIndexResult:
    contracts: Mapping[str, JavaInterfaceContract]
    conflicts: tuple[JavaContractConflict, ...]
    source_roles: tuple[tuple[tuple[str, str, str], ContractSourceRole], ...]


@dataclass(frozen=True)
class JavaContractVisibilityResult:
    status: JavaContractVisibilityStatus
    contracts: tuple[JavaInterfaceContract, ...]
    mappings: tuple[ContractSourceMapping, ...]
    conflicts: tuple[JavaContractConflict, ...] = ()


class JavaContractIndex:
    """Extract and index local Java interfaces without protocol/provider semantics."""

    def build(self, sources: tuple[JavaContractSource, ...]) -> JavaContractIndexResult:
        if type(sources) is not tuple or any(type(source) is not JavaContractSource for source in sources):
            raise ValueError("sources must be a tuple of JavaContractSource values")
        if len({source.identity for source in sources}) != len(sources):
            raise ValueError("sources must have unique pinned identities")
        declarations = [contract for source in sources for contract in self._extract(source)]
        grouped: dict[str, list[JavaInterfaceContract]] = {}
        for declaration in declarations:
            grouped.setdefault(declaration.fqcn, []).append(declaration)
        contracts: dict[str, JavaInterfaceContract] = {}
        conflicts: list[JavaContractConflict] = []
        for fqcn, items in sorted(grouped.items()):
            signatures = {tuple(method.canonical_signature for method in item.methods) for item in items}
            if len(signatures) != 1:
                conflicts.append(self._conflict(fqcn, items))
                continue
            contracts[fqcn] = self._merge(fqcn, items)
        roles = tuple(sorted(((source.identity, source.role) for source in sources), key=lambda item: item[0]))
        return JavaContractIndexResult(MappingProxyType(contracts), tuple(conflicts), roles)

    def visible_contracts(
        self,
        result: JavaContractIndexResult,
        consumer_repo_id: str,
        consumer_module_id: str,
        consumer_source_revision: str,
        version: str,
        mappings: tuple[ContractSourceMapping, ...] = (),
        interface_fqcn: str | None = None,
    ) -> JavaContractVisibilityResult:
        if type(result) is not JavaContractIndexResult:
            raise ValueError("result must be a JavaContractIndexResult")
        for name, value in (
            ("consumer_repo_id", consumer_repo_id),
            ("consumer_module_id", consumer_module_id),
            ("consumer_source_revision", consumer_source_revision),
            ("version", version),
        ):
            _require_nonblank(value, name)
        if interface_fqcn is not None:
            _require_nonblank(interface_fqcn, "interface_fqcn")
        if type(mappings) is not tuple or any(type(item) is not ContractSourceMapping for item in mappings):
            raise ValueError("mappings must be a tuple of ContractSourceMapping values")
        applicable = tuple(
            sorted(
                (
                    item
                    for item in mappings
                    if (
                        item.consumer_repo_id,
                        item.consumer_module_id,
                        item.consumer_source_revision,
                    )
                    == (consumer_repo_id, consumer_module_id, consumer_source_revision)
                ),
                key=_mapping_key,
            )
        )
        known_sources = {identity for identity, _ in result.source_roles}
        authorized = tuple(item for item in applicable if item.contract_source_identity in known_sources)
        selected = tuple(item for item in authorized if item.version == version)
        allowed = {item.contract_source_identity for item in selected}
        contracts = tuple(
            contract
            for _, contract in sorted(result.contracts.items())
            if interface_fqcn in {None, contract.fqcn}
            and any(
                (source.repo_id, source.module_id, source.source_revision) in allowed for source in contract.sources
            )
        )
        conflicts = tuple(
            conflict
            for conflict in result.conflicts
            if interface_fqcn in {None, conflict.fqcn}
            and any(
                (source.repo_id, source.module_id, source.source_revision) in allowed for source in conflict.sources
            )
        )
        if conflicts:
            return JavaContractVisibilityResult(JavaContractVisibilityStatus.CONTRACT_CONFLICT, (), selected, conflicts)
        if contracts:
            return JavaContractVisibilityResult(JavaContractVisibilityStatus.VISIBLE, contracts, selected)
        has_other_version = any(item.version != version for item in authorized)
        status = (
            JavaContractVisibilityStatus.VERSION_CONFLICT
            if has_other_version
            else JavaContractVisibilityStatus.NO_EVIDENCE
        )
        return JavaContractVisibilityResult(status, (), selected)

    def _extract(self, source: JavaContractSource) -> tuple[JavaInterfaceContract, ...]:
        result: list[JavaInterfaceContract] = []
        for path in sorted(source.root_path.rglob("*.java")):
            text = path.read_text(encoding="utf-8")
            relative = path.relative_to(source.root_path).as_posix()
            result.extend(_extract_interfaces(source, relative, text))
        return tuple(result)

    @staticmethod
    def _merge(fqcn: str, declarations: list[JavaInterfaceContract]) -> JavaInterfaceContract:
        ordered = sorted(declarations, key=lambda item: _range_key(item.sources[0]))
        source_ranges = _unique_ranges(source for item in ordered for source in item.sources)
        by_signature: dict[str, list[JavaSourceRange]] = {}
        prototypes: dict[str, JavaContractMethod] = {}
        for item in ordered:
            for method in item.methods:
                prototypes.setdefault(method.canonical_signature, method)
                by_signature.setdefault(method.canonical_signature, []).extend(method.sources)
        methods = tuple(
            JavaContractMethod(
                prototype.declaring_interface_fqcn,
                prototype.name,
                prototype.parameter_types,
                prototype.return_type,
                _unique_ranges(by_signature[signature]),
            )
            for signature, prototype in sorted(prototypes.items())
        )
        return JavaInterfaceContract(
            fqcn,
            ordered[0].source_role,
            ordered[0].type_parameters,
            ordered[0].parent_interfaces,
            methods,
            source_ranges,
        )

    @staticmethod
    def _conflict(fqcn: str, declarations: list[JavaInterfaceContract]) -> JavaContractConflict:
        ordered = sorted(declarations, key=lambda item: _range_key(item.sources[0]))
        entries = tuple(
            (item.sources[0], tuple(method.canonical_signature for method in item.methods)) for item in ordered
        )
        return JavaContractConflict(
            fqcn, _unique_ranges(source for item in ordered for source in item.sources), entries
        )


_PACKAGE = re.compile(r"\bpackage\s+([\w.]+)\s*;")
_IMPORT = re.compile(r"\bimport\s+([\w.]+)\s*;")
_INTERFACE = re.compile(
    r"\b(?:public\s+)?interface\s+(?P<name>\w+)(?P<generics>\s*<[^>{}]+>)?"
    r"(?:\s+extends\s+(?P<parents>[^\{]+))?\s*\{"
)
_METHOD = re.compile(
    r"(?:@[\w.]+(?:\s*\([^;{}]*\))?\s*)*"
    r"(?:public\s+|private\s+|default\s+|static\s+|abstract\s+)*"
    r"(?P<return>[\w.$<>?,\[\]\s]+?)\s+(?P<name>\w+)\s*\((?P<params>[^(){};]*)\)\s*;"
)
_JAVA_LANG = frozenset({"String", "Object", "Boolean", "Integer", "Long", "Double", "Float", "Void"})
_PRIMITIVES = frozenset({"boolean", "byte", "char", "short", "int", "long", "float", "double", "void"})


def _extract_interfaces(source: JavaContractSource, path: str, text: str) -> tuple[JavaInterfaceContract, ...]:
    package = _PACKAGE.search(text)
    package_name = package.group(1) if package else ""
    imports = {value.rsplit(".", 1)[-1]: value for value in _IMPORT.findall(text)}
    result: list[JavaInterfaceContract] = []
    for match in _INTERFACE.finditer(text):
        closing = _matching_brace(text, match.end() - 1)
        if closing is None:
            continue
        fqcn = _qualified(match.group("name"), package_name, imports)
        interface_range = _source_range(source, path, text, match.start(), closing)
        body_start = match.end()
        body = text[body_start:closing]
        methods = tuple(
            _extract_method(source, path, text, body_start, item, fqcn, package_name, imports)
            for item in _METHOD.finditer(body)
        )
        parents = tuple(
            _qualified_type(parent.strip(), package_name, imports)
            for parent in (match.group("parents") or "").split(",")
            if parent.strip()
        )
        generics = tuple(
            item.strip() for item in (match.group("generics") or "").strip(" <>").split(",") if item.strip()
        )
        result.append(
            JavaInterfaceContract(
                fqcn,
                source.role,
                generics,
                parents,
                tuple(sorted(methods, key=lambda item: item.canonical_signature)),
                (interface_range,),
            )
        )
    return tuple(result)


def _extract_method(
    source: JavaContractSource,
    path: str,
    text: str,
    body_start: int,
    match: re.Match[str],
    fqcn: str,
    package_name: str,
    imports: dict[str, str],
) -> JavaContractMethod:
    parameters = tuple(
        _qualified_type(_parameter_type(item), package_name, imports)
        for item in match.group("params").split(",")
        if item.strip()
    )
    return JavaContractMethod(
        fqcn,
        match.group("name"),
        parameters,
        _qualified_type(match.group("return").strip(), package_name, imports),
        (_source_range(source, path, text, body_start + match.start(), body_start + match.end() - 1),),
    )


def _parameter_type(value: str) -> str:
    stripped = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", value).strip()
    pieces = stripped.rsplit(None, 1)
    return pieces[0] if len(pieces) == 2 else stripped


def _qualified_type(value: str, package_name: str, imports: dict[str, str]) -> str:
    value = value.strip().replace("...", "[]")
    generic = re.fullmatch(r"(?P<base>[\w.$]+)\s*<(?P<arguments>.*)>", value)
    if generic:
        arguments = ",".join(
            _qualified_type(item, package_name, imports)
            for item in _split_generic_arguments(generic.group("arguments"))
        )
        return f"{_qualified(generic.group('base'), package_name, imports)}<{arguments}>"
    suffix = ""
    while value.endswith("[]"):
        value, suffix = value[:-2].strip(), f"[]{suffix}"
    return f"{_qualified(value, package_name, imports)}{suffix}"


def _qualified(value: str, package_name: str, imports: dict[str, str]) -> str:
    if value in _PRIMITIVES or value == "?":
        return value
    if value in _JAVA_LANG:
        return f"java.lang.{value}"
    if "." in value:
        return value
    return imports.get(value, f"{package_name}.{value}" if package_name else value)


def _split_generic_arguments(value: str) -> tuple[str, ...]:
    result: list[str] = []
    start, depth = 0, 0
    for index, char in enumerate(value):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(value[start:index].strip())
            start = index + 1
    result.append(value[start:].strip())
    return tuple(item for item in result if item)


def _matching_brace(text: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _source_range(source: JavaContractSource, path: str, text: str, start: int, end: int) -> JavaSourceRange:
    return JavaSourceRange(
        source.repo_id,
        source.module_id,
        source.source_revision,
        path,
        text.count("\n", 0, start) + 1,
        text.count("\n", 0, end) + 1,
    )


def _unique_ranges(ranges: Iterable[JavaSourceRange]) -> tuple[JavaSourceRange, ...]:
    return tuple(sorted(set(ranges), key=_range_key))


def _range_key(value: JavaSourceRange) -> tuple[str, str, str, str, int, int]:
    return value.repo_id, value.module_id, value.source_revision, value.file_path, value.start_line, value.end_line


def _mapping_key(value: ContractSourceMapping) -> tuple[object, ...]:
    return (
        value.contract_repo_id,
        value.contract_module_id,
        value.contract_source_revision,
        value.version,
        value.evidence_file_path,
        value.evidence_start_line,
        value.evidence_end_line,
    )


def _valid_line_range(start_line: int, end_line: int) -> bool:
    return all(type(item) is int for item in (start_line, end_line)) and start_line >= 1 and end_line >= start_line


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_nonblank(value: object, name: str) -> None:
    if not _nonblank(value):
        raise ValueError(f"{name} must be nonblank")
