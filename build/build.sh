#!/bin/bash
# build.sh - builds the ITAD laptop boot ISO. Output: build/itad-laptop.iso
set -euo pipefail
cd "$(dirname "$0")"
CFG="config"; INC="$CFG/includes.chroot"; HOOKS="$CFG/hooks/normal"

echo "== 1. Clean =="
lb clean --purge 2>/dev/null || true
rm -rf "$CFG/bootloaders/isolinux"

echo "== 2. Configure =="
lb config --distribution bookworm --architectures amd64 \
  --archive-areas "main contrib non-free non-free-firmware" \
  --binary-images iso-hybrid --bootloaders "syslinux,grub-efi" \
  --debian-installer none --memtest none \
  --iso-application "ITAD Laptop Tool" --iso-volume "ITAD_LAPTOP" \
  --bootappend-live "boot=live components quiet splash loglevel=0 rd.systemd.show_status=false systemd.show_status=false rd.udev.log_level=3 udev.log_level=3 vt.global_cursor_default=0 plymouth.ignore-serial-consoles"

echo "== 2b. Packages =="
mkdir -p "$CFG/package-lists"
printf '%s\n' plymouth plymouth-themes xorg xinit openbox xterm python3 \
  python3-tk python3-reportlab python3-pil sudo util-linux x11-xserver-utils \
  unclutter ntfs-3g alsa-utils network-manager smartmontools dmidecode hdparm \
  nvme-cli pciutils usbutils lshw v4l-utils iw rfkill \
  > "$CFG/package-lists/itad.list.chroot"
