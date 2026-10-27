"""Builds the ITAD Ventoy theme: ventoy/ventoy.json + ventoy/theme/itad/*
(background, icons, highlight bar, GRUB .pf2 fonts, theme.txt) and a
preview picture of the menu drawn with the same fonts."""
import json
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

OUT = sys.argv[1]
TD = os.path.join(OUT, "ventoy", "theme", "itad")
os.makedirs(os.path.join(TD, "icons"), exist_ok=True)
FD = "/usr/share/fonts/truetype/dejavu/"
W, H = 1920, 1080
TILE = {"r": "#E8453C", "o": "#F59E0B", "y": "#FACC15", "g": "#22C55E",
        "t": "#14B8A6", "c": "#38BDF8", "b": "#1E7BFF"}
TICK = ["...b", "..ct", "ygt.", ".o.."]

# menu box position (same numbers go into theme.txt)
MX, MY, MW, MH = 0.22, 0.31, 0.56, 0.30
TIP_Y = 0.655
ITEM_H, ITEM_SP, ICON = 92, 18, 64


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------- fonts
CHARS = list(range(32, 127)) + [0xB7, 0x2022, 0x2013, 0x2191, 0x2193,
                                0xE9, 0xA9]


def make_pf2(ttf, size, name, family, weight, path):
    f = ImageFont.truetype(ttf, size)
    asc, desc = f.getmetrics()
    glyphs = []
    for cp in CHARS:
        ch = chr(cp)
        adv = int(round(f.getlength(ch)))
        bb = f.getbbox(ch, anchor="ls")
        x0, y0, x1, y1 = bb
        w, h = max(0, x1 - x0), max(0, y1 - y0)
        bits = b""
        if w and h:
            im = Image.new("L", (w, h), 0)
            ImageDraw.Draw(im).text((-x0, -y0), ch, font=f, fill=255,
                                    anchor="ls")
            px = im.load()
            acc, n, out = 0, 0, bytearray()
            for yy in range(h):
                for xx in range(w):
                    acc = (acc << 1) | (1 if px[xx, yy] >= 110 else 0)
                    n += 1
                    if n == 8:
                        out.append(acc)
                        acc, n = 0, 0
            if n:
                out.append(acc << (8 - n))
            bits = bytes(out)
        glyphs.append((cp, w, h, x0, -y1, adv, bits))

    def sec(tag, data):
        return tag + struct.pack(">I", len(data)) + data

    head = b"".join([
        sec(b"FILE", b"PFF2"),
        sec(b"NAME", name.encode() + b"\0"),
        sec(b"FAMI", family.encode() + b"\0"),
        sec(b"WEIG", weight.encode() + b"\0"),
        sec(b"SLAN", b"normal\0"),
        sec(b"PTSZ", struct.pack(">H", size)),
        sec(b"MAXW", struct.pack(">H", max(g[1] for g in glyphs))),
        sec(b"MAXH", struct.pack(">H", max(g[2] for g in glyphs))),
        sec(b"ASCE", struct.pack(">H", asc)),
        sec(b"DESC", struct.pack(">H", desc)),
    ])
    chix_len = 9 * len(glyphs)
    data_start = len(head) + 8 + chix_len + 8
    chix, data, off = b"", b"", data_start
    for cp, w, h, xo, yo, adv, bits in sorted(glyphs):
        g = struct.pack(">HHhhh", w, h, xo, yo, adv) + bits
        chix += struct.pack(">IBI", cp, 0, off)
        data += g
        off += len(g)
    blob = head + b"CHIX" + struct.pack(">I", chix_len) + chix + \
        b"DATA" + b"\xff\xff\xff\xff" + data
    with open(path, "wb") as fh:
        fh.write(blob)
    return name


def read_pf2(path):
    """Reads a .pf2 back (checks the file and draws the preview)."""
    b = open(path, "rb").read()
    pos, info, glyphs = 0, {}, {}
    while pos < len(b):
        tag = b[pos:pos + 4]
        ln = struct.unpack(">I", b[pos + 4:pos + 8])[0]
        pos += 8
        if tag == b"DATA":
            break
        val = b[pos:pos + ln]
        if tag == b"CHIX":
            for i in range(0, ln, 9):
                cp, fl, off = struct.unpack(">IBI", val[i:i + 9])
                w, h, xo, yo, adv = struct.unpack(">HHhhh", b[off:off + 10])
                nb = (w * h + 7) // 8
                glyphs[cp] = (w, h, xo, yo, adv, b[off + 10:off + 10 + nb])
        elif tag in (b"PTSZ", b"ASCE", b"DESC", b"MAXW", b"MAXH"):
            info[tag.decode()] = struct.unpack(">H", val)[0]
        else:
            info[tag.decode()] = val.rstrip(b"\0").decode()
        pos += ln
    return info, glyphs


