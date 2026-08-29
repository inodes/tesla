#!/bin/bash
set -eo pipefail

mkdir -p /data/input /data/output

if [ "$1" = "stitch" ] || [ "$1" = "tesla-stitch" ]; then
    shift
    exec /usr/local/bin/tesla-stitch "$@"
fi

if [ $# -eq 0 ]; then
    echo "=============================================================="
    echo "   🚗 ExportDash.cam Tesla Dashcam Processor & Viewer        "
    echo "=============================================================="
    echo "  Web UI:     http://localhost:${PORT:-3000}"
    echo "  Input Dir:  /data/input  (TeslaCam Source - Read Only)"
    echo "  Export Dir: /data/output (Rendered Clips - Read/Write)"
    echo "=============================================================="
    exec npm run dev -- -H 0.0.0.0 -p "${PORT:-3000}"
elif [ "${1#-}" != "$1" ]; then
    exec npm run dev -- -H 0.0.0.0 -p "${PORT:-3000}" "$@"
else
    exec "$@"
fi
