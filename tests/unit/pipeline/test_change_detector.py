from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ontoagent.pipeline.change_detector import (
    ChangedFile,
    ChangeType,
    GitChangeDetector,
    GitStatus,
    SHA256Cache,
)

# Task 1: ChangeType + GitStatus 枚举测试 (3 tests)


@pytest.mark.unit
def test_changetype_has_all_values():
    """Test that ChangeType enum has all required values."""
    assert ChangeType.ADDED.value == "added"
    assert ChangeType.DELETED.value == "deleted"
    assert ChangeType.SIGNATURE.value == "signature"
    assert ChangeType.BODY.value == "body"
    assert ChangeType.DOC_ONLY.value == "doc_only"


@pytest.mark.unit
def test_changetype_count():
    """Test that ChangeType has exactly 5 values."""
    assert len(ChangeType) == 5


@pytest.mark.unit
def test_gitstatus_has_all_values():
    """Test that GitStatus enum has all required values."""
    assert GitStatus.ADDED.value == "A"
    assert GitStatus.DELETED.value == "D"
    assert GitStatus.MODIFIED.value == "M"
    assert GitStatus.RENAMED.value == "R"
    assert GitStatus.COPIED.value == "C"
    assert GitStatus.TYPE_CHANGE.value == "T"
    assert GitStatus.UNMERGED.value == "U"
    assert GitStatus.UNKNOWN.value == "X"


# Task 2: ChangedFile dataclass 测试 (3 tests)


@pytest.mark.unit
def test_changed_file_creation_added():
    """Test creating a ChangedFile for ADDED type."""
    cf = ChangedFile(
        path="src/example.py",
        change_type=ChangeType.ADDED,
        git_status=GitStatus.ADDED,
        new_sha256="abc123",
        new_content=b"def foo(): pass",
    )
    assert cf.path == "src/example.py"
    assert cf.change_type == ChangeType.ADDED
    assert cf.git_status == GitStatus.ADDED
    assert cf.old_sha256 is None
    assert cf.new_sha256 == "abc123"
    assert cf.old_content is None
    assert cf.new_content == b"def foo(): pass"


@pytest.mark.unit
def test_changed_file_creation_deleted():
    """Test creating a ChangedFile for DELETED type."""
    cf = ChangedFile(
        path="src/old.py",
        change_type=ChangeType.DELETED,
        git_status=GitStatus.DELETED,
        old_sha256="def456",
        old_content=b"old content",
    )
    assert cf.path == "src/old.py"
    assert cf.change_type == ChangeType.DELETED
    assert cf.git_status == GitStatus.DELETED
    assert cf.old_sha256 == "def456"
    assert cf.new_sha256 is None


@pytest.mark.unit
def test_changed_file_empty_path_raises():
    """Test that ChangedFile rejects empty path."""
    with pytest.raises(ValueError, match="path cannot be empty"):
        ChangedFile(
            path="",
            change_type=ChangeType.ADDED,
            git_status=GitStatus.ADDED,
        )


@pytest.mark.unit
def test_changed_file_invalid_change_type_raises():
    """Test that ChangedFile rejects invalid change_type."""
    with pytest.raises(TypeError, match="change_type must be ChangeType"):
        ChangedFile(
            path="test.py",
            change_type="added",  # type: ignore
            git_status=GitStatus.ADDED,
        )


@pytest.mark.unit
def test_changed_file_invalid_git_status_raises():
    """Test that ChangedFile rejects invalid git_status."""
    with pytest.raises(TypeError, match="git_status must be GitStatus"):
        ChangedFile(
            path="test.py",
            change_type=ChangeType.ADDED,
            git_status="A",  # type: ignore
        )


# Task 3: SHA256Cache 基础操作测试 (7 tests)


@pytest.mark.unit
def test_sha256cache_get_missing_returns_none():
    """Test that SHA256Cache.get returns None for missing keys."""
    cache = SHA256Cache()
    assert cache.get("nonexistent.py") is None


@pytest.mark.unit
def test_sha256cache_set_and_get():
    """Test SHA256Cache set and get operations."""
    cache = SHA256Cache()
    cache.set("test.py", "abc123", b"content")
    assert cache.get("test.py") == "abc123"


@pytest.mark.unit
def test_sha256cache_has_changed_new_file():
    """Test SHA256Cache.has_changed returns True for new files."""
    cache = SHA256Cache()
    assert cache.has_changed("new.py", "sha256") is True


@pytest.mark.unit
def test_sha256cache_has_changed_unchanged():
    """Test SHA256Cache.has_changed returns False for unchanged files."""
    cache = SHA256Cache()
    cache.set("stable.py", "sha256_unchanged")
    assert cache.has_changed("stable.py", "sha256_unchanged") is False


