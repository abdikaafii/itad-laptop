#!/bin/bash
# build_usb.sh — create a bootable ITAD live USB from a stock Ubuntu ISO
# WARNING: this DESTROYS everything on the target USB device.

set -euo pipefail
ITAD_VERSION="1.0.1" # Updated version

# --- Check for root privileges ---
if [ "$EUID" -ne 0 ]; then
  echo "Error: Please run this script with sudo: sudo ./build_usb.sh" >&2
  exit 1
fi

# --- Check for required dependencies ---
for cmd in dd parted wipefs partprobe mkfs.ext4 mkfs.vfat; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: Required command '$cmd' is not installed." >&2
        exit 1
    fi
done

if [ -t 1 ]; then
    B=$'\033[1m'; R=$'\033[0;31m'; G=$'\033[0;32m'; Y=$'\033[0;33m'
    C=$'\033[0;36m'; N=$'\033[0m'
else
    B=""; R=""; G=""; Y=""; C=""; N=""
fi
say()  { echo -e "${G}▶${N} $*"; }
warn() { echo -e "${Y}▲${N} $*"; }
die()  { echo -e "${R}✖${N} $*" >&2; exit 1; }

usage() {
    cat <<EOF
build_usb.sh — create a bootable ITAD live USB

Usage:
  sudo $0 --iso <ubuntu.iso> --device /dev/sdX [--yes]

Example:
  sudo $0 --iso ~/ubuntu-24.04-desktop-amd64.iso --device /dev/sdb

WARNING: this ERASES /dev/sdX completely. Verify with: lsblk -o NAME,SIZE,MODEL
EOF
    exit "${1:-0}"
}

ISO=""; DEVICE=""; SKIP_CONFIRM=0
while [ $# -gt 0 ]; do
    case "$1" in
        --iso)    ISO="$2";    shift 2 ;;
        --device) DEVICE="$2"; shift 2 ;;
        --yes|-y) SKIP_CONFIRM=1; shift ;;
        --help|-h) usage 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[ -n "$ISO" ]    || usage 1
[ -n "$DEVICE" ] || usage 1

# ---------- validate ----------
say "validating inputs"
[ -f "$ISO" ]    || die "ISO not found: $ISO"
ISO_SIZE=$(stat -c %s "$ISO")
[ "$ISO_SIZE" -gt 1000000000 ] || die "ISO smaller than 1 GB — wrong file? Make sure you downloaded the Ubuntu Desktop ISO, not a minimal/Debian ISO."

[ -b "$DEVICE" ] || die "not a block device: $DEVICE"
DEV_NAME=$(basename "$DEVICE")

REMOVABLE="/sys/block/$DEV_NAME/removable"
if [ -f "$REMOVABLE" ] && [ "$(cat "$REMOVABLE")" != "1" ]; then
    die "$DEVICE is NOT removable (internal disk?). Refusing."
fi

if findmnt -rn -S "$DEVICE" 2>/dev/null | grep -q .; then
    warn "device has mounted partitions:"
    findmnt -rn -S "$DEVICE" || true
    die "unmount them first: sudo umount ${DEVICE}*"
fi

ROOT_SRC=$(findmnt -n -o SOURCE /)
case "$ROOT_SRC" in
    *"$DEV_NAME"*|*"$DEVICE"*) die "$DEVICE appears to host /. Refusing." ;;
esac

USB_BYTES=$(blockdev --getsize64 "$DEVICE")
USB_GB=$(awk "BEGIN { printf \"%.1f\", $USB_BYTES/1e9 }")
ISO_GB=$(awk "BEGIN { printf \"%.1f\", $ISO_SIZE/1e9 }")
if [ "$USB_BYTES" -lt $((ISO_SIZE * 2)) ]; then
    die "USB is only ${USB_GB}GB; ISO is ${ISO_GB}GB. Need ≥$(awk "BEGIN { printf \"%.0f\", $ISO_SIZE*2/1e9 }")GB."
fi

USB_MODEL=$(lsblk -dno MODEL "$DEVICE" 2>/dev/null | tr -s ' ' || echo "unknown")

echo
echo "================================================================"
echo "  ITAD USB BUILDER v$ITAD_VERSION"
echo "================================================================"
echo "  USB device:  $DEVICE"
echo "  Model:       $USB_MODEL"
echo "  Size:        ${USB_GB} GB"
echo "  ISO:         $ISO"
echo "  ISO size:    ${ISO_GB} GB"
echo "================================================================"
echo

