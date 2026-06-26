#!/bin/bash
# Upload a corrected merged dir to a staging path in the posters.science dev
# container, so another machine (e.g. hpcf) can pull it. Uses az CLI creds to
# mint a short-lived SAS, same as the other sync scripts.
#
# Usage: push_merged_to_staging.sh <src_dir> <dest_subpath>
#   e.g. push_merged_to_staging.sh /home/james/data_2025/merged staging/data2025-merged
set -e

SRC="${1:?usage: push_merged_to_staging.sh <src_dir> <dest_subpath>}"
DEST="${2:?usage: push_merged_to_staging.sh <src_dir> <dest_subpath>}"
ACCOUNT=devboxposters
CONTAINER=dev

EXP=$(date -u -d "+3 hour" +%Y-%m-%dT%H:%MZ)
SAS=$(az storage container generate-sas --account-name "$ACCOUNT" --name "$CONTAINER" \
        --permissions racwdl --expiry "$EXP" --auth-mode key -o tsv 2>/dev/null)
echo "$SAS" | grep -q 'sig=' || { echo "FATAL: SAS generation failed (is az logged in?)"; exit 1; }

echo "[$(date)] sync ${SRC} -> ${CONTAINER}/${DEST}"
azcopy sync "${SRC%/}/" "https://${ACCOUNT}.blob.core.windows.net/${CONTAINER}/${DEST}?${SAS}" \
    --recursive --put-md5
echo "[$(date)] PUSHED -> ${CONTAINER}/${DEST}"
