# Python 编码规范

## 适用范围
所有 `src/` 和 `tests/` 目录下的 `.py` 文件。

## 基础规范
- Python 3.13+，使用现代语法
- `from __future__ import annotations` — 所有文件头部
- 4空格缩进，UTF-8 编码
- 行宽 120 字符
- 每个模块有 `__init__.py`（可空）
- 类型注解必须：所有 public 函数参数和返回值
- 使用 `X | None` 而非 `Optional[X]`
- 使用 `list[X]` 而非 `List[X]`（Python 3.9+）
- f-string 优先于 `.format()` 和 `%`
- `pathlib.Path` 代替 `os.path`

## 命名规范
- 模块/包：`snake_case`
- 类：`PascalCase`
- 函数/方法/变量：`snake_case`
- 常量：`UPPER_SNAKE_CASE`
- 私有成员：`_leading_underscore`
- 抽象基类：`Base` 前缀或 `ABC` 后缀

## 导入顺序（isort 风格）
```python
from __future__ import annotations

# 1. 标准库
import json
from pathlib import Path

# 2. 第三方库
from neo4j import GraphDatabase

# 3. 本项目
from layerkg.schema import CodeEntity
```

## Docstring 规范（Google 风格）
```python
def build_graph(repo_path: Path, *, incremental: bool = False) -> Graph:
    """构建代码知识图谱。

    Args:
        repo_path: 仓库根目录路径。
        incremental: 是否增量构建，默认全量。

    Returns:
        构建完成的 Graph 对象。

    Raises:
        ValueError: 当 repo_path 不存在时。
    """
```

## 数据模型
- Schema 实体使用 `@dataclass` 定义
- 必须实现 `__post_init__` 做字段校验
- ID 生成使用 UUID v4
- 时间字段使用 `datetime` + UTC

## 错误处理
- 自定义异常继承自 `LayerKGError` 基类
- 不捕获裸 `Exception`，明确指定异常类型
- 使用 `logging` 而非 `print`
- 函数失败时抛异常，不返回 None

## 异步
- Phase 0 暂不使用 async
- Phase 1+ 引入 LangGraph 时切换为 async

## 格式化
- 使用 ruff format 自动格式化
- 使用 ruff check 做静态检查
- 提交前必须 ruff 通过
