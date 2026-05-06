#!/bin/bash
# Watchdog: ensures one batch_extract_v2 process per assigned GPU.
# If a GPU's process has died and its split still has work to do, restart it.

LOCKFILE=/tmp/watchdog.lock
exec 200>"$LOCKFILE"
flock -n 200 || { echo "[$(date)] Another watchdog is running, exiting"; exit 0; }

LOG=/home/james/logs/watchdog.log
SPLITS=/home/james/gpu_splits
EXT=/home/james/corpus_output/extractions

mkdir -p /home/james/logs

is_gpu_alive() {
    local gpu="$1"
    for path in "gpu${gpu}" "remaining" "gpu3"; do
        for pid in $(pgrep -f "batch_extract_v2.py.*--posters.*/home/james/gpu_splits/${path}"); do
            local env_gpu=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | grep CUDA_VISIBLE_DEVICES | cut -d= -f2)
            if [ "$env_gpu" = "$gpu" ]; then
                return 0
            fi
        done
    done
    return 1
}

count_pending_in() {
    local split_dir="$1"
    [ -d "$split_dir" ] || { echo 0; return; }
    comm -23 \
        <(ls "$split_dir" | sed 's/\.[^.]*$//' | sort -u) \
        <(find "$EXT" -maxdepth 1 -name '*_extracted.json' -exec grep -L '"error"' {} \; 2>/dev/null | sed 's|.*/||;s|_extracted\.json$||' | sort -u) \
        | wc -l
}

restart_gpu() {
    local gpu="$1"
    local script="$2"
    echo "[$(date)] Restarting GPU $gpu via $script" >> "$LOG"
    setsid bash "$script" </dev/null >>"$LOG" 2>&1 &
    disown
}

bash /home/james/build_remaining_pool.sh >/dev/null 2>&1

for gpu in 0 1 2; do
    if is_gpu_alive "$gpu"; then
        continue
    fi

    primary_split=""
    primary_script=""
    case "$gpu" in
        0) primary_split="$SPLITS/gpu0"; primary_script=/home/james/start_gpu0_split0.sh ;;
        1) primary_split="$SPLITS/gpu1"; primary_script=/home/james/start_gpu1_split1.sh ;;
        2) primary_split="$SPLITS/gpu2"; primary_script=/home/james/start_gpu2_remaining.sh ;;
    esac

    primary_pending=$(count_pending_in "$primary_split")

    if [ "$primary_pending" -gt 0 ]; then
        echo "[$(date)] GPU $gpu idle, $primary_pending still pending in primary split — restarting primary" >> "$LOG"
        restart_gpu "$gpu" "$primary_script"
        sleep 60
        continue
    fi

    pool_pending=$(count_pending_in "$SPLITS/remaining")
    if [ "$pool_pending" -gt 0 ]; then
        echo "[$(date)] GPU $gpu primary split done, $pool_pending in pool — pivoting to pool" >> "$LOG"
        setsid bash /home/james/start_pool_gpu.sh "$gpu" </dev/null >>"$LOG" 2>&1 &
        disown
        sleep 60
    fi
done