def draw_pf2(img, path, x, y_base, text, colour):
    info, gl = read_pf2(path)
    px = img.load()
    c = rgb(colour)
    for ch in text:
        g = gl.get(ord(ch)) or gl.get(ord("?"))
        w, h, xo, yo, adv, bits = g
        top = y_base - (h + yo)
        for i in range(w * h):
            if bits[i // 8] & (0x80 >> (i % 8)):
                xx, yy = x + xo + i % w, top + i // w
                if 0 <= xx < img.width and 0 <= yy < img.height:
                    px[xx, yy] = c
        x += adv
    return x


fonts = {
    "item": make_pf2(FD + "DejaVuSans-Bold.ttf", 30, "DejaVu Sans Bold 30",
                     "DejaVu Sans", "bold", os.path.join(TD, "dvb30.pf2")),
    "tip": make_pf2(FD + "DejaVuSans.ttf", 20, "DejaVu Sans Regular 20",
                    "DejaVu Sans", "normal", os.path.join(TD, "dv20.pf2")),
    "small": make_pf2(FD + "DejaVuSans.ttf", 16, "DejaVu Sans Regular 16",
                      "DejaVu Sans", "normal", os.path.join(TD, "dv16.pf2")),
}
for fn in ("dvb30.pf2", "dv20.pf2", "dv16.pf2"):
    info, gl = read_pf2(os.path.join(TD, fn))
    assert info["FILE"] == "PFF2" and len(gl) == len(CHARS), fn
print("fonts ok:", fonts)

# --------------------------------------------------------------- background
bg = Image.new("RGB", (W, H))
d = ImageDraw.Draw(bg)
top, bot = rgb("#07334A"), rgb("#04121F")
for yy in range(H):
    t = yy / (H - 1.0)
    d.line([(0, yy), (W, yy)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t)
                                          for i in range(3)))
# banner band
d.rectangle([0, 0, W, 150], fill=rgb("#07334A"))
d.rectangle([0, 150, W, 152], fill=rgb("#0A4560"))
sq, gp, x0, y0 = 22, 5, 70, 30
for r, line in enumerate(TICK):
    for k, ch in enumerate(line):
        if ch in TILE:
            d.rectangle([x0 + k * (sq + gp), y0 + r * (sq + gp),
                         x0 + k * (sq + gp) + sq, y0 + r * (sq + gp) + sq],
                        fill=rgb(TILE[ch]))
fB = ImageFont.truetype(FD + "DejaVuSans-Bold.ttf", 70)
fM = ImageFont.truetype(FD + "DejaVuSans-Bold.ttf", 26)
fR = ImageFont.truetype(FD + "DejaVuSans.ttf", 22)
fS = ImageFont.truetype(FD + "DejaVuSans.ttf", 18)
d.text((200, 108), "IT", font=fB, fill="white", anchor="ls")
d.text((200 + fB.getlength("IT"), 108), "AD", font=fB, fill=rgb("#38BDF8"),
       anchor="ls")
d.text((200 + fB.getlength("ITAD") + 16, 104), "TOOLKIT", font=fM,
       fill="white", anchor="ls")
dx = 200 + fB.getlength("ITAD") + 16 + fM.getlength("TOOLKIT") + 34
d.rectangle([dx, 38, dx + 3, 112], fill=rgb("#D7E7F2"))
for i, line in enumerate(("Certified data erasure", "Device testing",
                          "Signed certificates")):
    d.text((dx + 30, 60 + i * 28), line, font=fR, fill="white", anchor="ls")
d.text((W - 70, 70), "Choose a tool", font=fM, fill="white", anchor="rs")
d.text((W - 70, 102), "Up / Down to move  -  Enter to start", font=fS,
       fill=rgb("#BFD3F2"), anchor="rs")