@pytest.mark.unit
def test_sha256cache_has_changed_changed():
    """Test SHA256Cache.has_changed returns True for changed files."""
    cache = SHA256Cache()
    cache.set("modified.py", "old_sha")
    assert cache.has_changed("modified.py", "new_sha") is True


@pytest.mark.unit
def test_sha256cache_remove():
    """Test SHA256Cache.remove removes entry."""
    cache = SHA256Cache()
    cache.set("to_remove.py", "sha256")
    cache.remove("to_remove.py")
    assert cache.get("to_remove.py") is None


@pytest.mark.unit
def test_sha256cache_size():
    """Test SHA256Cache.size returns correct count."""
    cache = SHA256Cache()
    assert cache.size == 0
    cache.set("a.py", "sha_a")
    cache.set("b.py", "sha_b")
    assert cache.size == 2
    cache.remove("a.py")
    assert cache.size == 1


# Task 4: SHA256Cache 持久化测试 (2 tests)


@pytest.mark.unit
def test_sha256cache_save_and_load(tmp_path: Path):
    """Test SHA256Cache save and load roundtrip."""
    cache_file = tmp_path / "cache.json"
    cache1 = SHA256Cache(cache_file=cache_file)
    cache1.set("test.py", "abc123")
    cache1.save()

    cache2 = SHA256Cache(cache_file=cache_file)
    assert cache2.get("test.py") == "abc123"
    assert cache2.size == 1


@pytest.mark.unit
def test_sha256cache_load_corrupted_degrades_gracefully(tmp_path: Path, caplog):
    """Test SHA256Cache handles corrupted cache file gracefully."""
    cache_file = tmp_path / "cache.json"
    # 写入损坏的 JSON
    cache_file.write_text("{invalid json")

    cache = SHA256Cache(cache_file=cache_file)
    # 应该优雅降级，不崩溃
    assert cache.size == 0
    # 应该有警告日志
    assert any("Failed to load cache" in r.message for r in caplog.records)


# Task 5: _compute_sha256 测试 (1 test)


@pytest.mark.unit
def test_compute_sha256_matches_hashlib(tmp_path: Path):
    """Test that _compute_sha256 matches hashlib.sha256."""
    test_file = tmp_path / "test.py"
    test_file.write_text("def foo(): pass")

    expected = hashlib.sha256(test_file.read_bytes()).hexdigest()
    actual = GitChangeDetector._compute_sha256(test_file)

    assert actual == expected


# Task 6: _is_supported 测试 (2 tests)


@pytest.mark.unit
def test_is_supported_default_extensions():
    """Test _is_supported with default extensions."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    assert detector._is_supported("test.py") is True
    assert detector._is_supported("test.md") is False
    assert detector._is_supported("test.css") is False


@pytest.mark.unit
def test_is_supported_custom_extensions():
    """Test _is_supported with custom extensions."""
    detector = GitChangeDetector(
        repo_path=Path.cwd(),
        supported_extensions=frozenset({".py", ".md", ".rs"}),
    )
    assert detector._is_supported("test.py") is True
    assert detector._is_supported("README.md") is True
    assert detector._is_supported("main.rs") is True
    assert detector._is_supported("style.css") is False


# Task 7: _parse_git_status 测试 (3 tests)


@pytest.mark.unit
def test_parse_git_status_simple_codes():
    """Test _parse_git_status with simple status codes."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    assert detector._parse_git_status("A") == GitStatus.ADDED
    assert detector._parse_git_status("M") == GitStatus.MODIFIED
    assert detector._parse_git_status("D") == GitStatus.DELETED
    assert detector._parse_git_status("R") == GitStatus.RENAMED
    assert detector._parse_git_status("C") == GitStatus.COPIED
    assert detector._parse_git_status("T") == GitStatus.TYPE_CHANGE
    assert detector._parse_git_status("U") == GitStatus.UNMERGED


@pytest.mark.unit
def test_parse_git_status_composite_code():
    """Test _parse_git_status with composite code like R100."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    # R100 表示重命名且 100% 相似度
    assert detector._parse_git_status("R100") == GitStatus.RENAMED
    assert detector._parse_git_status("C095") == GitStatus.COPIED


@pytest.mark.unit
def test_parse_git_status_empty_and_unknown():
    """Test _parse_git_status with empty and unknown codes."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    assert detector._parse_git_status("") == GitStatus.UNKNOWN
    assert detector._parse_git_status("Z") == GitStatus.UNKNOWN
    assert detector._parse_git_status("INVALID") == GitStatus.UNKNOWN


# 额外：构造函数校验测试


@pytest.mark.unit
def test_constructor_non_directory_raises():
    """Test that GitChangeDetector raises ValueError for non-directory path."""
    with pytest.raises(ValueError, match="must be a directory"):
        GitChangeDetector(repo_path=Path("/nonexistent/path/xyz"))


