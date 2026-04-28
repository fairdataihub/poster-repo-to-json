#!/bin/bash
# 3-GPU launcher. ALL GPUS POWER-CAPPED TO 250W (verify with nvidia-smi).
# Approach: 3 GPUs in parallel on splits 0,1,2. When all finish,
# kick off split 3 on GPU 0.

VENV=/home/james/poster_env
SCRIPT=/home/james/batch_extract_v2.py
QC_SCRIPT=/home/james/post_batch_qc.py
OUTPUT=/home/james/corpus_output/extractions
LOGDIR=/home/james/logs
SPLITS=/home/james/gpu_splits

mkdir -p "$LOGDIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

. "$VENV/bin/activate"

echo "[$(date)] Phase A: GPUs 0,1,2 on splits 0,1,2..." | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"

CUDA_VISIBLE_DEVICES=0 python3 "$SCRIPT" --posters "$SPLITS/gpu0" --output "$OUTPUT" > "$LOGDIR/split0_${TIMESTAMP}.log" 2>&1 &
PID_0=$!

CUDA_VISIBLE_DEVICES=1 python3 "$SCRIPT" --posters "$SPLITS/gpu1" --output "$OUTPUT" > "$LOGDIR/split1_${TIMESTAMP}.log" 2>&1 &
PID_1=$!

CUDA_VISIBLE_DEVICES=2 python3 "$SCRIPT" --posters "$SPLITS/gpu2" --output "$OUTPUT" > "$LOGDIR/split2_${TIMESTAMP}.log" 2>&1 &
PID_2=$!

echo "  Split 0 -> GPU 0 (PID $PID_0)" | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"
echo "  Split 1 -> GPU 1 (PID $PID_1)" | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"
echo "  Split 2 -> GPU 2 (PID $PID_2)" | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"

wait $PID_0 $PID_1 $PID_2
echo "[$(date)] Phase A done." | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"

python3 "$QC_SCRIPT" >> "$LOGDIR/launcher_${TIMESTAMP}.log" 2>&1

echo "[$(date)] Phase B: GPU 0 on split 3..." | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"

CUDA_VISIBLE_DEVICES=0 python3 "$SCRIPT" --posters "$SPLITS/gpu3" --output "$OUTPUT" > "$LOGDIR/split3_${TIMESTAMP}.log" 2>&1 &
PID_3=$!
echo "  Split 3 -> GPU 0 (PID $PID_3)" | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"

wait $PID_3

python3 "$QC_SCRIPT" >> "$LOGDIR/launcher_${TIMESTAMP}.log" 2>&1
echo "[$(date)] ALL DONE." | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"
