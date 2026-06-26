#!/bin/bash
# Push the corpus pieces that are NOT yet on Azure, so hpcf can assemble the full
# working set. Run on ws209 as a user with `az` logged in (e.g. root).
#
# Already on Azure (do NOT re-push): pre-2025 merged/, extractions/, posters/.
# Gaps this fills:
#   - pre-2025 raw deposit metadata        -> dev/metadata
#   - full 2025 merged / metadata / extractions -> dev/work-2025/...
# (2025 PDFs are large and handled separately; date/field fixes don't need them.)
set -e

ACCOUNT=devboxposters
CONTAINER=dev
EXP=$(date -u -d "+6 hour" +%Y-%m-%dT%H:%MZ)
SAS=$(az storage container generate-sas --account-name "$ACCOUNT" --name "$CONTAINER" \
        --permissions racwdl --expiry "$EXP" --auth-mode key -o tsv 2>/dev/null)
echo "$SAS" | grep -q 'sig=' || { echo "FATAL: SAS generation failed (is az logged in?)"; exit 1; }
BASE="https://${ACCOUNT}.blob.core.windows.net/${CONTAINER}"

push() {  # <src_dir> <dest_subpath>
  if [ ! -d "$1" ]; then echo "SKIP (missing): $1"; return; fi
  echo "[$(date)] $1 -> ${CONTAINER}/$2"
  azcopy sync "${1%/}/" "${BASE}/$2?${SAS}" --recursive --put-md5 2>&1 | tail -6
}

push /home/james/metadata                metadata
push /home/james/data_2025/merged        work-2025/merged
push /home/james/data_2025/metadata      work-2025/metadata
push /home/james/data_2025/extractions   work-2025/extractions

echo "[$(date)] workbench push complete"
