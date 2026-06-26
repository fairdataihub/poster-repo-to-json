#!/bin/bash
# Resilient workbench push for a flaky network. Retries each azcopy sync until it
# exits cleanly, so dropped connections can't leave the upload half-done. azcopy
# sync is incremental, so every retry resumes (re-uploads only what's missing).
#
# Run DETACHED so it survives terminal/network drops:
#   nohup bash push_workbench_resilient.sh > /home/james/wbpush.log 2>&1 &
# Watch:   tail -f /home/james/wbpush.log
# Safe to re-run any time.

ACCOUNT=devboxposters
CONTAINER=dev
BASE="https://${ACCOUNT}.blob.core.windows.net/${CONTAINER}"

gen_sas() {
  az storage container generate-sas --account-name "$ACCOUNT" --name "$CONTAINER" \
    --permissions racwdl --expiry "$(date -u -d "+6 hour" +%Y-%m-%dT%H:%MZ)" \
    --auth-mode key -o tsv 2>/dev/null
}

push_until_done() {  # <src_dir> <dest_subpath>
  local src="$1" dest="$2" attempt=0
  if [ ! -d "$src" ]; then echo "[$(date)] SKIP (missing): $src"; return 0; fi
  while true; do
    attempt=$((attempt + 1))
    local SAS; SAS=$(gen_sas)
    if ! echo "$SAS" | grep -q 'sig='; then
      echo "[$(date)] $dest: SAS generation failed (az logged in?), retry in 30s"
      sleep 30; continue
    fi
    echo "[$(date)] $src -> ${CONTAINER}/${dest}  (attempt $attempt)"
    if azcopy sync "${src%/}/" "${BASE}/${dest}?${SAS}" --recursive --put-md5; then
      echo "[$(date)] DONE: ${dest}"
      return 0
    fi
    echo "[$(date)] ${dest}: azcopy exited non-zero, retrying in 20s..."
    sleep 20
  done
}

echo "[$(date)] === resilient workbench push START ==="
push_until_done /home/james/metadata               metadata
push_until_done /home/james/data_2025/merged        work-2025/merged
push_until_done /home/james/data_2025/metadata      work-2025/metadata
push_until_done /home/james/data_2025/extractions   work-2025/extractions
echo "[$(date)] === ALL WORKBENCH PUSHES COMPLETE ==="
