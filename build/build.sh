#!/bin/bash
# build.sh - builds the ITAD laptop boot ISO (itad-laptop)
# Runs on GitHub (Build ITAD Laptop ISO). Output: build/itad-laptop.iso
#
# Changes in this version:
#  - Hooks are now named *.hook.chroot / *.hook.binary. live-build ignores
#    any other name, so the old boot-splash hook never ran.
#  - No more GRUB "png: color type not supported" error: the boot menu
#    picture is removed and the menu is hidden (UEFI and BIOS).
#  - No more "login: user (automatic login)" text before the window.
#  - The extra package lists in build/package-lists are now really used
#    (stress-ng, memtester, ffmpeg, Wi-Fi firmware and more).

set -euo pipefail
cd "$(dirname "$0")"

CFG="config"
INC="$CFG/includes.chroot"
HOOKS="$CFG/hooks/normal"

echo "== 1. Clean old build =="
lb clean --purge 2>/dev/null || true
# remove the old custom BIOS menu folder (it replaced the whole default one)
rm -rf "$CFG/bootloaders/isolinux"

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
  --bootappend-live "boot=live components quiet splash loglevel=0 rd.systemd.show_status=false systemd.show_status=false rd.udev.log_level=3 udev.log_level=3 vt.global_cursor_default=0 plymouth.ignore-serial-consoles"

