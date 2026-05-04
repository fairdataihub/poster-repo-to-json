#!/bin/bash
# Builds /home/james/gpu_splits/remaining/ with symlinks to every poster
# that doesn't yet have a successful extraction JSON.
#
# Safe to re-run; it rebuilds the pool from scratch each time.

POOL=/home/james/gpu_splits/remaining
EXT=/home/james/corpus_output/extractions
SPLITS=/home/james/gpu_splits

rm -rf "$POOL"
mkdir -p "$POOL"

count=0
for gpu_dir in "$SPLITS"/gpu0 "$SPLITS"/gpu1 "$SPLITS"/gpu2 "$SPLITS"/gpu3; do
    [ -d "$gpu_dir" ] || continue
    for sym in "$gpu_dir"/*; do
        [ -e "$sym" ] || continue
        stem=$(basename "$sym")
        # strip extension
        stem="${stem%.*}"
        out="$EXT/${stem}_extracted.json"
        if [ ! -f "$out" ] || grep -q '"error"' "$out" 2>/dev/null; then
            target=$(readlink -f "$sym")
            ln -sf "$target" "$POOL/$(basename "$sym")"
            count=$((count+1))
        fi
    done
done

echo "Pool size: $count posters"
