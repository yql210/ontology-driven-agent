from __future__ import annotations

import importlib
import importlib.util
import pathlib

import pytest


@pytest.mark.unit
def test_all_source_modules_importable():
    """验证所有 src/ontoagent/*.py 可导入。"""
    src_dir = pathlib.Path(__file__).parent.parent.parent / "src" / "ontoagent"

    # 收集所有 .py 文件（排除 __pycache__ 等）
    py_files = sorted(src_dir.rglob("*.py"))
    # 过滤掉 __init__.py 之外的文件，并跳过测试目录
    source_files = [
        f for f in py_files if "__pycache__" not in f.parts and ".pytest_cache" not in f.parts and "test_" not in f.name
    ]

    failed = []
    for file_path in source_files:
        # 计算模块路径
        rel_path = file_path.relative_to(src_dir.parent.parent)
        module_name = str(rel_path.with_suffix("")).replace("/", ".")

        try:
            importlib.import_module(module_name)
        except Exception as e:
            failed.append((module_name, e))

    # 如果有导入失败，输出详细信息
    if failed:
        error_msg = "\n".join(f"  - {name}: {err}" for name, err in failed)
        pytest.fail(f"Failed to import modules:\n{error_msg}")


@pytest.mark.unit
def test_all_test_modules_importable():
    """验证所有 test_*.py 可导入。"""
    tests_dir = pathlib.Path(__file__).parent.parent

    # 收集所有 test_*.py 文件
    test_files = sorted(tests_dir.rglob("test_*.py"))
    # 过滤掉 __pycache__ 等
    test_files = [
        f
        for f in test_files
        if "__pycache__" not in f.parts and ".pytest_cache" not in f.parts and "conftest.py" not in f.name
    ]

    failed = []
    for file_path in test_files:
        # 计算模块路径
        rel_path = file_path.relative_to(tests_dir.parent)
        module_name = str(rel_path.with_suffix("")).replace("/", ".")

        try:
            importlib.import_module(module_name)
        except Exception as e:
            failed.append((module_name, e))

    # 如果有导入失败，输出详细信息
    if failed:
        error_msg = "\n".join(f"  - {name}: {err}" for name, err in failed)
        pytest.fail(f"Failed to import test modules:\n{error_msg}")
