from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ChangeType(Enum):
    """变更类型枚举。"""

    ADDED = "added"
    DELETED = "deleted"
    SIGNATURE = "signature"
    BODY = "body"
    DOC_ONLY = "doc_only"


class GitStatus(Enum):
    """git diff --name-status 的状态码。"""

    ADDED = "A"
    DELETED = "D"
    MODIFIED = "M"
    RENAMED = "R"
    COPIED = "C"
    TYPE_CHANGE = "T"
    UNMERGED = "U"
    UNKNOWN = "X"


@dataclass
class ChangedFile:
    """变更文件描述。

    Attributes:
        path: 文件相对路径（相对于仓库根目录）。
        change_type: 变更类型分类。
        git_status: git 原始状态码。
        old_sha256: 变更前的 SHA256 哈希（新增文件为 None）。
        new_sha256: 变更后的 SHA256 哈希（删除文件为 None）。
        old_content: 变更前的文件内容字节（可选）。
        new_content: 变更后的文件内容字节（可选）。
    """

    path: str
    change_type: ChangeType
    git_status: GitStatus
    old_sha256: str | None = None
    new_sha256: str | None = None
    old_content: bytes | None = None
    new_content: bytes | None = None

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.path or not self.path.strip():
            raise ValueError("ChangedFile.path cannot be empty")
        if not isinstance(self.change_type, ChangeType):
            raise TypeError(f"ChangedFile.change_type must be ChangeType, got {type(self.change_type)}")
        if not isinstance(self.git_status, GitStatus):
            raise TypeError(f"ChangedFile.git_status must be GitStatus, got {type(self.git_status)}")


@dataclass
class _CacheEntry:
    """缓存条目。"""

    sha256: str
    content: bytes | None = None


class SHA256Cache:
    """文件内容 SHA256 缓存，用于快速判断文件是否变更。

    内部使用 dict 存储路径→哈希映射，可持久化到 JSON 文件。
    """

    def __init__(self, cache_file: Path | None = None) -> None:
        """初始化缓存。

        Args:
            cache_file: 缓存持久化文件路径。为 None 则仅内存缓存。
        """
        self._cache_file = cache_file
        self._entries: dict[str, _CacheEntry] = {}
        self._logger = logging.getLogger(__name__)
        if cache_file and cache_file.exists():
            self._load()

    def get(self, path: str) -> str | None:
        """获取文件路径对应的 SHA256 哈希。"""
        return self._entries[path].sha256 if path in self._entries else None

    def set(self, path: str, sha256: str, content: bytes | None = None) -> None:
        """设置文件路径的 SHA256 哈希。"""
        self._entries[path] = _CacheEntry(sha256=sha256, content=content)

    def has_changed(self, path: str, current_sha256: str) -> bool:
        """判断文件的 SHA256 是否与缓存不同。"""
        cached = self.get(path)
        return cached != current_sha256

    def remove(self, path: str) -> None:
        """移除缓存条目。"""
        self._entries.pop(path, None)

    def save(self) -> None:
        """持久化缓存到文件。"""
        if self._cache_file is None:
            return
        data = {path: entry.sha256 for path, entry in self._entries.items()}
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._cache_file.write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        """从文件加载缓存。"""
        try:
            data = json.loads(self._cache_file.read_text())  # type: ignore[union-attr]
            self._entries = {path: _CacheEntry(sha256=sha256) for path, sha256 in data.items()}
        except (OSError, json.JSONDecodeError) as e:
            self._logger.warning("Failed to load cache: %s", e)

    @property
    def size(self) -> int:
        """缓存条目数量。"""
        return len(self._entries)