# Task 8: _is_doc_line 测试 (4 tests)


@pytest.mark.unit
def test_is_doc_line_hash_comment():
    """Test _is_doc_line with # comment."""
    assert GitChangeDetector._is_doc_line("# This is a comment") is True
    assert GitChangeDetector._is_doc_line("# TODO: fix this") is True
    assert GitChangeDetector._is_doc_line("    # indented comment") is False  # 不以#开头


@pytest.mark.unit
def test_is_doc_line_docstring_start():
    """Test _is_doc_line with docstring start markers."""
    assert GitChangeDetector._is_doc_line('"""This is a docstring') is True
    assert GitChangeDetector._is_doc_line("'''This is also a docstring") is True
    assert GitChangeDetector._is_doc_line('    """Start of docstring') is False  # 不以"""开头


@pytest.mark.unit
def test_is_doc_line_docstring_end():
    """Test _is_doc_line with docstring end markers."""
    assert GitChangeDetector._is_doc_line('End of docstring"""') is True
    assert GitChangeDetector._is_doc_line("End of docstring'''") is True


@pytest.mark.unit
def test_is_doc_line_inline_docstring():
    """Test _is_doc_line with inline docstrings."""
    assert GitChangeDetector._is_doc_line('"""Inline docstring"""') is True
    assert GitChangeDetector._is_doc_line("'''Inline docstring'''") is True
    assert GitChangeDetector._is_doc_line('"""x"""') is False  # 长度 = 7, 太短
    assert GitChangeDetector._is_doc_line('"""xy"""') is True  # 长度 = 8, 有效
    assert GitChangeDetector._is_doc_line("'''''") is False  # 长度 = 5, 太短


@pytest.mark.unit
def test_is_doc_line_non_doc():
    """Test _is_doc_line with non-documentation lines."""
    assert GitChangeDetector._is_doc_line("def foo():") is False
    assert GitChangeDetector._is_doc_line("return 42") is False
    assert GitChangeDetector._is_doc_line("x = 1") is False
    assert GitChangeDetector._is_doc_line("") is False


# Task 8: _analyze_diff_hunks 测试 (6 tests)


@pytest.mark.unit
def test_analyze_diff_signature_change():
    """Test _analyze_diff_hunks with signature change."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    diff = """@@ -1,3 +1,3 @@
-def old_func(self):
+def new_func(self, arg):
     pass
"""
    assert detector._analyze_diff_hunks(diff) == ChangeType.SIGNATURE


@pytest.mark.unit
def test_analyze_diff_body_change():
    """Test _analyze_diff_hunks with body change."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    diff = """@@ -1,3 +1,3 @@
 def foo():
-    return 1
+    return 2
"""
    assert detector._analyze_diff_hunks(diff) == ChangeType.BODY


@pytest.mark.unit
def test_analyze_diff_doc_only_comment():
    """Test _analyze_diff_hunks with only comment changes."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    diff = """@@ -1,3 +1,3 @@
 def foo():
-    # Old comment
+    # New comment
     pass
"""
    assert detector._analyze_diff_hunks(diff) == ChangeType.DOC_ONLY


@pytest.mark.unit
def test_analyze_diff_doc_only_docstring():
    """Test _analyze_diff_hunks with only docstring changes."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    diff = """@@ -1,4 +1,4 @@
 def foo():
-    \"""Old docstring\"""
+    \"""New docstring\"""
     pass
"""
    assert detector._analyze_diff_hunks(diff) == ChangeType.DOC_ONLY


@pytest.mark.unit
def test_analyze_diff_empty():
    """Test _analyze_diff_hunks with empty diff."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    assert detector._analyze_diff_hunks("") == ChangeType.DOC_ONLY
    assert detector._analyze_diff_hunks("+++ /dev/null\n--- /dev/null") == ChangeType.DOC_ONLY


@pytest.mark.unit
def test_analyze_diff_mixed_signature_and_body():
    """Test _analyze_diff_hunks with mixed changes (SIGNATURE takes priority)."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    diff = """@@ -1,5 +1,5 @@
-def old_func(self):
+def new_func(self, arg):
     \"""Docstring\"""
-    return 1
+    return 2
"""
    assert detector._analyze_diff_hunks(diff) == ChangeType.SIGNATURE


@pytest.mark.unit
def test_analyze_diff_class_change():
    """Test _analyze_diff_hunks with class signature change."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    diff = """@@ -1,3 +1,3 @@
-class OldClass:
+class NewClass(Base):
     pass
"""
    assert detector._analyze_diff_hunks(diff) == ChangeType.SIGNATURE


@pytest.mark.unit
def test_analyze_diff_async_def_change():
    """Test _analyze_diff_hunks with async def signature change."""
    detector = GitChangeDetector(repo_path=Path.cwd())
    diff = """@@ -1,3 +1,3 @@
