#!/bin/bash
# build.sh - builds the ITAD laptop boot ISO (itad-laptop)
# Runs on GitHub (Build ITAD Laptop ISO). Output: build/itad-laptop.iso

set -euo pipefail
cd "$(dirname "$0")"

CFG="config"
INC="$CFG/includes.chroot"
HOOKS="$CFG/hooks/normal"

echo "== 1. Clean old build =="
lb clean --purge 2>/dev/null || true

echo "== 2. Configure =="
lb config \
  --distribution bookworm \
  --architectures amd64 \
  --archive-areas "main contrib non-free non-free-firmware" \
  --binary-images iso-hybrid \
  --bootloaders "syslinux,grub-efi" \
  --debian-installer none \
  --memtest none \
  --iso-application "ITAD Laptop Tool" \
  --iso-volume "ITAD_LAPTOP" \
  --bootappend-live "boot=live components quiet splash loglevel=0 systemd.show_status=false vt.global_cursor_default=0 plymouth.ignore-serial-consoles"

echo "== 2b. Package list (programs installed into the ISO) =="
mkdir -p "$CFG/package-lists"
cat > "$CFG/package-lists/itad.list.chroot" <<'EOF'
plymouth
plymouth-themes
xorg
xinit
openbox
xterm
python3
EOF

echo "== 3. Boot splash theme (logo) =="
THEME="$INC/usr/share/plymouth/themes/itad"
mkdir -p "$THEME"

convert -size 760x240 xc:none \
  -font /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf \
  -fill white -gravity center -pointsize 90 -annotate 0 "ITAD" \
  -font /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
  -fill "#38bdf8" -pointsize 30 -annotate +0+80 "Laptop Test and Wipe" \
  "$THEME/logo.png"

# --- FIXED: complete plymouth theme descriptor ---
cat > "$THEME/itad.plymouth" <<'EOF'
[Plymouth Theme]
Name=ITAD
Description=ITAD Laptop boot splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/itad
ScriptFile=/usr/share/plymouth/themes/itad/itad.script
EOF

cat > "$THEME/itad.script" <<'EOF'
# Minimal plymouth script theme: logo centred on the background
logo = Image("logo.png");

screen_w = Window.GetWidth();
screen_h = Window.GetHeight();

sprite = Sprite(logo);
sprite.SetX(screen_w / 2 - logo.GetWidth() / 2);
sprite.SetY(screen_h / 2 - logo.GetHeight() / 2);
EOF

echo "== 4. Hook: enable the ITAD plymouth theme inside the chroot =="
mkdir -p "$HOOKS"
cat > "$HOOKS/0100-set-plymouth-theme.chroot" <<'EOF'
#!/bin/sh
set -e
update-alternatives --install \
  /usr/share/plymouth/themes/default.plymouth \
  default.plymouth \
  /usr/share/plymouth/themes/itad/itad.plymouth \
  100
update-alternatives --set default.plymouth \
  /usr/share/plymouth/themes/itad/itad.plymouth
EOF
chmod +x "$HOOKS/0100-set-plymouth-theme.chroot"

echo "== 5. Build =="
lb build

echo
echo "== 6. Collect output =="
# live-build produces live-image-amd64.hybrid.iso (or binary.hybrid.iso on older versions)
ISO_IN="$(ls -1t live-image-*.hybrid.iso binary.hybrid.iso 2>/dev/null | head -n1 || true)"
if [ -z "${ISO_IN:-}" ]; then
  echo "ERROR: lb build finished but no .hybrid.iso was produced" >&2
  exit 1
fi
mv -f "$ISO_IN" "itad-laptop.iso"
ls -lh itad-laptop.iso