#!/usr/bin/env bash
# 启动 Celery workers（修复 Windows 上 FORKED_BY_MULTIPROCESSING 环境变量缺失 bug）
#
# 使用方式：
#   bash scripts/start_workers.sh
#
# 对应 docs/TASK-ORCHESTRATION.md Celery 任务编排。
# 关键：必须设置 FORKED_BY_MULTIPROCESSING=True，否则 Celery 5.x 在 Windows 上
# 不会调用 setup_worker_optimizations()，导致 fast_trace_task() 抛
# ValueError: not enough values to unpack (expected 3, got 0)。

set -e

cd "$(dirname "$0")/.."

# Python 路径
export PYTHONPATH="$(pwd)/src:${PYTHONPATH:-}"

# 禁用 Celery 自身的 OTel 插桩（避免与其它 instrumentation 冲突）
export OTEL_PYTHON_DISABLED_INSTRUMENTATIONS="celery"

# ⚠️ 关键修复：Celery 进程池（billiard 在 Windows 上使用 spawn）
# 必须设置此环境变量，否则 setup_worker_optimizations 不会被调用。
export FORKED_BY_MULTIPROCESSING="True"

# 加载 .env（如果存在）
if [ -f .env ]; then
    set -a; source .env; set +a
fi

PYBIN="${PYTHON_BIN:-$(which python3 || which python)}"

echo "==> Starting GPU worker (concurrency=1, solo GPU serialization)..."
"${PYBIN}" -m celery -A videomind.application.task_orchestration.celery worker \
    -Q gpu -c 1 -n "gpu@%h" --loglevel=info &
GPU_PID=$!

echo "==> Starting CPU worker (concurrency=4, parallel CPU pipeline)..."
"${PYBIN}" -m celery -A videomind.application.task_orchestration.celery worker \
    -Q cpu -c 4 -n "cpu@%h" --loglevel=info &
CPU_PID=$!

trap "echo 'Stopping workers...'; kill $GPU_PID $CPU_PID 2>/dev/null || true; exit" INT TERM

echo "==> GPU worker PID=$GPU_PID, CPU worker PID=$CPU_PID"
echo "==> Workers running. Press Ctrl+C to stop."

wait
