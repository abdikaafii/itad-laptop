#!/bin/bash
# launch.sh - starts the ITAD laptop tool
# Repo location: build/config/includes.chroot/opt/launch.sh
# On the boot USB it lives at: /opt/launch.sh
# Start it with:  bash /opt/launch.sh

APP_DIR="/opt"
APP="$APP_DIR/itad_run.py"
LOG="/var/log/itad-launch.log"
DATA_LABEL="ITAD_DATA"
DATA_MNT="/mnt/itad"

log() { echo "$(date '+%F %T') $*" >> "$LOG" 2>/dev/null; }

# ------------------------------------------------------------
# INNER MODE: this part runs inside the fullscreen window
# ------------------------------------------------------------
if [ "${1:-}" = "--inner" ]; then
    export TERM="${TERM:-xterm-256color}"
    clear

    while true; do
        if ! command -v python3 >/dev/null 2>&1; then
            echo "ERROR: python3 is not installed on this USB."
        elif [ ! -f "$APP" ]; then
            echo "ERROR: $APP not found."
            echo "Files in $APP_DIR:"
            ls -1 "$APP_DIR" 2>/dev/null
        else
            cd "$APP_DIR" || true
            log "starting itad_run.py"
            python3 "$APP"
            log "itad_run.py exited with code $?"
        fi

        echo
        echo "=============================================="
        echo "  ITAD tool has stopped. What now?"
        echo "=============================================="
        echo "  [R] Run the tool again"
        echo "  [S] Open a shell (for technicians)"
        echo "  [B] Reboot"
        echo "  [P] Power off"
        echo
        read -r -n1 -p "  Choose R / S / B / P: " choice
        echo
        case "${choice,,}" in
            r) clear ;;
            s) clear; echo "Type 'exit' to come back to this menu."; bash ;;
            b) sync; reboot ;;
            p) sync; poweroff ;;
            *) clear ;;
        esac
    done
    exit 0
fi

# ------------------------------------------------------------
# OUTER MODE: prepare the system, then open the window
# ------------------------------------------------------------
mkdir -p "$(dirname "$LOG")" 2>/dev/null
log "launch.sh started"

# 1. Never let the screen go blank or sleep
setterm -blank 0 -powerdown 0 2>/dev/null || true

# 2. Find and mount the ITAD_DATA stick (for reports)
dev="$(blkid -L "$DATA_LABEL" 2>/dev/null || true)"
if [ -n "$dev" ]; then
    mkdir -p "$DATA_MNT"
    mountpoint -q "$DATA_MNT" || mount -o rw "$dev" "$DATA_MNT" 2>>"$LOG"
    export ITAD_DATA="$DATA_MNT"
    log "ITAD_DATA stick mounted from $dev"
else
    mkdir -p /tmp/itad-reports
    export ITAD_DATA="/tmp/itad-reports"
    log "no ITAD_DATA stick found, using /tmp/itad-reports"
fi

# 3. Wait for the graphical screen (up to 30 seconds)
if [ -z "${DISPLAY:-}" ] && [ -S /tmp/.X11-unix/X0 ]; then
    export DISPLAY=":0"
fi
for i in $(seq 1 30); do
    if [ -n "${DISPLAY:-}" ] && command -v xset >/dev/null 2>&1 && xset q >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# 4. Screen settings and hidden mouse pointer
if command -v xset >/dev/null 2>&1; then
    xset s off 2>/dev/null
    xset -dpms 2>/dev/null
    xset s noblank 2>/dev/null
fi
if command -v unclutter >/dev/null 2>&1; then
    unclutter -idle 0.5 -root >/dev/null 2>&1 &
fi

# 5. Open the tool: fullscreen window if possible, otherwise plain console
if [ -n "${DISPLAY:-}" ] && command -v xterm >/dev/null 2>&1 && xset q >/dev/null 2>&1; then
    log "opening fullscreen xterm on $DISPLAY"
    exec xterm -fullscreen +sb \
        -title "ITAD Laptop Tool" \
        -fa "DejaVu Sans Mono" -fs 13 \
        -bg "#050a14" -fg "#e6f1ff" \
        -e bash "$0" --inner
else
    log "no graphical screen, running on the console"
    exec bash "$0" --inner
fi