class GitChangeDetector:
    """Git 变更检测器。

    通过 git diff + SHA256 缓存检测代码变更，
    结合 diff hunks 分析判断变更粒度（签名级 vs 函数体级 vs 仅文档）。
    """

    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".py"})

    def __init__(
        self,
        repo_path: Path,
        cache: SHA256Cache | None = None,
        supported_extensions: frozenset[str] | None = None,
    ) -> None:
        """初始化变更检测器。

        Args:
            repo_path: Git 仓库根目录路径。
            cache: SHA256 缓存实例。为 None 则自动创建内存缓存。
            supported_extensions: 支持的文件扩展名集合。

        Raises:
            ValueError: repo_path 不是目录。
        """
        if not repo_path.is_dir():
            raise ValueError(f"repo_path must be a directory: {repo_path}")
        self._repo_path = repo_path
        self._cache = cache or SHA256Cache()
        self._extensions = supported_extensions or self.SUPPORTED_EXTENSIONS
        self._logger = logging.getLogger(__name__)

    def _parse_git_status(self, status_code: str) -> GitStatus:
        """解析 git status code 为 GitStatus 枚举。"""
        code = status_code[0] if status_code else "X"
        for gs in GitStatus:
            if gs.value == code:
                return gs
        return GitStatus.UNKNOWN

    def _is_supported(self, file_path: str) -> bool:
        """判断文件扩展名是否受支持。"""
        return Path(file_path).suffix in self._extensions

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        """计算文件的 SHA256 哈希。"""
        content = file_path.read_bytes()
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _is_doc_line(content: str) -> bool:
        """判断一行内容是否是文档/注释行。

        匹配规则：
        - # 开头的注释
        - \"\"\"...\"\"\" 或 '''...''' 单行 docstring（长度>7，有内容）
        - \"\"\" 或 ''' 开头（docstring 开始，但不是完整的单行）
        - \"\"\" 或 ''' 结尾（docstring 结束，但不是完整的单行）
        """
        if not content:
            return False
        # # 开头的注释
        if content.startswith("#"):
            return True
        # docstring 开标记或闭标记
        starts_dq = content.startswith('"""')
        starts_sq = content.startswith("'''")
        ends_dq = content.endswith('"""')
        ends_sq = content.endswith("'''")

        # 单行完整 docstring: """text""" 或 '''text''' (长度 > 7，必须有实际内容)
        if (starts_dq and ends_dq) or (starts_sq and ends_sq):
            return len(content) > 7  # 排除 """""" 和 """x""" 等极短情况，至少 """xy"""
        # docstring 开标记（非单行）
        if starts_dq or starts_sq:
            return True
        # docstring 闭标记（非单行）
        return bool(ends_dq or ends_sq)

    def _analyze_diff_hunks(self, diff_text: str) -> ChangeType:
        """分析 diff hunks 判断变更类型。

        策略：
        - def/class/async def 行变更 → SIGNATURE
        - 注释(#)、docstring 开闭标记 → DOC_ONLY
        - 其他代码变更 → BODY

        Args:
            diff_text: git diff 输出文本。

        Returns:
            变更类型。
        """
        has_signature_change = False
        has_body_change = False

        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
                continue
            if line.startswith("+") or line.startswith("-"):
                content = line[1:].strip()
                if not content:
                    continue
                if content.startswith(("def ", "class ", "async def ")):
                    has_signature_change = True
                elif self._is_doc_line(content):
                    continue  # 文档/注释行，不计入 body
                else:
                    has_body_change = True

        if has_signature_change:
            return ChangeType.SIGNATURE
        if has_body_change:
            return ChangeType.BODY
        return ChangeType.DOC_ONLY

    def _walk_supported_files(self) -> list[Path]:
        """遍历仓库中所有受支持的文件。

        跳过常见构建产物和缓存目录。
        """
        skip_dirs = {
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            "node_modules",
            ".mypy_cache",
            ".pytest_cache",
        }
        files: list[Path] = []
        for p in self._repo_path.rglob("*"):
            if any(skip in p.parts for skip in skip_dirs):
                continue
            if p.is_file() and p.suffix in self._extensions:
                files.append(p)
        return sorted(files)

    def full_scan(self) -> list[ChangedFile]:
        """全量扫描：检测所有与缓存不一致的文件。

        不依赖 git diff，直接遍历文件系统并对比 SHA256 缓存。

        Returns:
            变更文件列表。
        """
        results: list[ChangedFile] = []
        for file_path in self._walk_supported_files():
            rel_path = str(file_path.relative_to(self._repo_path))
            current_sha = self._compute_sha256(file_path)

            if not self._cache.has_changed(rel_path, current_sha):
                continue

            old_sha = self._cache.get(rel_path)
            results.append(
                ChangedFile(
                    path=rel_path,
                    change_type=ChangeType.ADDED if old_sha is None else ChangeType.BODY,
                    git_status=GitStatus.ADDED if old_sha is None else GitStatus.MODIFIED,
                    old_sha256=old_sha,
                    new_sha256=current_sha,
                    new_content=file_path.read_bytes(),
                )
            )

        self._logger.info("Full scan found %d changed files", len(results))
        return results

    def update_cache(self, changes: list[ChangedFile]) -> None:
        """根据变更结果更新缓存。

        Args:
            changes: ChangedFile 列表。
        """
        for change in changes:
            if change.change_type == ChangeType.DELETED:
                self._cache.remove(change.path)
            else:
                sha = change.new_sha256 or self._compute_sha256(self._repo_path / change.path)
                self._cache.set(change.path, sha, change.new_content)
        self._cache.save()

    def detect_changes(self, since: str = "HEAD~1") -> list[ChangedFile]:
        """检测指定 commit 范围内的变更文件。

        Args:
            since: git diff 的起始引用（如 HEAD~1, abc123）。

        Returns:
            分类后的 ChangedFile 列表。

        Raises:
            subprocess.CalledProcessError: git 命令执行失败。
        """
        raw_changes = self._git_diff_name_status(since)
        filtered = [c for c in raw_changes if self._is_supported(c[1])]

        results: list[ChangedFile] = []
        for git_status_code, file_path in filtered:
            changed_file = self._classify_change(file_path, git_status_code, since)
            if changed_file is not None:
                results.append(changed_file)

        self._logger.info("Detected %d changed files (since %s)", len(results), since)
        return results

    def _git_diff_name_status(self, since: str) -> list[tuple[str, str]]:
        """调用 git diff --name-status 获取变更列表。

        Args:
            since: git diff 的起始引用。

        Returns:
            [(status_code, file_path), ...] 列表。

        Raises:
            subprocess.CalledProcessError: git 命令执行失败。
        """
        cmd = ["git", "diff", "--name-status", since]
        result = subprocess.run(
            cmd,
            cwd=str(self._repo_path),
            capture_output=True,
            text=True,
            check=True,
        )
        changes: list[tuple[str, str]] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                changes.append((parts[0], parts[-1]))
        return changes

    def _classify_change(self, file_path: str, git_status_code: str, since: str) -> ChangedFile | None:
        """对单个文件进行变更分类。

        Args:
            file_path: 文件相对路径。
            git_status_code: git 状态码。
            since: git diff 起始引用。

        Returns:
            ChangedFile 对象，如果文件未实际变更则返回 None。
        """
        git_status = self._parse_git_status(git_status_code)
        abs_path = self._repo_path / file_path

        if git_status == GitStatus.DELETED:
            old_sha = self._cache.get(file_path)
            return ChangedFile(
                path=file_path,
                change_type=ChangeType.DELETED,
                git_status=git_status,
                old_sha256=old_sha,
            )

        if git_status == GitStatus.ADDED:
            new_sha = self._compute_sha256(abs_path) if abs_path.exists() else None
            new_content = abs_path.read_bytes() if abs_path.exists() else None
            return ChangedFile(
                path=file_path,
                change_type=ChangeType.ADDED,
                git_status=git_status,
                new_sha256=new_sha,
                new_content=new_content,
            )

        old_sha = self._cache.get(file_path)
        new_sha = self._compute_sha256(abs_path) if abs_path.exists() else None

        if old_sha and new_sha and old_sha == new_sha:
            return None

        new_content = abs_path.read_bytes() if abs_path.exists() else None
        change_type = self._classify_modification(file_path, abs_path, since)

        return ChangedFile(
            path=file_path,
            change_type=change_type,
            git_status=git_status,
            old_sha256=old_sha,
            new_sha256=new_sha,
            new_content=new_content,
        )

    def _classify_modification(self, file_path: str, abs_path: Path, since: str) -> ChangeType:
        """对修改文件进行细粒度变更分类。

        Args:
            file_path: 文件相对路径。
            abs_path: 文件绝对路径。
            since: git diff 起始引用。

        Returns:
            变更类型（SIGNATURE/BODY/DOC_ONLY）。
        """
        try:
            cmd = ["git", "diff", since, "--", file_path]
            result = subprocess.run(
                cmd,
                cwd=str(self._repo_path),
                capture_output=True,
                text=True,
                check=True,
            )
            diff_text = result.stdout
        except subprocess.CalledProcessError:
            return ChangeType.BODY

        return self._analyze_diff_hunks(diff_text)