echo "== 2b. Package lists =="
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
python3-pil
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
# The lists in build/package-lists were never used (live-build only reads
# build/config/package-lists). Copy them in so their packages are installed.
for f in package-lists/*.list.chroot; do
  if [ -f "$f" ]; then
    cp -f "$f" "$CFG/package-lists/repo-$(basename "$f")"
    echo "added package list: $f"
  fi
done

echo "== 3. Boot splash theme =="
THEME="$INC/usr/share/plymouth/themes/itad"
mkdir -p "$THEME"

convert -size 760x240 xc:none \
  -font /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf \
  -fill white -gravity center -pointsize 90 -annotate 0 "ITAD" \
  -font /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
  -fill "#38bdf8" -pointsize 30 -annotate +0+80 "Laptop Test and Wipe" \
  "PNG32:$THEME/logo.png"

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
Window.SetBackgroundTopColor(0, 0, 0);
Window.SetBackgroundBottomColor(0, 0, 0);
logo = Image("logo.png");
screen_w = Window.GetWidth();
screen_h = Window.GetHeight();
sprite = Sprite(logo);
sprite.SetX(screen_w / 2 - logo.GetWidth() / 2);
sprite.SetY(screen_h / 2 - logo.GetHeight() / 2);
EOF

echo "== 4. Hook: turn on the ITAD boot splash (runs inside the new system) =="
mkdir -p "$HOOKS"
# old hook names that live-build never ran
rm -f "$HOOKS/0100-set-plymouth-theme.chroot" "$HOOKS/0200-update-grub.chroot"
cat > "$HOOKS/0100-itad-plymouth.hook.chroot" <<'EOF'
#!/bin/sh
echo "ITAD hook: setting boot splash theme"
if command -v plymouth-set-default-theme >/dev/null 2>&1; then
  plymouth-set-default-theme itad || true
fi
update-alternatives --install \
  /usr/share/plymouth/themes/default.plymouth default.plymouth \
  /usr/share/plymouth/themes/itad/itad.plymouth 100 2>/dev/null || true
update-alternatives --set default.plymouth \
  /usr/share/plymouth/themes/itad/itad.plymouth 2>/dev/null || true
# rebuild the start-up image so the splash is inside it
if command -v update-initramfs >/dev/null 2>&1; then
  update-initramfs -u -k all || true
fi
exit 0
EOF
chmod +x "$HOOKS/0100-itad-plymouth.hook.chroot"

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
# Black background while the window opens
xsetroot -solid black > /dev/null 2>&1
# Allow root (sudo) to draw windows on the screen
xhost +local:root > /dev/null 2>&1
# Start LaptopSeal through launch.sh (opens laptop_window.py,
# and shows the reason on screen if the window cannot open)
/opt/itad-laptop/launch.sh > /tmp/launch-out.log 2>&1 &
EOF
chmod +x "$AUTOSTART_DIR/autostart"

echo "== 4c. Hide Debian text and auto-start the screen =="
# Clear the Debian welcome messages
: > "$INC/etc/issue"
: > "$INC/etc/issue.net"
: > "$INC/etc/motd"

# Silent automatic login on the first screen (tty1).
# Without this you see "debian login: user (automatic login)".
# The name starts with "zz-" so it wins over live-config's own setting.
GETTY_DIR="$INC/etc/systemd/system/getty@tty1.service.d"
mkdir -p "$GETTY_DIR"
cat > "$GETTY_DIR/zz-itad-autologin.conf" <<'EOF'
[Service]
Type=idle
ExecStart=
ExecStart=-/sbin/agetty --autologin user --skip-login --nonewline --noissue --nohostname --noclear %I $TERM
EOF

SKEL_DIR="$INC/etc/skel"
mkdir -p "$SKEL_DIR"

# No "Last login" or welcome text after the automatic login
: > "$SKEL_DIR/.hushlogin"

cat > "$SKEL_DIR/.xinitrc" <<'EOF'
#!/bin/sh
exec openbox-session
EOF
chmod +x "$SKEL_DIR/.xinitrc"

# Start the screen straight away. All X messages go to a log file,
# not onto the screen.
START_X='if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    clear
    startx -- -keeptty > /tmp/startx.log 2>&1
    clear
fi'
printf '%s\n' "$START_X" > "$SKEL_DIR/.bash_profile"
printf '%s\n' "$START_X" > "$SKEL_DIR/.profile"

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

echo "== 4f. Hide the boot menus (GRUB for UEFI, isolinux for BIOS) =="
# Plain black pictures in full colour (PNG24). GRUB cannot show the old
# 16-colour pictures, which gave "png: color type not supported".
BIN_INC="$CFG/includes.binary/isolinux"
mkdir -p "$BIN_INC"
convert -size 800x600 xc:black "PNG24:$BIN_INC/splash800x600.png"
convert -size 640x480 xc:black "PNG24:$BIN_INC/splash.png"

# This hook runs on the finished USB files, just before the ISO is made.
cat > "$HOOKS/0900-itad-hide-boot-menu.hook.binary" <<'EOF'
#!/bin/sh
echo "ITAD hook: hiding boot menus (folder: $(pwd))"
B="."
if [ ! -d boot/grub ] && [ -d binary/boot/grub ]; then
  B="binary"
fi

# --- GRUB (UEFI) ---
G="$B/boot/grub"
if [ -d "$G" ]; then
  # No background picture. The old picture caused
  # "png: color type not supported / Press any key to continue".
  cat > "$G/theme.cfg" <<'X'
set color_normal=black/black
set color_highlight=black/black
set menu_color_normal=black/black
set menu_color_highlight=black/black
X
  # Start the first entry (the live system) at once, menu hidden
  for f in "$G"/*.cfg; do
    [ -f "$f" ] && sed -i 's/^\([[:space:]]*\)set timeout=.*/\1set timeout=0/' "$f"
  done
  if [ -f "$G/config.cfg" ]; then
    printf '\nset default=0\nset timeout=0\nset timeout_style=hidden\n' >> "$G/config.cfg"
  fi
  if [ -f "$G/grub.cfg" ]; then
    { printf 'set default=0\nset timeout=0\nset timeout_style=hidden\n'
      cat "$G/grub.cfg"; } > "$G/grub.cfg.new"
    mv -f "$G/grub.cfg.new" "$G/grub.cfg"
  fi
  echo "ITAD hook: GRUB menu hidden"
fi

# --- isolinux (older BIOS laptops) ---
I="$B/isolinux"
if [ -f "$I/live.cfg" ]; then
  LABEL="$(awk 'tolower($1)=="label" {print $2; exit}' "$I/live.cfg")"
  if [ -n "$LABEL" ]; then
    cat > "$I/isolinux.cfg" <<X
include live.cfg
default $LABEL
prompt 0
timeout 1
X
    echo "ITAD hook: isolinux boots '$LABEL' straight away"
  fi
fi
exit 0
EOF
chmod +x "$HOOKS/0900-itad-hide-boot-menu.hook.binary"

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
