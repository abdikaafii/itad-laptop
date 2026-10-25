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

echo "== 2b. Package list =="
mkdir -p "$CFG/package-lists"
cat > "$CFG/package-lists/itad.list.chroot" <<'EOF'
plymouth
plymouth-themes
xorg
xinit
openbox
xterm
python3
python3-tk
python3-reportlab
sudo
util-linux
x11-xserver-utils
unclutter
ntfs-3g
alsa-utils
network-manager
smartmontools
dmidecode
hdparm
nvme-cli
pciutils
usbutils
lshw
v4l-utils
iw
rfkill
EOF

echo "== 3. Boot splash theme =="
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

echo "== 4. Hook: enable the ITAD plymouth theme =="
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

echo "== 4a. Passwordless sudo for live user =="
SUDOERS_DIR="$INC/etc/sudoers.d"
mkdir -p "$SUDOERS_DIR"
echo "user ALL=(ALL) NOPASSWD: ALL" > "$SUDOERS_DIR/live-nopasswd"
chmod 0440 "$SUDOERS_DIR/live-nopasswd"

echo "== 4a2. Put the tool files in /opt/itad-laptop =="
# The files may sit directly in includes.chroot/opt (GitHub folder "opt").
# The USB looks for them in /opt/itad-laptop, so copy them there.
mkdir -p "$INC/opt/itad-laptop"
for f in "$INC"/opt/*.py "$INC"/opt/*.sh "$INC"/opt/*.md \
         "$INC"/opt/*.png "$INC"/opt/*.gif "$INC"/opt/*.svg \
         "$INC"/opt/*.txt; do
  if [ -f "$f" ]; then
    cp -f "$f" "$INC/opt/itad-laptop/"
  fi
done
echo "Files that will be on the USB in /opt/itad-laptop:"
ls -1 "$INC/opt/itad-laptop"
for need in laptop_window.py laptop_check.py laptop_wipe.py laptop_test.py itad_run.py launch.sh; do
  if [ ! -f "$INC/opt/itad-laptop/$need" ]; then
    echo "ERROR: $need is missing from build/config/includes.chroot/opt" >&2
    exit 1
  fi
done

echo "== 4b. Setup auto-start for the ITAD GUI =="
AUTOSTART_DIR="$INC/etc/xdg/openbox"
mkdir -p "$AUTOSTART_DIR"

cat > "$AUTOSTART_DIR/autostart" <<'EOF'
#!/bin/bash
# Allow root (sudo) to draw windows on the screen
xhost +local:root > /dev/null 2>&1
# Start LaptopSeal through launch.sh (opens laptop_window.py,
# and shows the reason on screen if the window cannot open)
/opt/itad-laptop/launch.sh > /tmp/launch-out.log 2>&1 &
EOF
chmod +x "$AUTOSTART_DIR/autostart"

echo "== 4c. Hide Debian text and Auto-start Xorg =="
# Clear the Debian welcome messages
: > "$INC/etc/issue"
: > "$INC/etc/issue.net"
: > "$INC/etc/motd"

SKEL_DIR="$INC/etc/skel"
mkdir -p "$SKEL_DIR"

cat > "$SKEL_DIR/.xinitrc" <<'EOF'
#!/bin/sh
exec openbox-session
EOF
chmod +x "$SKEL_DIR/.xinitrc"

# Create BOTH .profile and .bash_profile to catch any shell
cat > "$SKEL_DIR/.bash_profile" <<'EOF'
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    clear
    startx
fi
EOF

cat > "$SKEL_DIR/.profile" <<'EOF'
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    clear
    startx
fi
EOF

echo "== 4d. Custom Openbox Menu =="
OPENBOX_MENU="$INC/etc/xdg/openbox/menu.xml"
mkdir -p "$(dirname "$OPENBOX_MENU")"
cat > "$OPENBOX_MENU" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_menu xmlns="http://openbox.org/3.4/menu">
<menu id="root-menu" label="Openbox 3">
  <item label="Launch LaptopSeal (window)">
    <action name="Execute">
      <command>/opt/itad-laptop/launch.sh</command>
    </action>
  </item>
  <item label="Launch old text pipeline (Check - Wipe - Test)">
    <action name="Execute">
      <command>xterm -fullscreen -e sudo python3 /opt/itad-laptop/itad_run.py</command>
    </action>
  </item>
  <separator />
  <item label="Component tests only (text)">
    <action name="Execute">
      <command>xterm -e sudo python3 /opt/itad-laptop/laptop_test.py</command>
    </action>
  </item>
  <item label="Test Keyboard">
    <action name="Execute">
      <command>python3 /opt/itad-laptop/keyboard_test.py</command>
    </action>
  </item>
  <item label="Test Screen (Colors)">
    <action name="Execute">
      <command>python3 /opt/itad-laptop/screen_test.py</command>
    </action>
  </item>
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
chmod +x "$INC/opt/itad-laptop/"*.sh 2>/dev/null || true
chmod +x "$INC/opt/itad-laptop/"*.py 2>/dev/null || true

echo "== 4f. Hide Boot Menus (Syslinux & GRUB) =="
# 1. Syslinux (BIOS/Legacy)
SYS_CFG_DIR="$CFG/bootloaders/isolinux"
mkdir -p "$SYS_CFG_DIR"

# Generate an 8-bit indexed color PNG with 16 colors for Syslinux compatibility
convert -size 640x480 xc:black -type Palette -colors 16 -depth 8 "$SYS_CFG_DIR/splash.png"
convert -size 800x600 xc:black -type Palette -colors 16 -depth 8 "$SYS_CFG_DIR/splash800x600.png"

# Bypass the graphical menu entirely and boot the default live entry instantly
cat > "$SYS_CFG_DIR/isolinux.cfg" <<'EOF'
include live.cfg
default live
prompt 0
timeout 1
EOF

# 2. GRUB (UEFI)
GRUB_DIR="$INC/etc/default"
mkdir -p "$GRUB_DIR"
cat > "$GRUB_DIR/grub" <<'EOF'
GRUB_DEFAULT=0
GRUB_TIMEOUT=0
GRUB_TIMEOUT_STYLE=hidden
GRUB_HIDDEN_TIMEOUT=0
GRUB_HIDDEN_TIMEOUT_QUIET=true
GRUB_DISABLE_RECOVERY=true
EOF

cat > "$HOOKS/0200-update-grub.chroot" <<'EOF'
#!/bin/sh
set -e
if command -v update-grub >/dev/null 2>&1; then
    update-grub
fi
EOF
chmod +x "$HOOKS/0200-update-grub.chroot"

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
