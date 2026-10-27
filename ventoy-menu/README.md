# ITAD Ventoy menu

The boot menu shown by a Ventoy stick that holds the three ITAD tools.

## Put it on the Ventoy stick (Windows laptop)

1. Copy the folder **`ventoy`** (from this folder) to the **top of the Ventoy
   drive** in File Explorer. The drive then has:
   ```
   ventoy\            <- this folder (ventoy.json + theme)
   ITAD-LaptopSeal.iso
   ITAD-PhoneSeal.iso
   ITAD-SwitchSeal.iso
   ```
2. Eject the stick safely.

The ISO names must be exactly as above (the menu uses them for the
names, icons and tips).

## Change it

`make_theme.py` (v2, fits small screens too) makes everything again (background, icons, fonts,
`theme.txt`, `ventoy.json`) and `preview.png`.
Auto-start a tool after some seconds: add to `"control"` in ventoy.json
`{"VTOY_MENU_TIMEOUT": "10"}` and `{"VTOY_DEFAULT_IMAGE": "/ITAD-LaptopSeal.iso"}`.

Menu looks wrong on a computer? Delete the `ventoy` folder from the stick:
Ventoy goes back to its normal menu.
