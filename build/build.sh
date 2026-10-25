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

cat > "$THEME/itad.plymouth" <<'EOF'
[Plymouth Theme]
Name=ITAD
Description=ITAD boot splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/itad
ScriptFile=/usr/share/plymouth/themes/itad/itad.script
EOF

cat > "$THEME/itad.script" <<'EOF'
Window.SetBackgroundTopColor(0.02, 0.04, 0.08);
Window.SetBackgroundBottomColor(0.02, 0.04, 0.08);
logo.image = Image("logo.png");
logo.sprite = Sprite(logo.image);
logo.sprite.SetX(Window.GetWidth() / 2 - logo.image.GetWidth() / 2);
logo.sprite.SetY(Window.GetHeight() / 2 - logo.image.GetHeight() / 2);
EOF

echo "== 4. Auto-login and auto-start =="
mkdir -p "$INC/etc/systemd/system/getty@tty1.service.d"
cat > "$INC/etc/systemd/system/getty@tty1.service.d/zz-itad.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I $TERM
EOF

mkdir -p "$INC/root"
cat > "$INC/root/.bash_profile" <<'EOF'
# Start the ITAD tool automatically on the first screen
if [ -z "${DISPLAY:-}" ] && [ "$(tty)" = "/dev/tty1" ]; then
    exec xinit /opt/xinitrc -- :0 vt1 -nolisten tcp >/var/log/xinit.log 2>&1
fi
EOF

mkdir -p "$INC/opt"
cat > "$INC/opt/xinitrc" <<'EOF'
#!/bin/sh
xsetroot -solid "#050a14"
openbox &
sleep 1
exec bash /opt/launch.sh
EOF
chmod +x "$INC/opt/xinitrc"

echo "== 5. Build hooks =="
mkdir -p "$HOOKS"

cat > "$HOOKS/0100-plymouth.hook.chroot" <<'EOF'
#!/bin/sh
set -e
plymouth-set-default-theme itad
update-initramfs -u
EOF

cat > "$HOOKS/0200-hide-menu.hook.binary" <<'EOF'
#!/bin/sh
set -e
# BIOS boot menu (syslinux): hide it and boot at once
if [ -d binary/isolinux ]; then
    for f in binary/isolinux/*.cfg; do
        sed -i 's/^[Tt][Ii][Mm][Ee][Oo][Uu][Tt] .*/TIMEOUT 1/' "$f"
        sed -i 's/^[Pp][Rr][Oo][Mm][Pp][Tt] .*/PROMPT 0/' "$f"
    done
    printf '\nmenu hidden\nmenu autoboot\n' >> binary/isolinux/menu.cfg
fi
# UEFI boot menu (grub): hide it and boot at once
if [ -f binary/boot/grub/grub.cfg ]; then
    sed -i '1i set timeout=0\nset timeout_style=hidden' binary/boot/grub/grub.cfg
fi
EOF

chmod +x "$HOOKS"/*

echo "== 6. Build the ISO (this takes a while) =="
lb build

ISO="$(ls -1 ./*.iso | head -n1)"
mv "$ISO" itad-laptop.iso
echo "Done: build/itad-laptop.iso"
