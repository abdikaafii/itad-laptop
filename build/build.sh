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
sudo
util-linux
x11-xserver-utils
unclutter
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

echo "== 4b. Setup auto-start for the ITAD tool (Openbox) =="
AUTOSTART_DIR="$INC/etc/xdg/openbox"
mkdir -p "$AUTOSTART_DIR"

cat > "$AUTOSTART_DIR/autostart" <<'EOF'
#!/bin/bash
# Start the ITAD Laptop Tool automatically when the GUI loads.
/opt/itad-laptop/launch.sh &
EOF
chmod +x "$AUTOSTART_DIR/autostart"

echo "== 4c. Auto-start Xorg on tty1 login =="
# Using /etc/skel ensures the live user gets these files automatically on boot
SKEL_DIR="$INC/etc/skel"
mkdir -p "$SKEL_DIR"

# Tell startx to launch openbox-session
cat > "$SKEL_DIR/.xinitrc" <<'EOF'
#!/bin/sh
exec openbox-session
EOF
chmod +x "$SKEL_DIR/.xinitrc"

# Auto-run startx on tty1 login via bash_profile
cat > "$SKEL_DIR/.bash_profile" <<'EOF'
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    exec startx
fi
EOF

echo "== 4d. Custom Openbox Menu =="
OPENBOX_MENU="$INC/etc/xdg/openbox/menu.xml"
mkdir -p "$(dirname "$OPENBOX_MENU")"
cat > "$OPENBOX_MENU" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_menu xmlns="http://openbox.org/3.4/menu">
<menu id="root-menu" label="Openbox 3">
  <item label="Launch ITAD Tool">
    <action name="Execute">
      <command>/opt/itad-laptop/launch.sh</command>
    </action>
  </item>
  <separator />
  <item label="Terminal">
    <action name="Execute">
      <command>xterm</command>
    </action>
  </item>
  <item label="Exit">
    <action name="Exit" />
  </item>
</menu>
</openbox_menu>
EOF

echo "== 4e. Fix permissions for the ITAD tool =="
# Ensure the scripts uploaded to GitHub are executable inside the ISO
chmod +x "$INC/opt/itad-laptop/launch.sh" 2>/dev/null || true
chmod +x "$INC/opt/itad-laptop/"*.py 2>/dev/null || true

echo "== 5. Build =="
lb build

echo "== 6. Collect output =="
ISO_IN="$(ls -1t live-image-*.hybrid.iso binary.hybrid.iso 2>/dev/null | head -n1 || true)"
if [ -z "${ISO_IN:-}" ]; then
  echo "ERROR: lb build finished but no .hybrid.iso was produced" >&2
  exit 1
fi
mv -f "$ISO_IN" "itad-laptop.iso"
ls -lh itad-laptop.iso