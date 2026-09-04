#!/usr/bin/env bash
# ==============================================================================
# TeslaCam Web Viewer & Stitcher (ExportDash Orchestrator)
# Zero-to-Standup Docker Runner for macOS & Linux
# ==============================================================================

set -eo pipefail

# ANSI color styling
BOLD="$(tput bold 2>/dev/null || echo '')"
RESET="$(tput sgr0 2>/dev/null || echo '')"
GREEN="$(tput setaf 2 2>/dev/null || echo '')"
CYAN="$(tput setaf 6 2>/dev/null || echo '')"
YELLOW="$(tput setaf 3 2>/dev/null || echo '')"
RED="$(tput setaf 1 2>/dev/null || echo '')"
DIM="$(tput dim 2>/dev/null || echo '')"

# Configuration Defaults
PORT=3000
BACKGROUND_MODE=0
CLI_MODE=0
TARGET_SOURCE=""
FORCE_REBUILD=0
EXTRA_ARGS=()

# ==============================================================================
# CLI ARGUMENT PARSER
# ==============================================================================
print_help() {
    cat << 'EOF'
Usage: ./run_exportdash.sh [options] [--cli <command>]

Options:
  -p, --port <port>       Local web server port (default: 3000)
  -s, --source <name>     Target recording volume: 'jowua', 'usb', '2tb', or custom path
  -d, --detach            Run Docker container in background (daemon mode)
  -b, --build             Force rebuild Docker container image
  -c, --cli <cmd>         Run CLI utility directly inside container (e.g. stitch, bash)
  -h, --help              Show this help message

CLI Utility Examples:
  ./run_exportdash.sh                                          # Launch Web UI on http://localhost:3000
  ./run_exportdash.sh --source usb                             # Launch Web UI using glovebox USB
  ./run_exportdash.sh --cli stitch                             # Batch stitch all clips into 4-camera grids
  ./run_exportdash.sh --cli stitch /data/input/SavedClips/XYZ  # Stitch specific event folder
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -s|--source)
            TARGET_SOURCE="$2"
            shift 2
            ;;
        -d|--detach)
            BACKGROUND_MODE=1
            shift
            ;;
        -b|--build)
            FORCE_REBUILD=1
            shift
            ;;
        -c|--cli)
            CLI_MODE=1
            shift
            if [[ "$1" == "stitch" ]]; then
                EXTRA_ARGS+=("tesla-stitch")
                shift
                while [[ $# -gt 0 ]]; do
                    EXTRA_ARGS+=("$1")
                    shift
                done
            else
                while [[ $# -gt 0 ]]; do
                    EXTRA_ARGS+=("$1")
                    shift
                done
            fi
            ;;
        -h|--help)
            print_help
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

echo ""
echo "${CYAN}${BOLD}==============================================================${RESET}"
echo "${CYAN}${BOLD}      🚗 TeslaCam ExportDash Orchestrator (macOS / Docker)     ${RESET}"
echo "${CYAN}${BOLD}==============================================================${RESET}"

# ==============================================================================
# 1. HARDWARE VOLUME RESOLUTION
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE_VOL=""
FOUND_JOWUA=""
FOUND_USB=""

if [[ -d "$SCRIPT_DIR/../ARCHIVE_2TB" ]]; then
    ARCHIVE_VOL="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

for vol in /Volumes/*; do
    [[ -d "$vol" ]] || continue

    if [[ -z "$ARCHIVE_VOL" && -d "$vol/ARCHIVE_2TB" ]]; then
        ARCHIVE_VOL="$vol"
    fi
    if [[ -d "$vol/TeslaCam" && -d "$vol/JOWUA_1TB" ]]; then
        FOUND_JOWUA="$vol"
    fi
    if [[ -d "$vol/TeslaCam" && -d "$vol/TESLA_USB_128GB" ]]; then
        FOUND_USB="$vol"
    fi
done

if [[ -z "$ARCHIVE_VOL" || ! -d "$ARCHIVE_VOL" ]]; then
    echo "${RED}${BOLD}[ERROR]${RESET} 2TB Archive drive (marker: 'ARCHIVE_2TB') is not mounted." >&2
    echo "Please connect the 2TB SSD and ensure it appears in /Volumes/." >&2
    exit 1
fi

echo "${GREEN}✔${RESET} Resolved 2TB Archive Drive:  ${BOLD}${ARCHIVE_VOL}${RESET}"

# Resolve Active Source Volume
SOURCE_VOL=""
if [[ -n "$TARGET_SOURCE" ]]; then
    case "$TARGET_SOURCE" in
        jowua|JOWUA|1tb|1TB)
            SOURCE_VOL="$FOUND_JOWUA"
            ;;
        usb|USB|128gb|128GB|glovebox)
            SOURCE_VOL="$FOUND_USB"
            ;;
        archive|ARCHIVE|2tb|2TB|ssd|SSD)
            SOURCE_VOL="$ARCHIVE_VOL"
            ;;
        *)
            if [[ -d "$TARGET_SOURCE" && -d "$TARGET_SOURCE/TeslaCam" ]]; then
                SOURCE_VOL="$TARGET_SOURCE"
            elif [[ -d "/Volumes/$TARGET_SOURCE" && -d "/Volumes/$TARGET_SOURCE/TeslaCam" ]]; then
                SOURCE_VOL="/Volumes/$TARGET_SOURCE"
            else
                echo "${RED}${BOLD}[ERROR]${RESET} Specified source '$TARGET_SOURCE' is invalid or lacks TeslaCam directory." >&2
                exit 1
            fi
            ;;
    esac
else
    if [[ -n "$FOUND_JOWUA" && -n "$FOUND_USB" ]]; then
        echo "${DIM}ℹ Both Jowua 1TB and Tesla USB 128GB detected. Defaulting to Jowua 1TB.${RESET}"
        echo "${DIM}  (Use '--source usb' to select the 128GB drive)${RESET}"
        SOURCE_VOL="$FOUND_JOWUA"
    elif [[ -n "$FOUND_JOWUA" ]]; then
        SOURCE_VOL="$FOUND_JOWUA"
    elif [[ -n "$FOUND_USB" ]]; then
        SOURCE_VOL="$FOUND_USB"
    elif [[ -n "$ARCHIVE_VOL" ]]; then
        SOURCE_VOL="$ARCHIVE_VOL"
    fi
fi

if [[ -z "$SOURCE_VOL" || ! -d "$SOURCE_VOL/TeslaCam" ]]; then
    echo "${RED}${BOLD}[ERROR]${RESET} No active TeslaCam recording source found." >&2
    exit 1
fi

# ==============================================================================
# 2. DOCKER ENGINE RESOLUTION & ACCESSIBILITY CHECK
# ==============================================================================
resolve_docker_binary() {
    local paths=(
        "$(which docker 2>/dev/null || true)"
        "$HOME/.orbstack/bin/docker"
        "/opt/homebrew/bin/docker"
        "/usr/local/bin/docker"
        "$HOME/.docker/bin/docker"
        "/Applications/OrbStack.app/Contents/MacOS/bin/docker"
        "/Applications/Docker.app/Contents/Resources/bin/docker"
    )
    for p in "${paths[@]}"; do
        if [[ -n "$p" && -x "$p" ]]; then
            echo "$p"
            return 0
        fi
    done
    return 1
}

DOCKER_BIN="$(resolve_docker_binary || true)"

if [[ -z "$DOCKER_BIN" ]]; then
    echo "${RED}${BOLD}[ERROR]${RESET} Docker binary not found in standard PATH." >&2
    echo "Please ensure Docker Desktop or OrbStack is installed." >&2
    exit 1
fi

# Auto-start Docker Desktop daemon if inactive
if ! "$DOCKER_BIN" info >/dev/null 2>&1; then
    echo "${YELLOW}⚙ Docker daemon not running. Launching Docker Desktop...${RESET}"
    if [[ -d "/Applications/Docker.app" ]]; then
        open -g -a Docker || true
    elif [[ -d "/Applications/OrbStack.app" ]]; then
        open -g -a OrbStack || true
    fi

    printf "${DIM}Waiting for Docker daemon to become responsive...${RESET}"
    TRIES=0
    while ! "$DOCKER_BIN" info >/dev/null 2>&1; do
        sleep 2
        printf "."
        ((TRIES++))
        if [[ $TRIES -gt 30 ]]; then
            echo ""
            echo "${RED}${BOLD}[ERROR]${RESET} Timed out waiting for Docker engine." >&2
            exit 1
        fi
    done
    echo ""
fi

echo "${GREEN}✔${RESET} Container Engine Active:    ${BOLD}$DOCKER_BIN${RESET}"

# AUTOMATIC MOUNTPOINT VALIDATION:
# Test if Docker VM can read the selected SOURCE_VOL. If not shared, auto-switch to 2TB Archive.
if ! "$DOCKER_BIN" run --rm -v "${SOURCE_VOL}/TeslaCam:/data/input:ro" alpine ls /data/input >/dev/null 2>&1; then
    if [[ "$SOURCE_VOL" != "$ARCHIVE_VOL" && -d "$ARCHIVE_VOL/TeslaCam" ]]; then
        echo "${YELLOW}ℹ Source '${SOURCE_VOL}' is not shared into Docker VM.${RESET}"
        echo "${GREEN}✔${RESET} Auto-switching mountpoint to 2TB Archive SSD (${ARCHIVE_VOL})..."
        SOURCE_VOL="$ARCHIVE_VOL"
    fi
fi

echo "${GREEN}✔${RESET} Resolved TeslaCam Source:   ${BOLD}${SOURCE_VOL}${RESET}"

EXPORTS_DIR="$ARCHIVE_VOL/TeslaCam_Exports"
mkdir -p "$EXPORTS_DIR"
echo "${GREEN}✔${RESET} Resolved Export Directory:  ${BOLD}${EXPORTS_DIR}${RESET}"

REPO_DIR="$ARCHIVE_VOL/Tools/exportdash.cam"
if [[ ! -d "$REPO_DIR" ]]; then
    echo "${RED}${BOLD}[ERROR]${RESET} ExportDash repository directory not found at $REPO_DIR" >&2
    exit 1
fi

# Normalize path arguments inside EXTRA_ARGS for in-container execution
NORMALIZED_ARGS=()
for arg in "${EXTRA_ARGS[@]}"; do
    # Remove host /Volumes/.../TeslaCam prefixes
    clean_arg="$arg"
    clean_arg="${clean_arg#/Volumes/*/TeslaCam/}"
    clean_arg="${clean_arg#/Volumes/*/}"
    clean_arg="${clean_arg#/data/input/TeslaCam/}"
    if [[ "$clean_arg" =~ ^(SavedClips|SentryClips|RecentClips|EncryptedClips)/ ]]; then
        NORMALIZED_ARGS+=("/data/input/$clean_arg")
    elif [[ "$clean_arg" =~ ^/data/input/ ]]; then
        NORMALIZED_ARGS+=("$clean_arg")
    else
        NORMALIZED_ARGS+=("$arg")
    fi
done

# ==============================================================================
# 3. DOCKER IMAGE BUILD / VALIDATION
# ==============================================================================
IMAGE_NAME="exportdash-cam:local"
IMAGE_EXISTS=$("$DOCKER_BIN" images -q "$IMAGE_NAME" 2>/dev/null || true)

if [[ -z "$IMAGE_EXISTS" || "$FORCE_REBUILD" -eq 1 ]]; then
    echo ""
    echo "${YELLOW}${BOLD}⚙ Building Docker image '${IMAGE_NAME}'...${RESET}"
    export COPYFILE_DISABLE=1
    find "$REPO_DIR" -type f -name "._*" -delete 2>/dev/null || true
    dot_clean -m "$REPO_DIR" 2>/dev/null || true
    "$DOCKER_BIN" build -t "$IMAGE_NAME" "$REPO_DIR"
    echo "${GREEN}✔ Image built successfully!${RESET}"
    echo ""
fi

# ==============================================================================
# 4. RUN CONTAINER
# ==============================================================================
echo "${CYAN}--------------------------------------------------------------${RESET}"
echo "${BOLD}Starting Container:${RESET} ${IMAGE_NAME}"
echo "  ${BOLD}Input Mount (RO):${RESET}  ${SOURCE_VOL}/TeslaCam  -> /data/input"
echo "  ${BOLD}Export Mount (RW):${RESET} ${EXPORTS_DIR}        -> /data/output"

# Smart TTY detection
DOCKER_RUN_FLAGS=()
if [[ "$BACKGROUND_MODE" -eq 1 ]]; then
    DOCKER_RUN_FLAGS+=("-d")
else
    DOCKER_RUN_FLAGS+=("--rm" "--init")
    if [[ -t 0 && -t 1 ]]; then
        DOCKER_RUN_FLAGS+=("-it")
    else
        DOCKER_RUN_FLAGS+=("-i")
    fi
fi

if [[ "$CLI_MODE" -eq 1 || ${#NORMALIZED_ARGS[@]} -gt 0 ]]; then
    echo "  ${BOLD}Mode:${RESET}              CLI Execution"
    echo "${CYAN}--------------------------------------------------------------${RESET}"
    exec "$DOCKER_BIN" run "${DOCKER_RUN_FLAGS[@]}" \
        -v "${SOURCE_VOL}/TeslaCam:/data/input:ro" \
        -v "${EXPORTS_DIR}:/data/output" \
        "$IMAGE_NAME" "${NORMALIZED_ARGS[@]}"
else
    echo "  ${BOLD}Mode:${RESET}              Web UI Service"
    echo "  ${BOLD}Local URL:${RESET}         ${CYAN}http://localhost:${PORT}${RESET}"
    echo "${CYAN}--------------------------------------------------------------${RESET}"
    if [[ "$BACKGROUND_MODE" -eq 0 ]]; then
        echo "${DIM}Press Ctrl+C at any time to stop the container.${RESET}"
    else
        echo "${DIM}Container running in background. Stop with: docker stop <id>${RESET}"
    fi
    echo ""

    (sleep 2 && open "http://localhost:${PORT}" 2>/dev/null || true) &

    exec "$DOCKER_BIN" run "${DOCKER_RUN_FLAGS[@]}" \
        -p "${PORT}:3000" \
        -v "${SOURCE_VOL}/TeslaCam:/data/input:ro" \
        -v "${EXPORTS_DIR}:/data/output" \
        "$IMAGE_NAME"
fi