for f in package-lists/*.list.chroot; do
  [ -f "$f" ] && cp -f "$f" "$CFG/package-lists/repo-$(basename "$f")"
done

echo "== 3. Boot splash =="
T="$INC/usr/share/plymouth/themes/itad"; mkdir -p "$T"
convert -size 760x240 xc:none \
  -font /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf \
  -fill white -gravity center -pointsize 90 -annotate 0 "ITAD" \
  -font /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
  -fill "#38bdf8" -pointsize 30 -annotate +0+80 "Laptop Test and Wipe" \
  "PNG32:$T/logo.png"
printf '%s\n' "[Plymouth Theme]" "Name=ITAD" "ModuleName=script" "" "[script]" \
  "ImageDir=/usr/share/plymouth/themes/itad" \
  "ScriptFile=/usr/share/plymouth/themes/itad/itad.script" > "$T/itad.plymouth"
cat > "$T/itad.script" <<'EOF'
Window.SetBackgroundTopColor(0, 0, 0);
Window.SetBackgroundBottomColor(0, 0, 0);
logo = Image("logo.png");
s = Sprite(logo);
s.SetX(Window.GetWidth() / 2 - logo.GetWidth() / 2);
s.SetY(Window.GetHeight() / 2 - logo.GetHeight() / 2);
EOF

echo "== 4. Splash hook =="
mkdir -p "$HOOKS"
cat > "$HOOKS/0100-itad-plymouth.hook.chroot" <<'EOF'
#!/bin/sh
plymouth-set-default-theme itad 2>/dev/null || true
P=/usr/share/plymouth/themes
update-alternatives --install $P/default.plymouth default.plymouth $P/itad/itad.plymouth 100 2>/dev/null || true
update-alternatives --set default.plymouth $P/itad/itad.plymouth 2>/dev/null || true
update-initramfs -u -k all 2>/dev/null || true
exit 0
EOF

echo "== 4a. sudo + tool files =="
mkdir -p "$INC/etc/sudoers.d"
echo "user ALL=(ALL) NOPASSWD: ALL" > "$INC/etc/sudoers.d/live-nopasswd"
chmod 0440 "$INC/etc/sudoers.d/live-nopasswd"
A="$INC/opt/itad-laptop"; mkdir -p "$A"
for f in "$INC"/opt/*.py "$INC"/opt/*.sh; do [ -f "$f" ] && cp -f "$f" "$A/"; done
ls -1 "$A"
for n in laptop_window.py laptop_check.py laptop_wipe.py laptop_test.py itad_run.py launch.sh; do
  [ -f "$A/$n" ] || { echo "ERROR: $n missing" >&2; exit 1; }
done
chmod +x "$A"/*.sh "$A"/*.py

echo "== 4b. Autostart, autologin, menu =="
OB="$INC/etc/xdg/openbox"; mkdir -p "$OB"
cat > "$OB/autostart" <<'EOF'
#!/bin/bash
xsetroot -solid black >/dev/null 2>&1
xhost +local:root >/dev/null 2>&1
/opt/itad-laptop/launch.sh >/tmp/launch-out.log 2>&1 &
EOF
: > "$INC/etc/issue"; : > "$INC/etc/issue.net"; : > "$INC/etc/motd"
G="$INC/etc/systemd/system/getty@tty1.service.d"; mkdir -p "$G"
printf '%s\n' "[Service]" "Type=idle" "ExecStart=" \
  'ExecStart=-/sbin/agetty --autologin user --skip-login --nonewline --noissue --nohostname --noclear %I $TERM' \
  > "$G/zz-itad-autologin.conf"
K="$INC/etc/skel"; mkdir -p "$K"; : > "$K/.hushlogin"
printf '#!/bin/sh\nexec openbox-session\n' > "$K/.xinitrc"
X='if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then clear; startx -- -keeptty >/tmp/startx.log 2>&1; clear; fi'
echo "$X" > "$K/.bash_profile"; echo "$X" > "$K/.profile"
cat > "$OB/menu.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_menu xmlns="http://openbox.org/3.4/menu">
<menu id="root-menu" label="Openbox 3">
<item label="Launch LaptopSeal (window)"><action name="Execute"><command>/opt/itad-laptop/launch.sh</command></action></item>
<item label="Old text pipeline"><action name="Execute"><command>xterm -fullscreen -e sudo python3 /opt/itad-laptop/itad_run.py</command></action></item>
<item label="Component tests (text)"><action name="Execute"><command>xterm -e sudo python3 /opt/itad-laptop/laptop_test.py</command></action></item>
<item label="Terminal"><action name="Execute"><command>xterm</command></action></item>
<item label="Exit"><action name="Exit" /></item>
</menu>
</openbox_menu>
EOF
chmod +x "$OB/autostart" "$K/.xinitrc" "$HOOKS"/*.hook.chroot

echo "== 4c. Hide boot menus =="
B="$CFG/includes.binary/isolinux"; mkdir -p "$B"
convert -size 800x600 xc:black "PNG24:$B/splash800x600.png"
convert -size 640x480 xc:black "PNG24:$B/splash.png"
cat > "$HOOKS/0900-itad-hide-boot-menu.hook.binary" <<'EOF'
#!/bin/sh
B="."; [ ! -d boot/grub ] && [ -d binary/boot/grub ] && B="binary"
G="$B/boot/grub"
if [ -d "$G" ]; then
  printf 'set color_normal=black/black\nset color_highlight=black/black\nset menu_color_normal=black/black\nset menu_color_highlight=black/black\n' > "$G/theme.cfg"
  for f in "$G"/*.cfg; do sed -i 's/^\([[:space:]]*\)set timeout=.*/\1set timeout=0/' "$f"; done
  [ -f "$G/config.cfg" ] && printf '\nset default=0\nset timeout=0\nset timeout_style=hidden\n' >> "$G/config.cfg"
  if [ -f "$G/grub.cfg" ]; then
    { printf 'set default=0\nset timeout=0\nset timeout_style=hidden\n'; cat "$G/grub.cfg"; } > "$G/g.new"
    mv -f "$G/g.new" "$G/grub.cfg"
  fi
fi
I="$B/isolinux"
if [ -f "$I/live.cfg" ]; then
  L="$(awk 'tolower($1)=="label" {print $2; exit}' "$I/live.cfg")"
  [ -n "$L" ] && printf 'include live.cfg\ndefault %s\nprompt 0\ntimeout 1\n' "$L" > "$I/isolinux.cfg"
fi
exit 0
EOF
chmod +x "$HOOKS/0900-itad-hide-boot-menu.hook.binary"

echo "== 5. Build =="
lb build
ISO="$(ls -1t live-image-*.hybrid.iso binary.hybrid.iso 2>/dev/null | head -n1 || true)"
[ -n "$ISO" ] || { echo "ERROR: no ISO produced" >&2; exit 1; }
mv -f "$ISO" itad-laptop.iso
ls -lh itad-laptop.iso
