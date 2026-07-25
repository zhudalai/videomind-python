# Task 1 完成报告

## 状态: PASS

**日期:** 2026-07-25
**Phase:** Phase 1 - RAG 检索层
**Task:** Task 1 - evidence_id 锚定

---

## 创建/修改的文件

### 新建
| 文件 | 说明 |
|------|------|
| `src/videomind/core/rag/evidence.py` | evidence_id 模块实现（纯函数，无外部依赖） |
| `tests/core/rag/test_evidence.py` | 13 个单元测试，覆盖生成/解析/正则/提取 |

### 已存在（Task 0 产物，未修改）
- `src/videomind/core/rag/__init__.py`
- `tests/__init__.py` / `tests/core/__init__.py` / `tests/core/rag/__init__.py`

---

## 测试结果

```
============================= 13 passed in 0.12s ==============================
```

分解：
- `TestMakeEvidenceId` — 3 tests PASS（基本生成、_02、_100 大索引）
- `TestParseEvidenceId` — 4 tests PASS（round-trip 索引验证、idx=0 回归、无效格式抛出 ValueError）
- `TestCitationRegex` — 6 tests PASS（文本提取、部分前缀不匹配、正则 Pattern 类型检查、合法/非法格式 fullmatch）

---

## 导出接口

| 函数 / 常量 | 签名 |
|------------|------|
| `make_evidence_id` | `(chunk_id: uuid.UUID, index: int) -> str` |
| `parse_evidence_id` | `(eid: str) -> tuple[uuid.UUID, int]` |
| `extract_evidence_ids` | `(text: str) -> list[str]` |
| `EID_CITATION_RE` | `re.Pattern` — 匹配 `[EID_{8hex}_{2digit}]` |

---

## 实现细节说明

1. **UUID 部分还原**: `make_evidence_id` 只编码 UUID 前 8 位 hex，`parse_evidence_id` 返回的 UUID 只有前 8 hex 位与原始一致（其余位填入 `0000-4000-a000-000000000000` 占位符）。这是信息论上的硬限制 —— 仅从 8 hex 字符无法恢复完整 32 hex UUID。后续 RAG pipeline 中靠 chunk 表通过前 8 hex 和 media_id 条件查询定位完整 chunk。

2. **正则分离**: Plan 原版只有一个带方括号的正则 `EID_CITATION_RE`，但同时把它用于 `parse_evidence_id`（输入不带方括号）。实现中新增一个内部的 `_EID_INNER_PATTERN`（`r'^EID_([a-z0-9]{8})_(\d{2})$'`）专用于解析函数，`EID_CITATION_RE` 保持不变用于文本提取。

3. **中文 docstring**: 所有函数和模块使用中文文档字符串。

---

## Commit SHA

本项目非 git 仓库，未生成 commit。

---

## 无后续 concern

Task 1 是纯函数工具模块，没有外部依赖、IO 或 side effect。所有测试通过，可直接被后续 Task 2（BM25 通道）引用。