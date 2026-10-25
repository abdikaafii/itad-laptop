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