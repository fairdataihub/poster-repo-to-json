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
    # Returns 0 if a python batch_extract_v2 process is targeting this split.
    # We match on the --posters argument rather than nvidia-smi PIDs, because
    # nvidia-smi compute-apps queries can return blank rows even when a
    # process exists (and the reverse during model load), and SYSTEM context
    # may not see the right /proc entries.
    local gpu="$1"
    if pgrep -f "batch_extract_v2.py.*--posters[ =]/home/james/gpu_splits/gpu${gpu}" >/dev/null; then
        return 0
    fi
    # GPU 2 also runs split gpu3 in its sequence
    if [ "$gpu" = "2" ]; then
        if pgrep -f "batch_extract_v2.py.*--posters[ =]/home/james/gpu_splits/gpu3" >/dev/null; then
            return 0
        fi
    fi
    return 1
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

# GPU 0 -> split gpu0
if ! is_gpu_alive 0; then
    todo=$(ls $EXT/*_extracted.json 2>/dev/null | wc -l)  # quick proxy
    todo_real=$(find $SPLITS/gpu0 -type l -o -type f 2>/dev/null | wc -l)
    if [ "$todo_real" -gt 0 ]; then
        # Quick check: are there pending files in split 0?
        pending_split0=$(comm -23 <(ls $SPLITS/gpu0 | sed 's/\.[^.]*$//' | sort) <(ls $EXT/*_extracted.json 2>/dev/null | sed 's|.*/||;s|_extracted\.json$||' | sort) | wc -l)
        if [ "$pending_split0" -gt 0 ]; then
            echo "[$(date)] GPU 0 dead, $pending_split0 still pending in split 0" >> "$LOG"
            restart_gpu 0 /home/james/start_gpu0_split0.sh
        fi
    fi
fi

# GPU 1 -> split gpu1
if ! is_gpu_alive 1; then
    pending_split1=$(comm -23 <(ls $SPLITS/gpu1 | sed 's/\.[^.]*$//' | sort) <(ls $EXT/*_extracted.json 2>/dev/null | sed 's|.*/||;s|_extracted\.json$||' | sort) | wc -l)
    if [ "$pending_split1" -gt 0 ]; then
        echo "[$(date)] GPU 1 dead, $pending_split1 still pending in split 1" >> "$LOG"
        restart_gpu 1 /home/james/start_gpu1_split1.sh
    fi
fi

# GPU 2 -> splits gpu2 then gpu3
if ! is_gpu_alive 2; then
    pending_split2=$(comm -23 <(ls $SPLITS/gpu2 | sed 's/\.[^.]*$//' | sort) <(ls $EXT/*_extracted.json 2>/dev/null | sed 's|.*/||;s|_extracted\.json$||' | sort) | wc -l)
    pending_split3=$(comm -23 <(ls $SPLITS/gpu3 | sed 's/\.[^.]*$//' | sort) <(ls $EXT/*_extracted.json 2>/dev/null | sed 's|.*/||;s|_extracted\.json$||' | sort) | wc -l)
    if [ "$pending_split2" -gt 0 ] || [ "$pending_split3" -gt 0 ]; then
        echo "[$(date)] GPU 2 dead, splits 2/3 pending: $pending_split2 / $pending_split3" >> "$LOG"
        restart_gpu 2 /home/james/start_gpu2_remaining.sh
    fi
fi