-async def old_func():
+async def new_func():
     pass
"""
    assert detector._analyze_diff_hunks(diff) == ChangeType.SIGNATURE


# Task 9: full_scan 测试 (3 tests)


@pytest.mark.unit
def test_full_scan_detects_new_file(tmp_path: Path):
    """Test full_scan detects new files not in cache."""
    detector = GitChangeDetector(repo_path=tmp_path)
    new_file = tmp_path / "test.py"
    new_file.write_text("def foo(): pass")

    results = detector.full_scan()
    assert len(results) == 1
    assert results[0].path == "test.py"
    assert results[0].change_type == ChangeType.ADDED


@pytest.mark.unit
def test_full_scan_unchanged(tmp_path: Path):
    """Test full_scan skips unchanged files."""
    cache = SHA256Cache()
    detector = GitChangeDetector(repo_path=tmp_path, cache=cache)

    # 先创建文件并加入缓存
    test_file = tmp_path / "stable.py"
    test_file.write_text("def foo(): pass")
    sha = GitChangeDetector._compute_sha256(test_file)
    cache.set("stable.py", sha)

    results = detector.full_scan()
    assert len(results) == 0


@pytest.mark.unit
def test_full_scan_skips_unsupported(tmp_path: Path):
    """Test full_scan skips unsupported file types."""
    detector = GitChangeDetector(repo_path=tmp_path)
    (tmp_path / "test.md").write_text("# Markdown")
    (tmp_path / "test.txt").write_text("Plain text")

    results = detector.full_scan()
    assert len(results) == 0


@pytest.mark.unit
def test_full_scan_skips_ignored_dirs(tmp_path: Path):
    """Test full_scan skips ignored directories."""
    detector = GitChangeDetector(repo_path=tmp_path)

    # 创建各个忽略目录中的文件
    for dir_name in [".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache"]:
        dir_path = tmp_path / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)
        (dir_path / "test.py").write_text("def foo(): pass")

    results = detector.full_scan()
    assert len(results) == 0


# Task 10: update_cache 测试 (3 tests)


@pytest.mark.unit
def test_update_cache_adds_new(tmp_path: Path):
    """Test update_cache adds new files."""
    cache_file = tmp_path / "cache.json"
    cache = SHA256Cache(cache_file=cache_file)
    detector = GitChangeDetector(repo_path=tmp_path, cache=cache)

    changes = [
        ChangedFile(
            path="new.py",
            change_type=ChangeType.ADDED,
            git_status=GitStatus.ADDED,
            new_sha256="abc123",
            new_content=b"content",
        )
    ]

    detector.update_cache(changes)
    assert cache.get("new.py") == "abc123"


@pytest.mark.unit
def test_update_cache_removes_deleted(tmp_path: Path):
    """Test update_cache removes deleted files."""
    cache_file = tmp_path / "cache.json"
    cache = SHA256Cache(cache_file=cache_file)
    cache.set("to_delete.py", "old_sha")
    detector = GitChangeDetector(repo_path=tmp_path, cache=cache)

    changes = [
        ChangedFile(
            path="to_delete.py",
            change_type=ChangeType.DELETED,
            git_status=GitStatus.DELETED,
            old_sha256="old_sha",
        )
    ]

    detector.update_cache(changes)
    assert cache.get("to_delete.py") is None


@pytest.mark.unit
def test_update_cache_saves_to_disk(tmp_path: Path):
    """Test update_cache persists to disk."""
    cache_file = tmp_path / "cache.json"
    cache = SHA256Cache(cache_file=cache_file)
    detector = GitChangeDetector(repo_path=tmp_path, cache=cache)

    changes = [
        ChangedFile(
            path="test.py",
            change_type=ChangeType.ADDED,
            git_status=GitStatus.ADDED,
            new_sha256="xyz789",
        )
    ]

    detector.update_cache(changes)
    assert cache_file.exists()

    # 加载新缓存验证
    new_cache = SHA256Cache(cache_file=cache_file)
    assert new_cache.get("test.py") == "xyz789"


# Task 11: detect_changes 测试 (1 test)


@pytest.mark.unit
def test_detect_changes_with_mock_git(tmp_path: Path, monkeypatch):
    """Test detect_changes with mocked git subprocess."""
    detector = GitChangeDetector(repo_path=tmp_path)

    # Mock subprocess.run for git diff --name-status
    class MockResult:
        stdout = "M\tmodified.py\nA\tnew.py\nD\tdeleted.py"

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: MockResult())

    # 创建文件供 SHA256 计算
    (tmp_path / "modified.py").write_text("new content")
    (tmp_path / "new.py").write_text("new file")

    results = detector.detect_changes("HEAD~1")
    paths = [r.path for r in results]
    assert "modified.py" in paths
    assert "new.py" in paths
