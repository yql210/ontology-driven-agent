# Day 4 实现计划：JavaParser 单元测试补充

> 目标：补充 20+ 边界/组合测试，覆盖现有 47 个测试未触及的场景。

## 当前状态
- 47 个测试已通过，覆盖：基本实体提取、关系提取、错误处理
- JavaParser ~700 行，18 个方法

## 缺失覆盖的场景（20+ cases）

### A. 实体提取边界（7 个）

1. **test_abstract_class** — abstract class 提取，验证 name 和 entity_type="class"
2. **test_generic_class** — `class Foo<T extends Bar>` 泛型类，验证 name 不含泛型参数
3. **test_nested_inner_class** — 类中嵌套类，验证两个 class 实体 + contains 关系
4. **test_static_import** — `import static org.junit.Assert.assertEquals` 静态导入
5. **test_multiple_constructors** — 多个构造函数重载
6. **test_method_with_throws** — 方法 throws 子句不影响参数提取
7. **test_enum_with_fields_only** — 枚举只有字段没有方法

### B. 关系提取边界（7 个）

8. **test_extends_generic_class** — `class Foo extends Bar<String>` extends 目标不含泛型
9. **test_extends_nested_class** — `class Foo extends Outer.Inner` 复杂 extends
10. **test_chained_method_calls** — `foo.bar().baz()` 链式调用
11. **test_method_call_in_if** — if 条件中的方法调用也能提取
12. **test_method_call_in_lambda** — lambda 体内的方法调用
13. **test_new_generic_object** — `new ArrayList<String>()` new 泛型对象
14. **test_static_method_call** — `Collections.sort(list)` 静态方法调用

### C. Javadoc 边界（3 个）

15. **test_javadoc_with_tags** — Javadoc 含 @param, @return, @throws 标签
16. **test_no_javadoc** — 方法/类没有 Javadoc 时 docstring 为 None
17. **test_javadoc_multiline** — 多行 Javadoc 正确提取

### D. 源码/文件路径边界（4 个）

18. **test_parse_source_vs_file** — parse_source 和 parse_file 结果一致
19. **test_file_entity_created** — 每次解析都有 file entity
20. **test_entity_start_end_line** — 实体的 start_line 和 end_line 正确
21. **test_source_truncated_at_500** — source 字段截断到 500 字符

### E. 组合场景（2 个）

22. **test_complex_service_class** — 一个 Service 类同时有 class/fields/methods/constructor/extends/implements/calls
23. **test_multiple_files_different_packages** — 两次解析不同 package 不互相干扰

## 执行方式

分两批给 Claude Code：
- **Batch 1**（A + B = 14 个测试）：实体和关系边界
- **Batch 2**（C + D + E = 9 个测试）：Javadoc + 源码边界 + 组合

每批 --max-turns 25。

## 文件
- 测试追加到 `tests/unit/test_java_parser.py` 末尾
- 不修改 `src/layerkg/parser/java_parser.py`

## 验证标准
- 全部 67+ 测试通过（47 现有 + 20 新增）
- `uv run ruff check tests/unit/test_java_parser.py` 无错误
- 全量 894+ passed
