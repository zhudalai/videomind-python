# Task 10: E2E 集成测试报告

## 状态: DONE

## 实现摘要

### 新建文件
- `tests/core/rag/test_e2e.py` — 6 个 E2E 测试用例

### 修改文件
- `pyproject.toml` — 添加 `e2e` marker

## 测试详情

| 测试 | 描述 | 结果 |
|---|---|---|
| `test_e2e_db_connectivity` | 验证 DB 连接可用 | PASSED |
| `test_e2e_find_media_with_chunks` | 找到已有 chunks 的 media_id | PASSED |
| `test_e2e_pipeline_executes` | 完整 RAG 管线执行不抛异常 | PASSED |
| `test_e2e_evidence_format` | 验证 evidence_id 以 EID_ 开头，格式正确 | PASSED |
| `test_e2e_top_k_respect` | 验证 top_k 参数生效 | PASSED |
| `test_e2e_empty_result_structure` | 无意义查询时返回结构完整性 | PASSED |

## 测试统计

- **E2E 测试**: 6 passed
- **全部 RAG 测试**: 59 passed
- **耗时**: ~44 秒

## 基础设施状态

- PostgreSQL (localhost:15432): 可用
- Qdrant (localhost:6333): 可用
- Embedding 模型: 可用（本地 sentence-transformers）
- 数据库中有已索引的 chunk 数据

## 关键设计点

1. **连接错误检测**: `_is_connection_error()` 函数覆盖了 httpx、grpc、socket、OSError、asyncpg 等各层级的连接失败场景
2. **优雅降级**: 每个测试在最外层 try/except 捕获连接错误并 skip，不会造成虚假失败
3. **格式严格校验**: evidence_format 测试不仅检查 EID_ 前缀，还验证 hex/seq 部分的长度和字符集
4. **E2E marker**: 通过 `pytest -m e2e` 可独立运行 E2E 测试套件，CI 环境可选择跳过