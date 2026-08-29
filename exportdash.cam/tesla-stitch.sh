#!/bin/bash
set -eo pipefail

if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    cat << 'HELP_EOF'
TeslaCam CLI Batch Video Stitcher
Combines 4-camera clips (Front, Rear, Left, Right) into a 2x2 synchronized grid.

Usage:
  tesla-stitch [input_dir] [output_dir]

Defaults:
  input_dir:  /data/input  (TeslaCam recordings)
  output_dir: /data/output (Rendered MP4s)
HELP_EOF
    exit 0
fi

INPUT_DIR="${1:-/data/input}"
OUTPUT_DIR="${2:-/data/output}"

echo "=============================================================="
echo "          TeslaCam CLI Batch Video Stitcher                  "
echo "=============================================================="
echo "Input Directory:  $INPUT_DIR"
echo "Output Directory: $OUTPUT_DIR"
echo "=============================================================="

if [ ! -d "$INPUT_DIR" ]; then
    echo "Error: Input directory $INPUT_DIR not found." >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

# Find all front camera clips
FRONT_CLIPS=$(find "$INPUT_DIR" -type f -name "*-front.mp4" 2>/dev/null | sort || true)
COUNT=$(echo "$FRONT_CLIPS" | grep -v '^$' | wc -l | tr -d ' ')

if [ "$COUNT" -eq 0 ]; then
    echo "No *-front.mp4 video files found in $INPUT_DIR."
    exit 0
fi

echo "Found $COUNT timestamped clips to process."

PROCESSED=0
for FRONT in $FRONT_CLIPS; do
    BASE_PREFIX="${FRONT%-front.mp4}"
    TIMESTAMP=$(basename "$BASE_PREFIX")
    SUBDIR=$(basename "$(dirname "$FRONT")")
    
    BACK="${BASE_PREFIX}-back.mp4"
    LEFT="${BASE_PREFIX}-left_repeater.mp4"
    RIGHT="${BASE_PREFIX}-right_repeater.mp4"
    
    TARGET_OUT="${OUTPUT_DIR}/${SUBDIR}_${TIMESTAMP}_4way.mp4"
    
    if [ -f "$TARGET_OUT" ]; then
        echo "[$((PROCESSED+1))/$COUNT] Skipping already stitched: $(basename "$TARGET_OUT")"
        PROCESSED=$((PROCESSED+1))
        continue
    fi
    
    echo "[$((PROCESSED+1))/$COUNT] Stitching 4-way grid: $TIMESTAMP ($SUBDIR)..."
    
    if [ -f "$BACK" ] && [ -f "$LEFT" ] && [ -f "$RIGHT" ]; then
        ffmpeg -y -hide_banner -loglevel error \
            -i "$FRONT" -i "$BACK" -i "$LEFT" -i "$RIGHT" \
            -filter_complex "\
                [0:v]scale=960:720,drawtext=text='Front':fontcolor=white:fontsize=24:x=20:y=20:box=1:boxcolor=black@0.5[v0]; \
                [1:v]scale=960:720,drawtext=text='Rear':fontcolor=white:fontsize=24:x=20:y=20:box=1:boxcolor=black@0.5[v1]; \
                [2:v]scale=960:720,drawtext=text='Left Repeater':fontcolor=white:fontsize=24:x=20:y=20:box=1:boxcolor=black@0.5[v2]; \
                [3:v]scale=960:720,drawtext=text='Right Repeater':fontcolor=white:fontsize=24:x=20:y=20:box=1:boxcolor=black@0.5[v3]; \
                [v0][v1]hstack=inputs=2[top]; \
                [v2][v3]hstack=inputs=2[bottom]; \
                [top][bottom]vstack=inputs=2[outv]" \
            -map "[outv]" -c:v libx264 -preset fast -crf 22 -c:a aac \
            "$TARGET_OUT"
        echo "  --> Saved: $(basename "$TARGET_OUT")"
    else
        echo "  [Warning] Incomplete camera set for $TIMESTAMP, copying front angle only."
        ffmpeg -y -hide_banner -loglevel error -i "$FRONT" -c copy "$TARGET_OUT"
    fi
    
    PROCESSED=$((PROCESSED+1))
done

echo "=============================================================="
echo "Batch stitching complete! Stitched clips saved to: $OUTPUT_DIR"
echo "=============================================================="
