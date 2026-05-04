#!/bin/bash
# Watchdog: ensures one batch_extract_v2 process per assigned GPU.
# If a GPU's process has died and its split still has work to do, restart it.
#
# Schedule via Windows Task Scheduler. IMPORTANT: register as SYSTEM (not the
# default current-user/Interactive only) so it runs even when no one is
# logged in:
#
#   schtasks /create /tn PosterWatchdog \
#       /tr "wsl.exe -d Ubuntu-24.04 -e bash /home/james/watchdog.sh" \
#       /sc minute /mo 5 /ru SYSTEM /rl highest /f
#
# Without /ru SYSTEM the task is "Interactive only" and silently skips runs
# while the user is signed out — defeating the whole point of the watchdog.

LOG=/home/james/logs/watchdog.log
SPLITS=/home/james/gpu_splits
EXT=/home/james/corpus_output/extractions

mkdir -p /home/james/logs

# Count successful extractions per split (anything missing -> TODO)
todo_for_split() {
    local split_dir="$1"
    local todo=0
    for f in "$split_dir"/*; do
        [ -e "$f" ] || continue
        local stem=$(basename "$f" .pdf)
        stem=${stem%.png}; stem=${stem%.jpg}; stem=${stem%.jpeg}
        local out="$EXT/${stem}_extracted.json"
        if [ ! -f "$out" ]; then
            todo=$((todo+1))
        elif grep -q '"error"' "$out" 2>/dev/null; then
            todo=$((todo+1))
        fi
    done
    echo $todo
}

is_gpu_alive() {
    # Returns 0 if a python batch_extract_v2 process is using this GPU
    # (against any --posters dir — original split or remaining pool).
    local gpu="$1"
    # Match a process whose CUDA_VISIBLE_DEVICES is set to this GPU.
    # We can't filter env vars directly with pgrep, so check by --posters paths
    # the GPU may be assigned to. Includes the unified pool.
    for path in "gpu${gpu}" "remaining" "gpu3"; do
        if pgrep -f "batch_extract_v2.py.*--posters[ =]/home/james/gpu_splits/${path}" >/dev/null; then
            # Confirm this PID is actually pinned to our GPU
            for pid in $(pgrep -f "batch_extract_v2.py.*--posters[ =]/home/james/gpu_splits/${path}"); do
                local env_gpu=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | grep CUDA_VISIBLE_DEVICES | cut -d= -f2)
                if [ "$env_gpu" = "$gpu" ]; then
                    return 0
                fi
            done
        fi
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
    # Launch as a detached background process inside WSL. The watchdog
    # daemon itself runs forever, so its child processes inherit the
    # daemon's lifetime — they survive SSH disconnects but die if the
    # WSL VM shuts down. setsid + & gives us a fully detached process
    # group so the daemon's wait/sleep loop doesn't wait on the child.
    setsid bash "$script" </dev/null >>"$LOG" 2>&1 &
    disown
}

# Rebuild the unified "remaining" pool every run so newly-finished posters
# fall out and any that errored fall back in. Cheap symlink ops, ~1s.
bash /home/james/build_remaining_pool.sh >/dev/null 2>&1

# Per-GPU schedule: if a GPU isn't running anything, give it work.
# Priority: original assigned split first, fall through to the unified pool.
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
        continue
    fi

    # Primary split done — pivot to the unified remaining pool.
    pool_pending=$(count_pending_in "$SPLITS/remaining")
    if [ "$pool_pending" -gt 0 ]; then
        echo "[$(date)] GPU $gpu primary split done, $pool_pending in pool — pivoting to pool" >> "$LOG"
        # Inline launcher: avoids needing a separate script that hardcodes the GPU.
        setsid bash /home/james/start_pool_gpu.sh "$gpu" </dev/null >>"$LOG" 2>&1 &
        disown
    fi
done