if [ "$SKIP_CONFIRM" -eq 0 ]; then
    echo -e "${R}WARNING:${N} ALL DATA ON $DEVICE WILL BE DESTROYED."
    read -rp "Type YES to continue: " ans
    [ "$ans" = "YES" ] || die "aborted"
fi

# ---------- wipe + write ISO ----------
say "wiping old signatures"
wipefs -a "$DEVICE" >/dev/null 2>&1 || true

say "writing ISO (5–15 min depending on USB speed)"
dd if="$ISO" of="$DEVICE" bs=4M status=progress oflag=sync conv=fsync
sync

sleep 1
partprobe "$DEVICE" 2>/dev/null || true
sleep 1

# ---------- find free space start ----------
say "analysing free space"
FREE_START=$(parted -s "$DEVICE" unit MiB print free 2>/dev/null \
             | grep -i "free space" | head -1 \
             | awk '{print $1}' | tr -d 'MiB ')

if [ -z "${FREE_START:-}" ] || ! [ "$FREE_START" -ge 100 ] 2>/dev/null; then
    die "could not locate free space after ISO.
     This ISO may not leave free space, or parted parsing failed.
     Try a larger USB, or partition manually with parted + gparted."
fi
say "free space starts at ${FREE_START} MiB"

USB_MIB=$((USB_BYTES / 1048576))
CASPER_END_MIB=$((USB_MIB - 4096))

if [ "$CASPER_END_MIB" -le "$FREE_START" ]; then
    die "not enough free space for both casper-rw and ITAD partitions"
fi

# ---------- casper-rw (persistence) ----------
say "creating persistence partition (casper-rw)"
parted -s "$DEVICE" mkpart primary ext4 "${FREE_START}MiB" "${CASPER_END_MIB}MiB"
partprobe "$DEVICE" 2>/dev/null || true
sleep 1

CASPER_PART="${DEVICE}2"; [ -b "$CASPER_PART" ] || CASPER_PART="${DEVICE}p2"
[ -b "$CASPER_PART" ] || die "could not find casper-rw partition"
mkfs.ext4 -F -L casper-rw "$CASPER_PART" >/dev/null

# ---------- ITAD data partition ----------
say "creating ITAD data partition"
parted -s "$DEVICE" mkpart primary fat32 "${CASPER_END_MIB}MiB" 100%
partprobe "$DEVICE" 2>/dev/null || true
sleep 1

ITAD_PART="${DEVICE}3"; [ -b "$ITAD_PART" ] || ITAD_PART="${DEVICE}p3"
[ -b "$ITAD_PART" ] || die "could not find ITAD partition"
mkfs.vfat -F32 -n ITAD "$ITAD_PART" >/dev/null

# ---------- copy files ----------
say "copying ITAD tools"
TMP_MNT=$(mktemp -d)
mount "$ITAD_PART" "$TMP_MNT"

# Check if first_boot.sh exists before the loop
if [ ! -f "first_boot.sh" ]; then
    warn "CRITICAL: first_boot.sh is missing in this directory!"
    warn "The USB will build, but the ITAD setup will NOT run automatically on first boot."
fi

for f in laptop_check.py laptop_wipe.py laptop_test.py \
         itad_run.py itad_report.py first_boot.sh \
         QUICKREF.md TEST_PLAN.md build_usb.sh; do
    if [ -f "$f" ]; then
        cp "$f" "$TMP_MNT/"
        case "$f" in
            *.py|*.sh) chmod 755 "$TMP_MNT/$f" ;;
        esac
        echo "  Copied: $f"
    else
        warn "missing: $f"
    fi
done

sync
umount "$TMP_MNT"
rmdir "$TMP_MNT"

echo
echo "================================================================"
echo -e "  ${G}SUCCESS${N}"
echo "================================================================"
echo "  USB ready: $DEVICE"
echo
echo "  Next steps:"
echo "    1. Boot a target laptop from this USB (Choose 'Try Ubuntu')"
echo "    2. Open a terminal (Ctrl+Alt+T) in the live session"
echo "    3. Find the ITAD partition name:"
echo "         lsblk -o NAME,LABEL,SIZE"
echo "    4. Mount it and run the setup script:"
echo "         sudo mkdir -p /mnt/itad"
echo "         sudo mount /dev/sdX3 /mnt/itad   <-- (Replace sdX3 with your actual ITAD partition)"
echo "         sudo bash /mnt/itad/first_boot.sh"
echo
echo "  Persistence means apt installs survive reboots."
echo "  first_boot.sh is idempotent — safe to re-run."
echo "================================================================"