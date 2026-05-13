#!/bin/bash
set -e

MERGED=/home/james/corpus_output/merged
POSTERS=/home/james/posters
ACCOUNT=devboxposters
CONTAINER=dev
BASE="https://${ACCOUNT}.blob.core.windows.net/${CONTAINER}"
LOG=/home/james/logs/pdf_sync_$(date +%Y%m%d_%H%M%S).log
STAGING=/tmp/pdf_staging
mkdir -p /home/james/logs

echo "[$(date)] Building matched PDF list..." | tee -a "$LOG"

# Build staging dirs with symlinks to matched PDFs only
rm -rf "$STAGING"
mkdir -p "$STAGING/zenodo" "$STAGING/figshare"

matched=0
missing=0
missing_ids=""

for src in zenodo figshare; do
    for json in "$MERGED/$src"/*_complete.json; do
        [ -f "$json" ] || continue
        id=$(basename "$json" _complete.json)
        # PDF is named {source}_{id}_*.pdf
        pdf=$(find "$POSTERS/$src" -maxdepth 1 -name "${src}_${id}_*.pdf" -type f 2>/dev/null | head -1)
        if [ -n "$pdf" ]; then
            ln -s "$pdf" "$STAGING/$src/$(basename "$pdf")"
            matched=$((matched + 1))
        else
            missing=$((missing + 1))
            if [ "$missing" -le 30 ]; then
                missing_ids="$missing_ids $src/$id"
            fi
        fi
    done
done

staged_z=$(find "$STAGING/zenodo" -type l 2>/dev/null | wc -l)
staged_f=$(find "$STAGING/figshare" -type l 2>/dev/null | wc -l)

echo "[$(date)] Matched: $matched, Missing: $missing" | tee -a "$LOG"
echo "  Staged zenodo: $staged_z" | tee -a "$LOG"
echo "  Staged figshare: $staged_f" | tee -a "$LOG"
if [ -n "$missing_ids" ]; then
    echo "  Sample missing:$missing_ids" | tee -a "$LOG"
fi

# Calculate size
echo "" | tee -a "$LOG"
echo "[$(date)] Calculating total size..." | tee -a "$LOG"
total_bytes=0
for f in "$STAGING"/zenodo/* "$STAGING"/figshare/*; do
    [ -L "$f" ] || continue
    sz=$(stat -L -c%s "$f" 2>/dev/null || echo 0)
    total_bytes=$((total_bytes + sz))
done
total_gb=$(echo "scale=1; $total_bytes / 1073741824" | bc)
echo "  Total size: ${total_gb} GB" | tee -a "$LOG"

# Generate SAS token
EXPIRY=$(date -u -d "+4 hours" +%Y-%m-%dT%H:%MZ)
SAS=$(az storage container generate-sas \
    --account-name "$ACCOUNT" --name "$CONTAINER" \
    --permissions racwdl --expiry "$EXPIRY" \
    --auth-mode key -o tsv 2>/dev/null)

if [ -z "$SAS" ]; then
    echo "[$(date)] FATAL: failed to generate SAS token" | tee -a "$LOG"
    exit 1
fi
echo "[$(date)] SAS token generated (expires $EXPIRY)" | tee -a "$LOG"

# Upload using azcopy copy with --follow-symlinks
echo "" | tee -a "$LOG"
echo "[$(date)] Starting upload..." | tee -a "$LOG"

azcopy copy "$STAGING/zenodo/*" \
    "${BASE}/posters/zenodo?${SAS}" \
    --follow-symlinks --put-md5 --overwrite=ifSourceNewer \
    --log-level=ERROR 2>&1 | tee -a "$LOG"

echo "" | tee -a "$LOG"

azcopy copy "$STAGING/figshare/*" \
    "${BASE}/posters/figshare?${SAS}" \
    --follow-symlinks --put-md5 --overwrite=ifSourceNewer \
    --log-level=ERROR 2>&1 | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "[$(date)] PDF sync complete" | tee -a "$LOG"

# Cleanup
rm -rf "$STAGING"
