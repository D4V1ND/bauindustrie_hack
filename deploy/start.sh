#!/bin/sh
# LV_DATA: where results and uploads live. Point it at a persistent volume (e.g. /data) so they survive a redeploy.
set -e
export LV_DATA="${LV_DATA:-/app/data}"
mkdir -p "$LV_DATA/library" "$LV_DATA/out" "$LV_DATA/uploads"
if [ -z "$(ls -A "$LV_DATA/library")" ]; then cp /app/seed/library/*.json "$LV_DATA/library/"; fi
# LV_REMOVE: comma-separated project IDs whose results and uploads are deleted at startup (one-off cleanup).
for p in $(echo "${LV_REMOVE:-}" | tr ',' ' '); do
  case "$p" in *[!A-Za-z0-9-]*|"") echo "LV_REMOVE: skipped '$p'"; continue;; esac
  rm -rf "$LV_DATA/out/$p".* "$LV_DATA/out/$p"_* "$LV_DATA/uploads/$p"
  echo "LV_REMOVE: removed $p"
done
exec python -m uvicorn lv.api:app --host 0.0.0.0 --port "${PORT:-8000}"
