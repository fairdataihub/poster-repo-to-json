#!/bin/bash
# Run batch_extract_v2 on the remaining pool. Caller passes the GPU index.
# Safe for multiple GPUs to share the same pool — files already done are
# skipped via batch_extract_v2's existence check.
#
# Usage: bash start_pool_gpu.sh <gpu_index>

GPU=${1:?"GPU index required"}
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
. /home/james/poster_env/bin/activate
exec env CUDA_VISIBLE_DEVICES=$GPU python3 /home/james/batch_extract_v2.py \
    --posters /home/james/gpu_splits/remaining \
    --output /home/james/corpus_output/extractions \
    > "/home/james/logs/pool_gpu${GPU}_${TIMESTAMP}.log" 2>&1
