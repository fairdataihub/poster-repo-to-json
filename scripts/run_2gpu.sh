#!/bin/bash
# NEVER use more than 2 GPUs - PSU will hard power off the machine.
# Zenodo first (gpu0+gpu1), then Figshare (gpu2+gpu3).
# Runs quality check after each batch, logging failed extractions.

VENV=/home/james/poster_env
SCRIPT=/home/james/batch_extract_v2.py
QC_SCRIPT=/home/james/post_batch_qc.py
OUTPUT=/home/james/corpus_output/extractions
LOGDIR=/home/james/logs
SPLITS=/home/james/gpu_splits

mkdir -p "$LOGDIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

. "$VENV/bin/activate"

# Zenodo first, then Figshare
for BATCH in "0 1" "2 3"; do
    set -- $BATCH
    SPLIT_A=$1
    SPLIT_B=$2

    echo "[$(date)] Processing splits $SPLIT_A and $SPLIT_B on GPUs 0+1..." | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"

    CUDA_VISIBLE_DEVICES=0 python3 "$SCRIPT" \
        --posters "$SPLITS/gpu$SPLIT_A" \
        --output "$OUTPUT" \
        > "$LOGDIR/split${SPLIT_A}_${TIMESTAMP}.log" 2>&1 &
    PID_A=$!

    CUDA_VISIBLE_DEVICES=1 python3 "$SCRIPT" \
        --posters "$SPLITS/gpu$SPLIT_B" \
        --output "$OUTPUT" \
        > "$LOGDIR/split${SPLIT_B}_${TIMESTAMP}.log" 2>&1 &
    PID_B=$!

    echo "  Split $SPLIT_A -> GPU 0 (PID $PID_A)" | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"
    echo "  Split $SPLIT_B -> GPU 1 (PID $PID_B)" | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"

    wait $PID_A $PID_B
    echo "[$(date)] Splits $SPLIT_A and $SPLIT_B done." | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"

    # Quality check after each batch
    echo "[$(date)] Running quality check..." | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"
    python3 "$QC_SCRIPT" >> "$LOGDIR/launcher_${TIMESTAMP}.log" 2>&1
done

echo "[$(date)] ALL DONE." | tee -a "$LOGDIR/launcher_${TIMESTAMP}.log"
