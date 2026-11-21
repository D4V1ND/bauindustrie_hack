#!/bin/sh
# LV_DATA: where results and uploads live. Point it at a persistent volume (e.g. /data) so they survive a redeploy.
set -e
export LV_DATA="${LV_DATA:-/app/data}"
mkdir -p "$LV_DATA/library" "$LV_DATA/out" "$LV_DATA/uploads"
if [ -z "$(ls -A "$LV_DATA/library")" ]; then cp /app/seed/library/*.json "$LV_DATA/library/"; fi
exec python -m uvicorn lv.api:app --host 0.0.0.0 --port "${PORT:-8000}"
