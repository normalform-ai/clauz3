#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
SCREEN_WIDTH="${SCREEN_WIDTH:-1280}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-800}"
SCREEN_DEPTH="${SCREEN_DEPTH:-24}"
CDP_PORT="${CDP_PORT:-9222}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
START_URL="${BROWSER_START_URL:-about:blank}"

rm -f "/tmp/.X${DISPLAY#:}-lock"

Xvfb "${DISPLAY}" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" -ac +extension RANDR &
sleep 0.5

fluxbox >/dev/null 2>&1 &

# Launch Chromium with persistent profile and local CDP port
chromium \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --user-data-dir=/data/profile \
    --window-position=0,0 \
    --window-size="${SCREEN_WIDTH},${SCREEN_HEIGHT}" \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port=9221 \
    --no-first-run \
    --no-default-browser-check \
    "${START_URL}" &

# Forward container network CDP port to 127.0.0.1:9221 so dot-workspace can connect
socat TCP-LISTEN:"${CDP_PORT}",fork,reuseaddr TCP:127.0.0.1:9221 &

# Expose X11 display over VNC + noVNC WebSockets for live view and "Take Over" mode
x11vnc -display "${DISPLAY}" -forever -shared -nopw -rfbport "${VNC_PORT}" -quiet &
exec websockify --web /usr/share/novnc "${NOVNC_PORT}" "127.0.0.1:${VNC_PORT}"
