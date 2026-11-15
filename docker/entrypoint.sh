#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_DIR="${CLAUZ3_WORKSPACE_DIR:-/workspace}"
PORT="${CLAUZ3_PORT:-8080}"
HOST="${CLAUZ3_HOST:-0.0.0.0}"
AGENT="${CLAUZ3_AGENT:-claude}"
DEFAULT_LIBRARIES="${CLAUZ3_DEFAULT_LIBRARIES:-stdlib:filesystem,stdlib:web_fetch}"

mkdir -p "${WORKSPACE_DIR}"
cd "${WORKSPACE_DIR}"

# Bootstrap default standard libraries on first boot if none are installed
if [ ! -d "${WORKSPACE_DIR}/.clauz3/tools" ] && [ -n "${DEFAULT_LIBRARIES}" ]; then
    IFS=',' read -ra LIBS <<< "${DEFAULT_LIBRARIES}"
    for lib in "${LIBS[@]}"; do
        lib_trimmed="$(echo "${lib}" | xargs)"
        if [ -n "${lib_trimmed}" ]; then
            echo "[clauz3-dot] Installing library ${lib_trimmed} into ${WORKSPACE_DIR}..." >&2
            clauz3 install "${lib_trimmed}"
        fi
    done
fi

ARGS=(
    "ui"
    "${AGENT}"
    "--no-browser"
    "--host" "${HOST}"
    "--port" "${PORT}"
)

if [ -n "${CLAUZ3_BROWSER_URL:-}" ]; then
    ARGS+=("--browser-url" "${CLAUZ3_BROWSER_URL}")
fi

if [ -n "${CLAUZ3_MODEL:-}" ]; then
    ARGS+=("--model" "${CLAUZ3_MODEL}")
fi

if [ -n "${CLAUZ3_POLICY:-}" ]; then
    ARGS+=("--policy" "${CLAUZ3_POLICY}")
fi

exec clauz3 "${ARGS[@]}" "$@"