# menu panel (the boot menu is drawn on top of it by GRUB)
px0, py0 = int(W * MX) - 30, int(H * MY) - 30
px1, py1 = int(W * (MX + MW)) + 30, int(H * TIP_Y) + 50
d.rounded_rectangle([px0, py0, px1, py1], radius=18, fill=rgb("#0B2238"),
                    outline=rgb("#1E4470"), width=2)
# footer
d.rectangle([0, H - 60, W, H], fill=rgb("#081830"))
for k, ch in enumerate("rygcb"):
    d.rectangle([70 + k * 16, H - 36, 70 + k * 16 + 10, H - 26],
                fill=rgb(TILE[ch]))
d.text((W // 2, H - 24), "Better devices. A cleaner planet.", font=fS,
       fill=rgb("#8EA6C8"), anchor="ms")
for k, ch in enumerate("yoct"):
    d.rectangle([W - 4 * 30 + k * 30, 128, W - 4 * 30 + k * 30 + 22, 150],
                fill=rgb(TILE[ch]))
bg.save(os.path.join(TD, "background.png"), optimize=True)

# --------------------------------------------------------------- highlight
Image.new("RGB", (8, ITEM_H), rgb("#1E7BFF")).save(
    os.path.join(TD, "select_c.png"))
Image.new("RGB", (6, ITEM_H), rgb("#38BDF8")).save(
    os.path.join(TD, "select_w.png"))

# --------------------------------------------------------------- icons


def icon(name, colour, draw_fn):
    s = 4 * ICON
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dd = ImageDraw.Draw(im)
    dd.rounded_rectangle([0, 0, s - 1, s - 1], radius=s // 5,
                         fill=rgb(colour) + (255,))
    draw_fn(dd, s)
    im = im.resize((ICON, ICON), Image.LANCZOS)
    im.save(os.path.join(TD, "icons", name + ".png"))


def laptop(dd, s):
    dd.rounded_rectangle([s * .22, s * .25, s * .78, s * .62], radius=s * .03,
                         outline="white", width=int(s * .05))
    dd.rounded_rectangle([s * .12, s * .66, s * .88, s * .74], radius=s * .03,
                         fill="white")


def phone(dd, s):
    dd.rounded_rectangle([s * .34, s * .16, s * .66, s * .84], radius=s * .07,
                         outline="white", width=int(s * .05))
    dd.ellipse([s * .47, s * .72, s * .53, s * .78], fill="white")


def switch(dd, s):
    dd.rounded_rectangle([s * .14, s * .36, s * .86, s * .64], radius=s * .04,
                         outline="white", width=int(s * .05))
    for k in range(5):
        x = s * (.23 + k * .115)
        dd.rectangle([x, s * .45, x + s * .07, s * .55], fill="white")


icon("laptop", "#7C5CFA", laptop)
icon("phone", "#14B8A6", phone)
icon("switch", "#F59E0B", switch)

# --------------------------------------------------------------- theme.txt
theme = '''# ITAD Toolkit - Ventoy boot menu theme
desktop-image: "background.png"
desktop-color: "#04121F"
title-text: ""
message-font: "{small}"
message-color: "#EAF2FF"
terminal-font: "{small}"

+ boot_menu {{
  left = {mx}%
  top = {my}%
  width = {mw}%
  height = {mh}%
  item_font = "{item}"
  item_color = "#C9D8EE"
  selected_item_font = "{item}"
  selected_item_color = "#FFFFFF"
  item_height = {ih}
  item_padding = 14
  item_spacing = {isp}
  icon_width = {ic}
  icon_height = {ic}
  item_icon_space = 26
  selected_item_pixmap_style = "select_*.png"
  scrollbar = false
}}

+ progress_bar {{
  id = "__timeout__"
  text = "@TIMEOUT_NOTIFICATION_SHORT@"
  left = 75%
  width = 20%
  top = 90%
  height = 24
  font = "{small}"
  text_color = "#FBBF24"
  bar_style = "*"
  highlight_style = "*"
}}

+ hbox {{
  left = 3%
  top = 95%-22
  width = 40%
  height = 22
  + label {{ text = "@VTOY_HOTKEY_TIP@" color = "#8EA6C8" font = "{small}" align = "left" }}
}}

+ hbox {{
  left = 3%
  top = 95%-50
  width = 30%
  height = 22
  + label {{ text = "@VTOY_MEM_DISK@" color = "#F87171" font = "{small}" align = "left" }}
}}

+ hbox {{
  left = 3%+300
  top = 95%-50
  width = 30%
  height = 22
  + label {{ text = "@VTOY_GRUB2_MODE@" color = "#F87171" font = "{small}" align = "left" }}
}}

+ hbox {{
  left = 3%+600
  top = 95%-50
  width = 30%
  height = 22
  + label {{ text = "@VTOY_ISO_RAW@" color = "#F87171" font = "{small}" align = "left" }}
}}
'''.format(mx=int(MX * 100), my=int(MY * 100), mw=int(MW * 100),
           mh=int(MH * 100), ih=ITEM_H, isp=ITEM_SP, ic=ICON, **fonts)
open(os.path.join(TD, "theme.txt"), "w").write(theme)

# --------------------------------------------------------------- ventoy.json
ITEMS = [("ITAD-LaptopSeal", "LaptopSeal   -   Laptops and PCs", "laptop",
          "Absolute and enrolment check, secure wipe, component tests, "
          "signed certificate"),
         ("ITAD-PhoneSeal", "PhoneSeal   -   Phones and tablets", "phone",
          "iPhone, iPad, Android: lock checks, tests in the browser, erase, "
          "certificate"),
         ("ITAD-SwitchSeal", "SwitchSeal   -   Cisco switches", "switch",
          "Console cable: hardware and port tests, configuration wipe, "
          "certificate")]
cfg = {
    "control": [
        {"VTOY_DEFAULT_MENU_MODE": "0"},
        {"VTOY_SECONDARY_BOOT_MENU": "0"},
        {"VTOY_FILT_DOT_UNDERSCORE_FILE": "1"},
        {"VTOY_MENU_LANGUAGE": "en_US"},
    ],
    "theme": {
        "file": "/ventoy/theme/itad/theme.txt",
        "gfxmode": "max",
        "display_mode": "GUI",
        "fonts": ["/ventoy/theme/itad/dvb30.pf2",
                  "/ventoy/theme/itad/dv20.pf2",
                  "/ventoy/theme/itad/dv16.pf2"],
    },
    "menu_alias": [{"image": "/%s.iso" % k, "alias": a}
                   for k, a, c, t in ITEMS],
    "menu_class": [{"key": k, "class": c} for k, a, c, t in ITEMS],
    "menu_tip": {"left": "22%", "top": "%d%%" % round(TIP_Y * 100), "color": "#8EA6C8",
                 "tips": [{"image": "/%s.iso" % k, "tip": t}
                          for k, a, c, t in ITEMS]},
}
with open(os.path.join(OUT, "ventoy", "ventoy.json"), "w") as fh:
    json.dump(cfg, fh, indent=2)

# --------------------------------------------------------------- preview
pv = bg.copy()
mx, my = int(W * MX), int(H * MY)
mw = int(W * MW)
sel = 0
for i, (k, a, c, t) in enumerate(ITEMS):
    y = my + i * (ITEM_H + ITEM_SP)
    if i == sel:
        ImageDraw.Draw(pv).rectangle([mx, y, mx + mw, y + ITEM_H],
                                     fill=rgb("#1E7BFF"))
        ImageDraw.Draw(pv).rectangle([mx, y, mx + 6, y + ITEM_H],
                                     fill=rgb("#38BDF8"))
    ic = Image.open(os.path.join(TD, "icons", c + ".png"))
    pv.paste(ic, (mx + 14, y + (ITEM_H - ICON) // 2), ic)
    draw_pf2(pv, os.path.join(TD, "dvb30.pf2"), mx + 14 + ICON + 26,
             y + ITEM_H // 2 + 11, a, "#FFFFFF" if i == sel else "#C9D8EE")
draw_pf2(pv, os.path.join(TD, "dv20.pf2"), int(W * 0.22), int(H * TIP_Y) + 20,
         ITEMS[sel][3], "#8EA6C8")
draw_pf2(pv, os.path.join(TD, "dv16.pf2"), int(W * .03), int(H * .95) - 4,
         "L:Language  F1:Help  F2:Browse  F3:TreeView  F4:Localboot  "
         "F5:Tools  F6:ExMenu", "#8EA6C8")
pv.save(os.path.join(OUT, "ventoy_menu_preview.png"))
print("done")
