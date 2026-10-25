#!/bin/bash
# launch.sh - starts the ITAD laptop tool
# It finds its own folder automatically (works in /opt or /opt/itad-laptop).
# New layout: starts laptop_window.py. If that fails, falls back to itad_run.py.

SELF="$(readlink -f "$0")"
APP_DIR="$(dirname "$SELF")"
APP="$APP_DIR/itad_run.py"
WINDOW="$APP_DIR/laptop_window.py"
LOG="/tmp/itad-launch.log"
WINLOG="/tmp/itad-window.log"
DATA_LABEL="ITAD_DATA"
DATA_MNT="/mnt/itad"

log() { echo "$(date '+%F %T') $*" >> "$LOG" 2>/dev/null; }

# ------------------------------------------------------------
# INNER MODE: old text screen (fallback), runs inside xterm
# ------------------------------------------------------------
if [ "${1:-}" = "--inner" ]; then
    export TERM="${TERM:-xterm-256color}"
    clear

    CONTINUE_FLAG=""

    while true; do
        if ! command -v python3 >/dev/null 2>&1; then
            echo "ERROR: python3 is not installed on this USB."
        elif [ ! -f "$APP" ]; then
            echo "ERROR: $APP not found."
            echo "Files in $APP_DIR:"
            ls -1 "$APP_DIR" 2>/dev/null
        else
            cd "$APP_DIR" || true
            log "starting itad_run.py with flags: $CONTINUE_FLAG"
            sudo ITAD_DATA="$ITAD_DATA" python3 "$APP" $CONTINUE_FLAG
            log "itad_run.py exited with code $?"
        fi

        echo
        echo "=============================================="
        echo "  ITAD tool has stopped. What now?"
        echo "=============================================="
        echo "  [R] Run the tool again (Check -> Wipe -> Test)"
        echo "  [C] CONTINUE anyway (Bypass Check, go to Wipe)"
        echo "  [S] Open a shell (for technicians)"
        echo "  [B] Reboot"
        echo "  [P] Power off"
        echo
        read -r -n1 -p "  Choose R / C / S / B / P: " choice
        echo
        case "${choice,,}" in
            r) CONTINUE_FLAG=""; clear ;;
            c) CONTINUE_FLAG="--continue-on-blocked"; clear ;;
            s) clear; echo "Type 'exit' to come back to this menu."; bash ;;
            b) sync; sudo reboot ;;
            p) sync; sudo poweroff ;;
            *) CONTINUE_FLAG=""; clear ;;
        esac
    done
    exit 0
fi

# ------------------------------------------------------------
# OUTER MODE: prepare the system, then open the tool
# ------------------------------------------------------------
touch "$LOG"
log "launch.sh started from $APP_DIR"

# 1. Never let the screen go blank or sleep
sudo setterm -blank 0 -powerdown 0 2>/dev/null || true

# 2. Find and mount the ITAD_DATA stick (for reports)
dev="$(blkid -L "$DATA_LABEL" 2>/dev/null || true)"
if [ -n "$dev" ]; then
    sudo mkdir -p "$DATA_MNT"
    sudo mountpoint -q "$DATA_MNT" || sudo mount -o rw "$dev" "$DATA_MNT" 2>>"$LOG"
    sudo chmod 777 "$DATA_MNT"
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

# 5. NEW LAYOUT: open the real window (laptop_window.py)
if [ -n "${DISPLAY:-}" ] && [ -f "$WINDOW" ] && command -v python3 >/dev/null 2>&1 \
   && command -v xset >/dev/null 2>&1 && xset q >/dev/null 2>&1; then
    log "starting laptop_window.py on $DISPLAY"
    cd "$APP_DIR" || true
    sudo -E ITAD_DATA="$ITAD_DATA" DISPLAY="$DISPLAY" python3 "$WINDOW" >"$WINLOG" 2>&1
    code=$?
    log "laptop_window.py exited with code $code (details in $WINLOG)"
    if [ "$code" -eq 0 ]; then
        exit 0
    fi
    log "window failed, falling back to the old text screen"
else
    log "window not started (no screen, or $WINDOW missing), using old text screen"
fi

# 6. FALLBACK: old text screen in a fullscreen xterm, or plain console
if [ -n "${DISPLAY:-}" ] && command -v xterm >/dev/null 2>&1 && xset q >/dev/null 2>&1; then
    log "opening fullscreen xterm on $DISPLAY"
    exec xterm -fullscreen +sb \
        -title "ITAD Laptop Tool" \
        -fa "DejaVu Sans Mono" -fs 13 \
        -bg "#050a14" -fg "#e6f1ff" \
        -e bash "$SELF" --inner
else
    log "no graphical screen, running on the console"
    exec bash "$SELF" --inner
fi
