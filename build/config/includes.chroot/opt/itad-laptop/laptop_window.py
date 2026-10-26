#!/usr/bin/env python3
"""
laptop_window.py - ITAD Laptop window (v1.5.0)
This is the GRAPHICAL USER INTERFACE (GUI).

v1.5.0
  - New automatic tests: Ethernet (cable link + speed) and Bluetooth
    (scans for nearby devices, e.g. a phone with Bluetooth ON).
  - Battery: discharge rate and estimated run time when the charger is
    unplugged.
  - New Speakers test window: LEFT, RIGHT and BOTH buttons. A voice says
    "Front left" / "Front right" so you can hear each speaker on its own.
    It uses the laptop's own speakers (not HDMI). Results: Both work /
    Left problem / Right problem / Crackling or no sound.
  - Tamper-proof report: every report is digitally signed (Ed25519) and
    the PDF has a QR code with the job, serial, result and fingerprint.
    The signing key is kept on the ITAD_DATA stick (keys folder).
    Check a report with verify_report.py.

v1.4.0
  - Keyboard test: number pad (Num Lock, 0-9, / * - + . Enter) shown on
    the right. It only has to be tested if the laptop has one: as soon
    as one number-pad key is pressed, all number-pad keys are needed.
  - New Webcam test: live camera picture, then Camera works / Picture
    bad / No camera.
  - New USB ports test: plug a USB stick into each port; every port is
    listed as it is found, then All ports work / A port does not work.
  - Every result can be changed by the technician: tap a result
    (for example Battery) and choose PASS, FAIL or Not fitted.
    The report says it was set by the technician.
  - Battery shows its health % next to the result.
  - On-screen test buttons are in two rows so they always fit the screen.

v1.3.0
  - TEST MODE (Settings > Test mode). For testing the tool on your own
    laptops (for example one that is still in MDM / Intune):
      * "Test components only" on the New job screen skips the check
        and the wipe and goes straight to the component tests.
      * After a check, "Skip wipe - test components" is always allowed,
        even when the result is BLOCKED.
      * Nothing is ever erased in Test mode.
      * Every report says TEST MODE and can never show PASS.
    Test mode is OFF after every boot. It stays on for "Next laptop".

v1.2.0
  - New look: dark blue design with a left menu (Home, New Job, Settings,
    About, Shutdown), a Home screen, and the 5 stages shown at the bottom
    of every screen: Check > Wiping > Testing > Observations > Reporting.
  - New Observations screen: cosmetic grade, issues, notes and photos.
    These go into the TXT and PDF report (photos are added to the PDF).
  - Optional picture on the Home screen: put hero.png in the tools folder.
v1.1.0
  - Technician sign in with password (stored only as a salted
    PBKDF2-SHA256 hash in technicians.txt).
  - Real RAM size, type, speed and slots. Screen size and resolution.
"""
import argparse
import base64
import glob
import hashlib
import hmac
import importlib.util
import json
import math
import os
import pty
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Dublin")
except Exception:
    TZ = None

VERSION = "1.5.0"
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

# --- THESE ARE THE FILES THIS GUI WILL RUN ---
CHECK = os.path.join(TOOLS_DIR, "laptop_check.py")    # The MDM/Autopilot scanner
WIPE = os.path.join(TOOLS_DIR, "laptop_wipe.py")      # The disk wiper
HW_TEST = os.path.join(TOOLS_DIR, "laptop_test.py")   # The automated CPU/RAM test
PY = sys.executable

# Colours (dark blue ITAD design)
BG, BG2, SIDE = "#050F22", "#081831", "#061430"
NAVY, CARD2 = "#0A1D3C", "#0B1F40"          # NAVY = card background
PANEL = BG                                  # page background
BORDER, BORDER_HI = "#1A3966", "#2B5FA8"
BLUE, BLUE2, CYAN = "#1E7BFF", "#2E9BFF", "#38BDF8"
TEXT, MUTED = "#EAF2FF", "#8EA6C8"
OK, BAD, WARN = "#34D399", "#F87171", "#FBBF24"
RED_BAN, GREEN_BAN = "#C62F2F", "#1FA971"
FONT = "DejaVu Sans"

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\([A-Z0-9]")
# The 5 stages at the bottom of the screen (in the real order of work)
STAGES = [("check", "Check", "Absolute lock and enrolment", "search",
           "#3B9BFF"),
          ("wipe", "Wiping", "Secure data erasure", "bin", "#F5B83D"),
          ("test", "Testing", "Run functional tests", "chip", "#2DD4BF"),
          ("obs", "Observations", "Record issues and photos", "clipboard",
           "#A78BFA"),
          ("report", "Reporting", "Generate final report", "doc",
           "#4F8BFF")]
GRADES = [("A", "Like new"), ("B", "Light wear"), ("C", "Heavy wear"),
          ("D", "Damaged / for parts")]
ISSUES = ["Scratches on lid or base", "Dents", "Screen scratches",
          "Screen damage / dead pixels", "Missing or worn keys",
          "Broken hinge", "Cracked or damaged case", "Swollen battery",
          "Missing parts (screws, feet, covers)", "Stickers or engraving",
          "Liquid damage signs", "No charger"]
TECH_FILE = "technicians.txt"
PBKDF_ITER = 200000
MIN_PASSWORD = 6
MAX_TRIES = 5          # wrong passwords before a short lock
LOCK_SECONDS = 30
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._'-]{1,29}$")
STD_SCREENS = [10.1, 11.6, 12.0, 12.3, 12.5, 13.0, 13.3, 13.4, 13.5, 13.6,
               14.0, 14.5, 15.0, 15.6, 16.0, 16.1, 17.0, 17.3, 18.0]
STD_RAM_GB = [1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 36, 40, 48, 64, 96, 128,
              192, 256]
LOCATIONS = ["Warehouse DPA3 Test Bay 1", "Warehouse DPA3 Test Bay 2",
             "Warehouse DPA3 Test Bay 3", "Warehouse DPA3 Test Bay 4", "Other"]
AUTO_TESTS = "cpu,ram,drive,battery,wifi,ethernet,bluetooth"
COMPONENTS = ["CPU", "RAM", "Drive Health", "Battery", "Wi-Fi", "Ethernet",
              "Bluetooth", "Webcam", "USB", "Screen", "Keyboard", "Touchpad",
              "Touchscreen", "Speakers", "Microphone"]
# On-screen tests the technician does (in button order)
MANUAL = ["Screen", "Keyboard", "Touchpad", "Touchscreen", "Speakers",
          "Microphone", "Webcam", "USB"]
# "N/A" = the laptop does not have this part (e.g. no touchscreen).
# It is not a problem and does not stop a PASS.
NOT_FITTED = "N/A"
BANNER_NAMES = {"CPU": "CPU", "RAM": "RAM", "DRIVE": "Drive Health",
                "BATTERY": "Battery", "WEBCAM": "Webcam", "WIFI": "Wi-Fi",
                "TOUCHPAD": "Touchpad", "USB": "USB", "ETHERNET": "Ethernet",
                "BLUETOOTH": "Bluetooth"}
BAD_SERIALS = {
    "", "none", "default string", "to be filled by o.e.m.",
    "to be filled by o.e.m", "system serial number", "not specified",
    "0", "n/a", "unknown", "o.e.m.", "oem", "chassis serial number",
    "0123456789", "1234567890",
}

SCREEN_COLORS = [("RED", "#FF0000", "white"), ("GREEN", "#00FF00", "black"),
                 ("BLUE", "#0000FF", "white"), ("WHITE", "#FFFFFF", "black"),
                 ("BLACK", "#000000", "white"), ("GREY", "#808080", "white")]
KB_ROWS = [
    [("ESC", "ESC")] + [(c, c) for c in "1234567890"]
    + [("-", "-"), ("=", "="), ("BKSP", "BKSP")],
    [("TAB", "TAB")] + [(c, c) for c in "QWERTYUIOP"]
    + [("[", "["), ("]", "]")],
    [("CAPS", "CAPS")] + [(c, c) for c in "ASDFGHJKL"]
    + [(";", ";"), ("'", "'"), ("ENTER", "ENTER")],
    [("SHIFT", "LSHIFT")] + [(c, c) for c in "ZXCVBNM"]
    + [(",", ","), (".", "."), ("/", "/"), ("SHIFT", "RSHIFT")],
    [("CTRL", "CTRL"), ("ALT", "ALT"), ("SPACE", "SPACE"), ("ALT", "RALT"),
     ("<", "<"), ("^", "^"), ("v", "v"), (">", ">")],
]
# Number pad (only needed if the laptop has one). Each key: (label, id).
NUMPAD_ROWS = [
    [("NUM", "KP_NUM"), ("/", "KP_/"), ("*", "KP_*"), ("-", "KP_-")],
    [("7", "KP_7"), ("8", "KP_8"), ("9", "KP_9"), ("+", "KP_+")],
    [("4", "KP_4"), ("5", "KP_5"), ("6", "KP_6")],
    [("1", "KP_1"), ("2", "KP_2"), ("3", "KP_3"), ("ENT", "KP_ENT")],
    [("0", "KP_0"), (".", "KP_.")],
]
# Number-pad keys give different names with Num Lock on or off.
NUMPAD_KEYMAP = {
    "Num_Lock": "KP_NUM", "KP_Divide": "KP_/", "KP_Multiply": "KP_*",
    "KP_Subtract": "KP_-", "KP_Add": "KP_+", "KP_Enter": "KP_ENT",
    "KP_0": "KP_0", "KP_Insert": "KP_0", "KP_1": "KP_1", "KP_End": "KP_1",
    "KP_2": "KP_2", "KP_Down": "KP_2", "KP_3": "KP_3", "KP_Next": "KP_3",
    "KP_Page_Down": "KP_3", "KP_4": "KP_4", "KP_Left": "KP_4",
    "KP_5": "KP_5", "KP_Begin": "KP_5", "KP_6": "KP_6", "KP_Right": "KP_6",
    "KP_7": "KP_7", "KP_Home": "KP_7", "KP_8": "KP_8", "KP_Up": "KP_8",
    "KP_9": "KP_9", "KP_Prior": "KP_9", "KP_Page_Up": "KP_9",
    "KP_Decimal": "KP_.", "KP_Delete": "KP_.", "KP_Separator": "KP_.",
}
KEYMAP = {
    "Escape": "ESC", "Tab": "TAB", "Caps_Lock": "CAPS", "Return": "ENTER",
    "space": "SPACE", "BackSpace": "BKSP",
    "Shift_L": "LSHIFT", "Shift_R": "RSHIFT", "Control_L": "CTRL",
    "Control_R": "CTRL", "Alt_L": "ALT", "Alt_R": "RALT",
    "ISO_Level3_Shift": "RALT", "Left": "<", "Right": ">", "Up": "^",
    "Down": "v", "minus": "-", "equal": "=", "bracketleft": "[",
    "bracketright": "]", "semicolon": ";", "apostrophe": "'", "comma": ",",
    "period": ".", "slash": "/", "exclam": "1", "at": "2", "numbersign": "3",
    "dollar": "4", "percent": "5", "asciicircum": "6", "ampersand": "7",
    "asterisk": "8", "parenleft": "9", "parenright": "0", "underscore": "-",
    "plus": "=", "braceleft": "[", "braceright": "]", "colon": ";",
    "quotedbl": "'", "less": ",", "greater": ".", "question": "/",
}


# =============================================================================
# plain helpers (no screen needed)
# =============================================================================
def now():
    return datetime.now(TZ) if TZ else datetime.now()


def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def valid_serial(s):
    return bool(s) and str(s).strip().lower() not in BAD_SERIALS


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s)) or "UNKNOWN"


def pick_out_dir():
    env = os.environ.get("ITAD_DATA")
    if env and os.path.isdir(env) and os.access(env, os.W_OK):
        return env, True
    for c in ("/mnt/itad", "/media/ITAD_DATA", "/mnt/ITAD_DATA"):
        if os.path.ismount(c) and os.access(c, os.W_OK):
            return c, True
    d = "/tmp/itad-reports"
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.expanduser("~/itad-reports")
        os.makedirs(d, exist_ok=True)
    return d, False


# =============================================================================
# technician accounts (technicians.txt)
#   One line per technician:   Name:pbkdf2_sha256$200000$<salt>$<hash>
#   A line with only a name has no password yet and cannot sign in.
#   The real password is NEVER written anywhere.
# =============================================================================
def tech_files(out_dir):
    seen, files = set(), []
    for base in (out_dir, "/mnt/itad", TOOLS_DIR):
        p = os.path.join(base, TECH_FILE)
        rp = os.path.realpath(p)
        if rp not in seen and os.path.isfile(p):
            seen.add(rp)
            files.append(p)
    return files


def parse_tech_line(ln):
    ln = ln.strip()
    if not ln or ln.startswith("#"):
        return None, ""
    if ":" in ln:
        name, rest = ln.split(":", 1)
    else:
        name, rest = re.split(r",", ln, 1)[0], ""
    name = name.strip()
    rest = rest.strip()
    return (name or None), (rest if rest.startswith("pbkdf2_sha256$") else "")


def load_accounts(out_dir):
    """Returns {lower name: {"name": Name, "hash": stored hash or ""}}."""
    acc = {}
    for p in tech_files(out_dir):
        try:
            with open(p) as f:
                for ln in f:
                    name, h = parse_tech_line(ln)
                    if not name:
                        continue
                    key = name.lower()
                    if key not in acc or (h and not acc[key]["hash"]):
                        acc[key] = {"name": name, "hash": h, "file": p}
        except Exception:
            pass
    return acc


def hash_password(password, salt=None, iterations=PBKDF_ITER):
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                             iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, salt.hex(), dk.hex())


def verify_password(password, stored):
    try:
        algo, it, salt, want = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), want)
    except Exception:
        return False


def save_account(out_dir, name, password):
    """Adds or updates one technician. Returns the file path used."""
    path = os.path.join(out_dir, TECH_FILE)
    lines = []
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except Exception:
        lines = ["# LaptopSeal technicians. Passwords are stored as hashes only.",
                 "# Add technicians from the sign-in screen."]
    new_line = "%s:%s" % (name, hash_password(password))
    done = False
    for i, ln in enumerate(lines):
        n, _ = parse_tech_line(ln)
        if n and n.lower() == name.lower():
            lines[i] = new_line
            done = True
    if not done:
        lines.append(new_line)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path


# =============================================================================
# hardware details for the card and the report
# =============================================================================
def run_cmd(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, p.stdout
    except Exception:
        return 1, ""


def unmute_audio():
    """A fresh live boot often starts muted or at 0%. Turn the main
    controls up and unmute them on every sound card before the tone."""
    if not shutil.which("amixer"):
        return
    try:
        with open("/proc/asound/cards") as f:
            cards = re.findall(r"^\s*(\d+)\s*\[", f.read(), re.M)
    except Exception:
        cards = []
    for c in cards or ["0"]:
        for ctl in ("Master", "Speaker", "PCM", "Front"):
            run_cmd(["amixer", "-q", "-c", c, "sset", ctl, "80%", "unmute"],
                    timeout=5)
        # microphone side: turn recording on and up
        for ctl in ("Capture", "Internal Mic", "Mic"):
            run_cmd(["amixer", "-q", "-c", c, "sset", ctl, "80%", "cap"],
                    timeout=5)


def speaker_device():
    """The laptop's own speakers, not HDMI / DisplayPort.
    Returns (alsa_device, description) or None for the default."""
    rc, out = 1, ""
    try:
        p = subprocess.run(["aplay", "-l"], capture_output=True, text=True,
                           timeout=5)
        rc, out = p.returncode, p.stdout
    except Exception:
        return None
    if rc != 0:
        return None
    for ln in out.splitlines():
        m = re.match(r"card (\d+): .*?\[(.*?)\], device (\d+): (.*?) \[",
                     ln)
        if not m:
            continue
        name = (m.group(2) + " " + m.group(4)).strip()
        if re.search(r"hdmi|displayport|\bdp\b", name, re.I):
            continue
        return ("plughw:%s,%s" % (m.group(1), m.group(3)), name)
    return None


def find_touchscreen():
    """Returns the name of a touchscreen, or "" if the laptop has none.
    A touchscreen is an input device marked INPUT_PROP_DIRECT (you touch
    the picture itself), unlike a touchpad."""
    for dev in sorted(glob.glob("/sys/class/input/event*/device")):
        try:
            with open(dev + "/properties") as f:
                props = int(f.read().strip() or "0", 16)
            with open(dev + "/name") as f:
                name = f.read().strip()
        except Exception:
            continue
        low = name.lower()
        if "pen" in low or "stylus" in low:
            continue
        if props & 0x2:          # INPUT_PROP_DIRECT
            return name
    return ""


def find_touchpad():
    """Returns the touchpad's name, or "" if none is found."""
    try:
        with open("/proc/bus/input/devices") as f:
            content = f.read()
    except Exception:
        return ""
    for block in content.split("\n\n"):
        low = block.lower()
        if any(k in low for k in ("touchpad", "synaptics", "trackpad",
                                   "alps", "elan")):
            m = re.search(r'N: Name="([^"]+)"', block)
            if m and "touchscreen" not in m.group(1).lower():
                return m.group(1)
    return ""


def capture_devices():
    """Recording devices to try: the default first, then each card."""
    devs = ["default"]
    rc, out = run_cmd(["arecord", "-l"], timeout=5)
    for card, dev in re.findall(r"card (\d+):.*?device (\d+):", out or ""):
        devs.append("plughw:%s,%s" % (card, dev))
    return devs


def wav_level(path):
    """Loudest sample of a 16-bit WAV file, from 0.0 (silence) to 1.0."""
    import array
    import wave
    try:
        with wave.open(path, "rb") as w:
            if w.getsampwidth() != 2:
                return 0.0
            data = array.array("h", w.readframes(w.getnframes()))
    except Exception:
        return 0.0
    if not data:
        return 0.0
    if sys.byteorder == "big":
        data.byteswap()
    # skip the first 0.2 s: some microphones click when they switch on
    skip = min(len(data) // 4, 8820)
    peak = max(abs(x) for x in data[skip:]) if len(data) > skip else 0
    return peak / 32768.0


def _size_to_gb(text):
    m = re.match(r"(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", text or "", re.I)
    if not m:
        return 0
    n, unit = float(m.group(1)), m.group(2).upper()
    return {"KB": n / 1048576, "MB": n / 1024, "GB": n, "TB": n * 1024}[unit]


def _gb_text(gb):
    return ("%d GB" % round(gb)) if abs(gb - round(gb)) < 0.05 else \
        ("%.1f GB" % gb)


def parse_dmidecode_memory(text):
    """Reads 'dmidecode -t 17' output. Returns a list of slot dicts."""
    slots = []
    cur = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line == "Memory Device":
            cur = {}
            slots.append(cur)
            continue
        if cur is None or ":" not in line or raw.startswith("\t\t"):
            continue
        k, v = line.split(":", 1)
        cur[k.strip()] = v.strip()
    out = []
    for s in slots:
        gb = _size_to_gb(s.get("Size", ""))
        speed = s.get("Configured Memory Speed") or s.get("Speed") or ""
        if speed.lower() in ("unknown", "none", "0 mt/s", "0 mhz"):
            speed = s.get("Speed", "")
        if speed.lower() in ("unknown", "none"):
            speed = ""
        out.append({
            "size_gb": gb,
            "type": s.get("Type", ""),
            "speed": speed.replace("MHz", "MT/s") if speed else "",
            "form": s.get("Form Factor", ""),
            "locator": s.get("Locator", ""),
            "maker": s.get("Manufacturer", ""),
            "part": s.get("Part Number", ""),
        })
    return out


def ram_from_meminfo():
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemTotal:"):
                    gb = int(ln.split()[1]) / 1048576
                    for std in STD_RAM_GB:
                        if gb <= std:
                            return std
                    return round(gb)
    except Exception:
        pass
    return 0


def ram_details():
    rc, out = run_cmd(["dmidecode", "-t", "17"])
    slots = parse_dmidecode_memory(out) if rc == 0 else []
    used = [s for s in slots if s["size_gb"] > 0]
    info = {"total_gb": 0, "type": "", "speed": "", "slots_total": len(slots),
            "slots_used": len(used), "modules": used, "soldered": False,
            "source": "dmidecode"}
    if used:
        info["total_gb"] = sum(s["size_gb"] for s in used)
        types = [s["type"] for s in used
                 if s["type"] and s["type"].lower() not in ("unknown", "other")]
        if types:
            info["type"] = max(set(types), key=types.count)
        speeds = []
        for s in used:
            m = re.match(r"(\d+)", s["speed"] or "")
            if m:
                speeds.append(int(m.group(1)))
        if speeds:
            info["speed"] = "%d MT/s" % max(speeds)
        info["soldered"] = any(s["form"].lower() in ("row of chips", "chip")
                               for s in used)
    else:
        info["total_gb"] = ram_from_meminfo()
        info["source"] = "estimate"
    return info


def ram_text(r, short=False):
    if not r or not r.get("total_gb"):
        return "-"
    main = " ".join(x for x in (_gb_text(r["total_gb"]), r.get("type"),
                                r.get("speed")) if x)
    if r.get("source") == "estimate":
        return main + " (estimated, type not readable)"
    if short:
        return main
    mods = " + ".join(_gb_text(m["size_gb"]) for m in r.get("modules", []))
    slots = "%d of %d slot%s used" % (r["slots_used"], r["slots_total"],
                                      "" if r["slots_total"] == 1 else "s")
    if r.get("soldered"):
        slots += ", soldered on board"
    return "%s\n%s: %s" % (main, slots, mods)


def parse_edid(b):
    """Returns dict with maker, name, width/height mm, resolution."""
    if len(b) < 128 or b[0:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    w = (b[8] << 8) | b[9]
    maker = "".join(chr(((w >> s) & 0x1F) + 64) for s in (10, 5, 0))
    info = {"maker": maker, "name": "", "w_mm": 0, "h_mm": 0,
            "res_w": 0, "res_h": 0}
    for off in (54, 72, 90, 108):
        d = b[off:off + 18]
        if d[0] == 0 and d[1] == 0:
            if d[3] == 0xFC:
                info["name"] = d[5:18].decode("ascii", "ignore").strip()
            continue
        if not info["res_w"]:
            info["res_w"] = d[2] | ((d[4] & 0xF0) << 4)
            info["res_h"] = d[5] | ((d[7] & 0xF0) << 4)
            info["w_mm"] = d[12] | ((d[14] & 0xF0) << 4)
            info["h_mm"] = d[13] | ((d[14] & 0x0F) << 8)
    if not info["w_mm"] or not info["h_mm"]:
        info["w_mm"], info["h_mm"] = b[21] * 10, b[22] * 10
    return info


def screen_details():
    found = []
    for d in sorted(glob.glob("/sys/class/drm/card*-*")):
        conn = os.path.basename(d).split("-", 1)[1]
        try:
            with open(os.path.join(d, "status")) as f:
                if f.read().strip() != "connected":
                    continue
            with open(os.path.join(d, "edid"), "rb") as f:
                e = parse_edid(f.read())
        except Exception:
            continue
        if e:
            e["connector"] = conn
            e["internal"] = bool(re.match(r"(eDP|LVDS|DSI)", conn))
            found.append(e)
    found.sort(key=lambda e: not e["internal"])
    if found:
        s = found[0]
    else:
        s = None
        rc, out = run_cmd(["xrandr", "--query"], timeout=5)
        for ln in out.splitlines():
            m = re.search(r"^(\S+) connected.*?(\d+)x(\d+)\+.*?(\d+)mm x "
                          r"(\d+)mm", ln)
            if m:
                s = {"connector": m.group(1), "maker": "", "name": "",
                     "res_w": int(m.group(2)), "res_h": int(m.group(3)),
                     "w_mm": int(m.group(4)), "h_mm": int(m.group(5)),
                     "internal": bool(re.match(r"(eDP|LVDS|DSI)",
                                               m.group(1)))}
                if s["internal"]:
                    break
    if not s:
        return None
    s["inches"] = 0
    if s["w_mm"] and s["h_mm"]:
        raw = math.hypot(s["w_mm"], s["h_mm"]) / 25.4
        near = min(STD_SCREENS, key=lambda x: abs(x - raw))
        # use the size printed on the box when it is very close
        s["inches"] = near if abs(near - raw) <= 0.2 else round(raw, 1)
    return s


def screen_text(s):
    if not s:
        return "not detected"
    parts = []
    if s.get("inches"):
        parts.append('%.1f"' % s["inches"])
    if s.get("res_w"):
        parts.append("%d x %d" % (s["res_w"], s["res_h"]))
    txt = "  ".join(parts) or "size unknown"
    extra = [x for x in (s.get("name") or s.get("maker"),
                         s.get("connector")) if x]
    if not s.get("internal"):
        extra.append("external screen")
    return txt + ("  (%s)" % ", ".join(extra) if extra else "")


def load_check_module():
    try:
        spec = importlib.util.spec_from_file_location("laptop_check_mod", CHECK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def quick_identity():
    mod = load_check_module()
    if mod:
        try:
            return mod.get_identity(), mod.scan_disks()[0]
        except Exception:
            pass

    def rd(n):
        try:
            with open("/sys/class/dmi/id/" + n) as f:
                return f.read().strip()
        except Exception:
            return ""
    return ({"manufacturer": rd("sys_vendor"), "model": rd("product_name"),
             "serial": rd("product_serial") or "UNKNOWN", "cpu": "", "ram_gb": 0,
             "device_type": "", "bios_vendor": rd("bios_vendor"),
             "bios_version": rd("bios_version"), "boot_mode": ""}, [])


def compute_overall(wipe_state, wipe_overall, tests):
    reasons = []
    failed = False
    if wipe_state == "FAILED" or wipe_overall == "FAIL":
        failed = True
        reasons.append("Wipe failed")
    for t in tests:
        if t.get("status") == "FAIL":
            failed = True
            reasons.append("%s failed" % t.get("component"))
    if failed:
        return "FAIL", reasons
    if wipe_state != "WIPED":
        reasons.append("Wipe not done (%s)" % wipe_state.lower())
    for t in tests:
        notes = t.get("notes") or []
        st = t.get("status")
        if "Not tested" in notes:
            reasons.append("%s not tested" % t.get("component"))
        elif st == "WARN":
            # A warning (e.g. weak battery, SMART unreadable) must be
            # looked at by a person: never report it as a clean PASS.
            reasons.append("%s needs checking%s" % (
                t.get("component"), (": " + notes[0][:70]) if notes else ""))
        elif st not in ("PASS", NOT_FITTED):
            reasons.append("%s not checked%s" % (
                t.get("component"), (": " + notes[0][:70]) if notes else ""))
    if reasons:
        return "INCOMPLETE", reasons
    return "PASS", []


def obs_lines(obs):
    obs = obs or {}
    out = [("Cosmetic grade", obs.get("grade") or "-"),
           ("Issues", ", ".join(obs.get("issues") or []) or "none"),
           ("Notes", (obs.get("notes") or "").strip() or "-"),
           ("Photos", ", ".join(os.path.basename(p) for p in
                                (obs.get("photo_files") or [])) or "none")]
    return out


def render_txt(rep):
    L = []
    add = L.append
    add("ITAD LAPTOP REPORT" + ("   *** TEST MODE ***" if rep.get("test_mode")
                                 else ""))
    add("RESULT: %s" % rep["overall"])
    for r in rep["reasons"]:
        add("  - %s" % r)
    add("=" * 60)
    for k, v in (("Job ID", rep["job_id"]), ("Asset ID", rep["asset_id"]),
                 ("Serial", rep["serial"]), ("Technician", rep["technician"]),
                 ("Location", rep["location"]), ("Date/time", rep["time"]),
                 ("Make / model", rep["make_model"]), ("CPU", rep["cpu"]),
                 ("RAM", rep["ram"]), ("Screen", rep.get("screen")),
                 ("Storage", rep["storage"])):
        add("%-14s: %s" % (k, v or "-"))
    add("")
    add("STAGES")
    add("%-14s: %s" % ("Absolute lock", rep["absolute"]))
    add("%-14s: %s" % ("Enrolment", rep["enrolment"]))
    add("%-14s: %s" % ("Wipe", rep["wipe_state"]))
    for d in rep["wipe_devices"]:
        add("   %s  %s  method=%s  verified=%s" % (
            d.get("path"), d.get("model") or "?", d.get("achieved", "-"),
            "yes" if (d.get("verification") or {}).get("verified") else "no"))
        ha = d.get("hidden_areas") or {}
        if ha.get("checked"):
            add("   hidden areas: HPA %s, DCO %s" % (ha.get("hpa"),
                                                   ha.get("dco")))
    if rep.get("certificate"):
        add("   certificate sha256: %s" % rep["certificate"])
    add("")
    add("COMPONENTS")
    for t in rep["tests"]:
        note = "; ".join(t.get("notes") or [])
        add("%-14s %-6s %s" % (t.get("component"), t.get("status"), note))
    add("")
    add("OBSERVATIONS")
    for k, v in obs_lines(rep.get("observations")):
        add("%-14s: %s" % (k, v))
    add("")
    add("Tool: ITAD Laptop window v%s" % VERSION)
    integ = rep.get("integrity") or {}
    if integ:
        add("")
        add("INTEGRITY")
        add("%-14s: %s" % ("SHA-256", integ.get("sha256", "-")))
        if integ.get("signed"):
            add("%-14s: Ed25519, key ID %s" % ("Signed", integ.get("key_id")))
            add("%-14s: %s" % ("Signature", integ.get("signature")))
        else:
            add("%-14s: NOT signed (%s)" % ("Signed", integ.get("note", "")))
    return "\n".join(L) + "\n"


SIGN_FIELDS = ("integrity",)


def report_fingerprint(rep):
    """SHA-256 of the report content (everything except the signature)."""
    body = dict((k, v) for k, v in rep.items() if k not in SIGN_FIELDS)
    data = json.dumps(body, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sign_report(rep, out_dir, persistent):
    """Add rep["integrity"]: fingerprint + Ed25519 signature.
    The private key lives on the ITAD_DATA stick (keys/), never in the ISO.
    Without the stick the report gets a fingerprint but no signature."""
    fp = report_fingerprint(rep)
    integ = {"algorithm": "SHA-256 + Ed25519", "sha256": fp,
             "signature": "", "key_id": "", "signed": False}
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)
    except Exception:
        integ["note"] = "cryptography module missing - not signed"
        rep["integrity"] = integ
        return integ
    if not persistent:
        integ["note"] = "no ITAD_DATA stick - not signed"
        rep["integrity"] = integ
        return integ
    kdir = os.path.join(out_dir, "keys")
    kpriv = os.path.join(kdir, "itad_signing_key.pem")
    kpub = os.path.join(kdir, "itad_signing_public.pem")
    try:
        os.makedirs(kdir, exist_ok=True)
        if os.path.exists(kpriv):
            with open(kpriv, "rb") as f:
                key = serialization.load_pem_private_key(f.read(), None)
        else:
            key = Ed25519PrivateKey.generate()
            with open(kpriv, "wb") as f:
                f.write(key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption()))
            os.chmod(kpriv, 0o600)
        pub = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        if not os.path.exists(kpub):
            with open(kpub, "wb") as f:
                f.write(key.public_key().public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo))
        integ["signature"] = key.sign(fp.encode("ascii")).hex()
        integ["key_id"] = hashlib.sha256(pub).hexdigest()[:16]
        integ["public_key"] = pub.hex()
        integ["signed"] = True
    except Exception as ex:
        integ["note"] = "signing failed: %s" % str(ex)[:80]
    rep["integrity"] = integ
    return integ


def qr_text(rep):
    integ = rep.get("integrity") or {}
    return "ITAD|%s|%s|%s|%s|%s|%s|%s" % (
        rep.get("job_id", ""), rep.get("asset_id", ""),
        rep.get("serial", ""), rep.get("overall", ""),
        rep.get("time", "")[:16], integ.get("sha256", "")[:32],
        integ.get("key_id", "") or "unsigned")


def write_pdf(path, rep):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)
    st = getSampleStyleSheet()
    col = {"PASS": "#1FA971", "FAIL": "#C62F2F"}.get(rep["overall"], "#B8860B")
    doc = SimpleDocTemplate(path, pagesize=A4, title="ITAD Laptop report")
    el = [Paragraph("ITAD Laptop - test and wipe report", st["Title"]),
          Paragraph('<font color="%s"><b>RESULT: %s</b></font>'
                    % (col, rep["overall"]), st["Heading2"])]
    for r in rep["reasons"]:
        el.append(Paragraph("- %s" % r, st["Normal"]))
    el.append(Spacer(1, 10))
    rows = [[k, Paragraph(str(v or "-"), st["Normal"])] for k, v in (
        ("Job ID", rep["job_id"]), ("Asset ID", rep["asset_id"]),
        ("Serial", rep["serial"]), ("Technician", rep["technician"]),
        ("Location", rep["location"]), ("Date/time", rep["time"]),
        ("Make / model", rep["make_model"]), ("CPU", rep["cpu"]),
        ("RAM", rep["ram"]), ("Screen", rep.get("screen")),
        ("Storage", rep["storage"]),
        ("Absolute lock", rep["absolute"]), ("Enrolment", rep["enrolment"]),
        ("Wipe", rep["wipe_state"]))]
    for d in rep.get("wipe_devices") or []:
        ha = d.get("hidden_areas") or {}
        txt = "%s %s - method %s, verified %s" % (
            d.get("path"), d.get("model") or "", d.get("achieved", "-"),
            "yes" if (d.get("verification") or {}).get("verified") else "no")
        if ha.get("checked"):
            txt += "<br/>Hidden areas: HPA %s, DCO %s" % (ha.get("hpa"),
                                                        ha.get("dco"))
        rows.append(["Wiped drive", Paragraph(txt, st["Normal"])])
    t = Table(rows, colWidths=[110, 380])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                           ("FONTSIZE", (0, 0), (-1, -1), 9),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    el += [t, Spacer(1, 12), Paragraph("Components", st["Heading3"])]
    rows = [["Component", "Status", "Notes"]]
    for x in rep["tests"]:
        rows.append([x.get("component"), x.get("status"),
                     Paragraph("; ".join(x.get("notes") or []) or "-",
                               st["Normal"])])
    t2 = Table(rows, colWidths=[100, 50, 340])
    t2.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                            ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    el.append(t2)
    el += [Spacer(1, 12), Paragraph("Observations", st["Heading3"])]
    rows = [[k, Paragraph(str(v), st["Normal"])]
            for k, v in obs_lines(rep.get("observations"))]
    t3 = Table(rows, colWidths=[110, 380])
    t3.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    el.append(t3)
    photos = (rep.get("observations") or {}).get("photo_files") or []
    if photos:
        try:
            from reportlab.lib.utils import ImageReader
            from reportlab.platypus import Image
            cells = []
            for p in photos[:6]:
                iw, ih = ImageReader(p).getSize()
                w = 155.0
                cells.append(Image(p, width=w, height=w * ih / float(iw)))
            grid = [cells[i:i + 3] for i in range(0, len(cells), 3)]
            el += [Spacer(1, 8), Table(grid)]
        except Exception:
            pass
    if rep.get("certificate"):
        el += [Spacer(1, 8), Paragraph("Wipe certificate SHA-256: %s"
                                       % rep["certificate"], st["Normal"])]
    integ = rep.get("integrity") or {}
    if integ:
        from reportlab.platypus import KeepTogether
        head = [Spacer(1, 12), Paragraph("Report integrity", st["Heading3"])]
        txt = ("Fingerprint (SHA-256): %s<br/>" % integ.get("sha256", "-"))
        if integ.get("signed"):
            txt += ("Digitally signed (Ed25519), key ID %s.<br/>Signature: "
                    "%s" % (integ.get("key_id"),
                            integ.get("signature", "")[:64] + "..."))
        else:
            txt += "NOT signed (%s)." % integ.get("note", "no key")
        para = Paragraph('<font size="7">%s</font>' % txt, st["Normal"])
        try:
            from reportlab.graphics.barcode.qr import QrCodeWidget
            from reportlab.graphics.shapes import Drawing
            qw = QrCodeWidget(qr_text(rep))
            x1, y1, x2, y2 = qw.getBounds()
            size = 95.0
            d = Drawing(size, size, transform=[size / (x2 - x1), 0, 0,
                                               size / (y2 - y1), 0, 0])
            d.add(qw)
            box = Table([[d, para]], colWidths=[105, 385])
            box.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            el.append(KeepTogether(head + [box]))
        except Exception:
            el.append(KeepTogether(head + [para]))
    doc.build(el)


def fallback_terminal(reason, dry_run=False):
    print("ITAD Laptop window not available (%s). Starting terminal version."
          % reason)
    args = [PY, os.path.join(TOOLS_DIR, "itad_run.py")]
    if dry_run:
        args.append("--dry-run")
    os.execv(PY, args)


# =============================================================================
# drawing helpers (Canvas): colours, rounded shapes and line icons
# =============================================================================
def mix(a, b, t):
    a, b = a.lstrip("#"), b.lstrip("#")
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(ca, cb))


def round_rect(c, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def draw_icon(c, kind, cx, cy, s, color, width=2):
    """Simple line icons. s = icon size in pixels."""
    h = s / 2.0
    w = max(1, int(width))
    def L(*p, **k):
        k.setdefault("width", w)
        return c.create_line(*p, fill=color, capstyle="round",
                             joinstyle="round", **k)
    if kind == "search":
        r = s * 0.30
        c.create_oval(cx - r - s * .08, cy - r - s * .08, cx + r - s * .08,
                      cy + r - s * .08, outline=color, width=w)
        L(cx + s * .14, cy + s * .14, cx + h * .85, cy + h * .85)
    elif kind == "chip":
        q = s * 0.26
        c.create_rectangle(cx - q, cy - q, cx + q, cy + q, outline=color,
                           width=w)
        c.create_rectangle(cx - q * .45, cy - q * .45, cx + q * .45,
                           cy + q * .45, outline=color, width=w)
        for k in (-0.5, 0, 0.5):
            d = q * k
            L(cx + d, cy - q, cx + d, cy - q - s * .12)
            L(cx + d, cy + q, cx + d, cy + q + s * .12)
            L(cx - q, cy + d, cx - q - s * .12, cy + d)
            L(cx + q, cy + d, cx + q + s * .12, cy + d)
    elif kind == "bin":
        L(cx - h * .7, cy - h * .5, cx + h * .7, cy - h * .5)
        L(cx - h * .2, cy - h * .5, cx - h * .2, cy - h * .7, cx + h * .2,
          cy - h * .7, cx + h * .2, cy - h * .5)
        c.create_polygon(cx - h * .55, cy - h * .35, cx + h * .55, cy - h * .35,
                         cx + h * .42, cy + h * .7, cx - h * .42, cy + h * .7,
                         outline=color, fill="", width=w)
        for k in (-0.2, 0.0, 0.2):
            L(cx + h * k, cy - h * .15, cx + h * k, cy + h * .5)
    elif kind in ("clipboard", "doc"):
        x1, y1, x2, y2 = cx - h * .55, cy - h * .7, cx + h * .55, cy + h * .75
        c.create_rectangle(x1, y1, x2, y2, outline=color, width=w)
        if kind == "clipboard":
            c.create_rectangle(cx - h * .25, y1 - h * .12, cx + h * .25,
                               y1 + h * .12, outline=color, fill=color)
        for k in (-0.3, 0.0, 0.3):
            L(x1 + h * .25, cy + h * k + h * .1, x2 - h * .25,
              cy + h * k + h * .1)
    elif kind == "shield":
        c.create_polygon(cx, cy - h * .85, cx + h * .7, cy - h * .55,
                         cx + h * .6, cy + h * .25, cx, cy + h * .85,
                         cx - h * .6, cy + h * .25, cx - h * .7, cy - h * .55,
                         outline=color, fill="", width=w, smooth=False)
        L(cx - h * .28, cy, cx - h * .05, cy + h * .25, cx + h * .32,
          cy - h * .22)
    elif kind == "lock":
        c.create_rectangle(cx - h * .45, cy - h * .05, cx + h * .45,
                           cy + h * .6, outline=color, fill=color, width=w)
        c.create_arc(cx - h * .3, cy - h * .55, cx + h * .3, cy + h * .1,
                     start=0, extent=180, style="arc", outline=color, width=w)
    elif kind == "leaf":
        c.create_polygon(cx - h * .6, cy + h * .6, cx - h * .55, cy - h * .2,
                         cx + h * .1, cy - h * .7, cx + h * .7, cy - h * .7,
                         cx + h * .6, cy - h * .05, cx + h * .1, cy + h * .55,
                         outline=color, fill="", width=w, smooth=True)
        L(cx - h * .6, cy + h * .6, cx + h * .35, cy - h * .35)
    elif kind in ("recycle", "refresh"):
        r = h * .62
        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=30, extent=140,
                     style="arc", outline=color, width=w)
        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=210, extent=140,
                     style="arc", outline=color, width=w)
        for ang in (30, 210):
            a = math.radians(ang)
            x, y = cx + r * math.cos(a), cy - r * math.sin(a)
            dx = 1 if ang == 30 else -1
            L(x - dx * h * .28, y - dx * h * .05, x, y,
              x + dx * h * .05, y - dx * h * .3)
    elif kind == "check":
        L(cx - h * .5, cy, cx - h * .12, cy + h * .4, cx + h * .55,
          cy - h * .4)
    elif kind == "cross":
        L(cx - h * .4, cy - h * .4, cx + h * .4, cy + h * .4)
        L(cx - h * .4, cy + h * .4, cx + h * .4, cy - h * .4)
    elif kind == "home":
        c.create_polygon(cx, cy - h * .75, cx + h * .75, cy - h * .05,
                         cx + h * .55, cy - h * .05, cx + h * .55, cy + h * .7,
                         cx - h * .55, cy + h * .7, cx - h * .55, cy - h * .05,
                         cx - h * .75, cy - h * .05, outline=color,
                         fill=color, width=w)
        c.create_rectangle(cx - h * .15, cy + h * .25, cx + h * .15,
                           cy + h * .7, outline="", fill=c["bg"])
    elif kind == "plus":
        L(cx - h * .6, cy, cx + h * .6, cy)
        L(cx, cy - h * .6, cx, cy + h * .6)
    elif kind == "gear":
        r1, r2 = h * .45, h * .72
        for k in range(8):
            a = math.radians(k * 45)
            L(cx + r1 * math.cos(a), cy + r1 * math.sin(a),
              cx + r2 * math.cos(a), cy + r2 * math.sin(a), width=w + 2)
        c.create_oval(cx - r1 - 1, cy - r1 - 1, cx + r1 + 1, cy + r1 + 1,
                      outline=color, width=w, fill=c["bg"])
        c.create_oval(cx - h * .18, cy - h * .18, cx + h * .18, cy + h * .18,
                      outline=color, width=w)
    elif kind == "info":
        c.create_oval(cx - h * .75, cy - h * .75, cx + h * .75, cy + h * .75,
                      outline=color, width=w)
        L(cx, cy - h * .05, cx, cy + h * .4)
        c.create_oval(cx - 1.5, cy - h * .38 - 1.5, cx + 1.5, cy - h * .38 + 1.5,
                      fill=color, outline=color)
    elif kind == "power":
        c.create_arc(cx - h * .65, cy - h * .6, cx + h * .65, cy + h * .7,
                     start=120, extent=300, style="arc", outline=color, width=w)
        L(cx, cy - h * .75, cx, cy - h * .05)
    elif kind == "user":
        c.create_oval(cx - h * .3, cy - h * .7, cx + h * .3, cy - h * .1,
                      outline=color, width=w)
        c.create_arc(cx - h * .65, cy + h * .05, cx + h * .65, cy + h * 1.25,
                     start=0, extent=180, style="arc", outline=color, width=w)
    elif kind == "monitor":
        c.create_rectangle(cx - h * .75, cy - h * .6, cx + h * .75, cy + h * .3,
                           outline=color, width=w)
        L(cx, cy + h * .3, cx, cy + h * .6)
        L(cx - h * .35, cy + h * .62, cx + h * .35, cy + h * .62)
        L(cx - h * .3, cy - h * .15, cx - h * .05, cy + h * .05, cx + h * .35,
          cy - h * .3)
    elif kind == "clock":
        c.create_oval(cx - h * .75, cy - h * .75, cx + h * .75, cy + h * .75,
                      outline=color, width=w)
        L(cx, cy - h * .45, cx, cy, cx + h * .3, cy + h * .2)
    elif kind == "play":
        c.create_polygon(cx - h * .3, cy - h * .45, cx + h * .5, cy,
                         cx - h * .3, cy + h * .45, fill=color, outline=color)
    elif kind == "arrow":
        L(cx - h * .25, cy - h * .45, cx + h * .25, cy, cx - h * .25,
          cy + h * .45)
    elif kind == "camera":
        c.create_rectangle(cx - h * .75, cy - h * .4, cx + h * .75, cy + h * .55,
                           outline=color, width=w)
        c.create_oval(cx - h * .3, cy - h * .2, cx + h * .3, cy + h * .4,
                      outline=color, width=w)


def draw_laptop_art(c, cx, cy, s):
    """The glowing laptop on the Home screen. s = laptop width."""
    rnd = __import__("random").Random(7)
    glow = "#0E3F80"
    # soft glow behind
    for i in range(18, 0, -1):
        t = i / 18.0
        rx, ry = s * (0.25 + 0.55 * t), s * (0.18 + 0.36 * t)
        c.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, outline="",
                      fill=mix(glow, BG, t ** 0.7))
    # orbit (glow + line)
    box = (cx - s * .78, cy - s * .48, cx + s * .70, cy + s * .30)
    c.create_arc(*box, start=165, extent=240, style="arc",
                 outline=mix(CYAN, BG, .65), width=9)
    c.create_arc(*box, start=165, extent=240, style="arc", outline=CYAN,
                 width=3)
    # screen corners (a little perspective)
    tl, tr = (cx - s * .40, cy - s * .33), (cx + s * .34, cy - s * .40)
    br, bl = (cx + s * .38, cy + s * .12), (cx - s * .36, cy + s * .15)
    c.create_polygon(*tl, *tr, *br, *bl, fill="#071F44",
                     outline="#8FD3FF", width=3)

    def lerp(p, q, t):
        return p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
    for k in range(1, 12):
        t = k / 12.0
        a, b = lerp(tl, tr, t), lerp(bl, br, t)
        c.create_line(*a, *b, fill="#1B5AA6", width=1)
    for k in range(1, 8):
        t = k / 8.0
        a, b = lerp(tl, bl, t), lerp(tr, br, t)
        c.create_line(*a, *b, fill="#1B5AA6", width=1)
    # data flowing out of the screen (small squares)
    for _ in range(46):
        t1, t2 = rnd.random(), rnd.random()
        x = tr[0] - s * .05 + t1 * s * .32
        y = tr[1] + s * .05 + t2 * s * .45
        q = s * (0.006 + rnd.random() * 0.014)
        c.create_rectangle(x - q, y - q, x + q, y + q, outline="",
                           fill=mix(CYAN, BG, rnd.random() * .6))
    # base and keyboard
    base = (bl[0], bl[1], br[0], br[1], cx + s * .56, cy + s * .30,
            cx - s * .54, cy + s * .35)
    c.create_polygon(*base, fill="#1A2C47", outline="#A9C2E0", width=2)
    for k in range(1, 5):
        t = k / 5.0 * 0.62
        a = lerp(bl, (cx - s * .54, cy + s * .35), t)
        b = lerp(br, (cx + s * .56, cy + s * .30), t)
        c.create_line(a[0] + s * .08, a[1], b[0] - s * .10, b[1],
                      fill="#4E6A90", width=2, dash=(4, 2))
    # arrow coming in from the left
    c.create_line(cx - s * .72, cy - s * .02, cx - s * .55, cy - s * .10,
                  cx - s * .22, cy - s * .08, fill=CYAN, width=3,
                  smooth=True, arrow="last", arrowshape=(14, 16, 6))
    # shield with lock above the screen
    sx, sy, sh = cx - s * .20, cy - s * .48, s * .20
    c.create_polygon(sx, sy - sh * .5, sx + sh * .42, sy - sh * .32,
                     sx + sh * .36, sy + sh * .15, sx, sy + sh * .5,
                     sx - sh * .36, sy + sh * .15, sx - sh * .42, sy - sh * .32,
                     fill="#0A2A5C", outline="#9EDCFF", width=3)
    draw_icon(c, "lock", sx, sy + sh * .02, sh * .55, "#CFEFFF", 2)
    # three tiles: check, bin, refresh
    for (tx, ty, kind) in ((cx + s * .62, cy - s * .30, "check"),
                           (cx + s * .72, cy - s * .06, "bin"),
                           (cx + s * .58, cy + s * .14, "refresh")):
        q = s * .085
        round_rect(c, tx - q, ty - q, tx + q, ty + q, q * .35,
                   fill="#0A2550", outline=CYAN, width=3)
        draw_icon(c, kind, tx, ty, q * 1.25, "#DFF4FF", 3)


# =============================================================================
# the window
# =============================================================================
class Pill:
    """Rounded button drawn on a Canvas (main buttons)."""

    def __init__(self, app, parent, text, cmd, width=300, height=54,
                 kind="primary", icon=None):
        self.app, self.cmd, self.kind, self.icon = app, cmd, kind, icon
        self.text, self.state, self.hover = text, "normal", False
        self.w, self.h = app.px(width), app.px(height)
        self.c = app.tk.Canvas(parent, width=self.w, height=self.h,
                               bg=parent["bg"], highlightthickness=0,
                               cursor="hand2")
        self.c.bind("<Button-1>", lambda e: self.click())
        self.c.bind("<Enter>", lambda e: self.set_hover(True))
        self.c.bind("<Leave>", lambda e: self.set_hover(False))
        self.draw()

    def set_hover(self, v):
        self.hover = v
        self.draw()

    def click(self):
        if self.state == "normal" and self.cmd:
            self.cmd()

    def configure(self, text=None, state=None, command=None):
        if text is not None:
            self.text = text
        if state is not None:
            self.state = state
        if command is not None:
            self.cmd = command
        self.c.configure(cursor="hand2" if self.state == "normal" else "")
        self.draw()

    config = configure

    def pack(self, **kw):
        self.c.pack(**kw)
        return self

    def grid(self, **kw):
        self.c.grid(**kw)
        return self

    def pack_forget(self):
        self.c.pack_forget()

    def draw(self):
        c, w, h, r = self.c, self.w, self.h, self.h / 2.0
        c.delete("all")
        off = self.state != "normal"
        if self.kind == "primary":
            c0, c1 = ("#2A3B55", "#2A3B55") if off else (
                ("#5CD0FF", "#2A8BFF") if self.hover else ("#3EC3FF", "#1C74F2"))
            # round ends first, then the colour fade between them
            c.create_oval(0, 0, 2 * r, h - 1, fill=c0, outline=c0)
            c.create_oval(w - 2 * r - 1, 0, w - 1, h - 1, fill=c1, outline=c1)
            steps = max(2, int(w - 2 * r))
            for i in range(steps + 1):
                col = mix(c0, c1, i / float(steps))
                c.create_line(r + i, 0, r + i, h, fill=col)
            fg = "#7F93B0" if off else "white"
        elif self.kind == "danger":
            col = "#3A2A33" if off else ("#E5484D" if self.hover else "#C93A3F")
            round_rect(c, 1, 1, w - 1, h - 1, r, fill=col, outline=col)
            fg = "#8E7C86" if off else "white"
        else:  # outline / ghost
            line = "#3A4E6E" if off else (CYAN if self.hover else
                                          ("#2E8BFF" if self.kind == "outline"
                                           else BORDER_HI))
            fill = BG2 if self.kind == "ghost" else c["bg"]
            round_rect(c, 2, 2, w - 2, h - 2, min(r, self.app.px(10)),
                       fill=fill, outline=line, width=2)
            fg = "#6F86A8" if off else (CYAN if self.kind == "outline"
                                        else TEXT)
        font = self.app.F(15 if self.h >= self.app.px(60) else
                          (12 if self.h >= self.app.px(46) else 11), True)
        if self.icon:
            ix = w / 2.0 - self.app.measure(self.text, font) / 2.0 - h * .35
            if self.kind == "primary":
                c.create_oval(ix - h * .30, h * .2, ix + h * .30, h * .8,
                              fill="#06142B", outline="")
                draw_icon(c, self.icon, ix + 2, h / 2.0, h * .34,
                          "white" if not off else "#7F93B0", 2)
            else:
                draw_icon(c, self.icon, ix, h / 2.0, h * .42, fg, 2)
            c.create_text(w / 2.0 + h * .2, h / 2.0, text=self.text, fill=fg,
                          font=font)
        else:
            c.create_text(w / 2.0, h / 2.0, text=self.text, fill=fg, font=font)


class App:
    def __init__(self, tk, ttk, messagebox, root, dry_run, quick, windowed):
        self.tk, self.ttk, self.mb, self.root = tk, ttk, messagebox, root
        self.dry, self.quick = dry_run, quick
        # Test mode is never saved to disk: it is OFF after every boot.
        self.test_mode = os.environ.get("ITAD_TEST_MODE") == "1"
        self.q = queue.Queue()
        self.log = []
        self.wiping = False
        self.job_active = False
        self.job_page = "start"
        self.out_dir, self.persistent = pick_out_dir()
        self.p_check = os.path.join(self.out_dir, "laptop_check.json")
        self.p_wipe_dry = os.path.join(self.out_dir, "laptop_wipe_preview.json")
        self.p_wipe = os.path.join(self.out_dir, "laptop_wipe.json")
        self.p_test = os.path.join(self.out_dir, "laptop_test.json")
        self.ident, self.disks = quick_identity()
        self.ram = ram_details()
        self.screen = screen_details()
        ser = self.ident.get("serial", "")
        self.s = {"job_id": "JOB-" + now().strftime("%Y%m%d-%H%M%S"),
                  "serial": ser if valid_serial(ser) else "",
                  "check": None, "wipe_dry": None, "wipe": None,
                  "wipe_state": "NOT RUN", "auto": [], "manual": {},
                  "obs": {"grade": "", "issues": [], "notes": "",
                          "photos": []}}
        self.pages, self.nav_items, self.stage_w = {}, {}, {}
        self.test_rows, self.manual_btns = {}, {}
        self.fails, self.locked_until = 0, 0.0
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        if windowed:
            sw, sh = 1366, 768
        self.sc = max(0.78, min(1.3, min(sw / 1600.0, sh / 900.0)))
        self._fonts = {}
        root.title("ITAD Laptop")
        root.configure(bg=BG)
        if not windowed:
            try:
                root.attributes("-fullscreen", True)
            except Exception:
                pass
        else:
            root.geometry("1366x768")
        self.setup_style()
        self.build_shell()
        self.build_home()
        self.build_login()
        self.build_start()
        self.build_checks()
        self.build_wipe()
        self.build_observe()
        self.build_report()
        self.build_settings()
        self.build_about()
        self.reset_stages()
        # "Next laptop" restarts the window but keeps the technician
        # signed in (only if that account still has a password).
        keep = os.environ.get("ITAD_SIGNED_IN", "")
        acc = load_accounts(self.out_dir).get(keep.lower())
        if keep and acc and acc["hash"]:
            self.signed_in(acc["name"])
        else:
            os.environ.pop("ITAD_SIGNED_IN", None)
            self.v_tech.set("")
            self.show("home")
        self.refresh_home()
        self.tick()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.bind("<Control-q>", lambda e: self.quit())
        root.bind("<Escape>", lambda e: self.toggle_fullscreen())
        root.after(100, self.pump)

    # ---------- small widget helpers ----------
    def px(self, n):
        return int(round(n * self.sc))

    def F(self, size, bold=False):
        return (FONT, max(7, int(round(size * self.sc))),
                "bold" if bold else "normal")

    def measure(self, text, font):
        key = font
        if key not in self._fonts:
            self._fonts[key] = self.tk.font.Font(family=font[0], size=font[1],
                                                 weight=font[2])
        return self._fonts[key].measure(text)

    def lbl(self, parent, text="", size=11, bold=False, fg=TEXT, bg=None, **kw):
        if "wraplength" in kw:
            kw["wraplength"] = self.px(kw["wraplength"])
        return self.tk.Label(parent, text=text, font=self.F(size, bold), fg=fg,
                             bg=bg if bg else NAVY, anchor="w", justify="left",
                             **kw)

    def btn(self, parent, text, cmd, kind="primary", width=None):
        # kind: primary / ghost / danger / outline
        w = width or max(120, int(self.measure(text, self.F(11, True))
                                  / self.sc) + 44)
        return Pill(self, parent, text, cmd, width=w, height=42, kind=kind)

    def icon(self, parent, kind, size=26, color=CYAN, bg=None, width=2):
        s = self.px(size)
        c = self.tk.Canvas(parent, width=s, height=s, bg=bg or parent["bg"],
                           highlightthickness=0)
        draw_icon(c, kind, s / 2.0, s / 2.0, s * .8, color, width)
        return c

    def card(self, parent, title, icon=None):
        tk = self.tk
        outer = tk.Frame(parent, bg=NAVY, highlightbackground=BORDER,
                         highlightthickness=1)
        head = tk.Frame(outer, bg=NAVY)
        head.pack(fill="x", padx=self.px(16), pady=(self.px(12), self.px(4)))
        if icon:
            self.icon(head, icon, 18, CYAN, NAVY).pack(side="left",
                                                      padx=(0, self.px(8)))
        tk.Label(head, text=title.upper(), font=self.F(9, True), fg=MUTED,
                 bg=NAVY, anchor="w").pack(side="left")
        inner = tk.Frame(outer, bg=NAVY)
        inner.pack(fill="both", expand=True, padx=self.px(16),
                   pady=(0, self.px(14)))
        return outer, inner

    def entry(self, parent, var, readonly=False, secret=False):
        return self.tk.Entry(parent, textvariable=var, bg=BG2, fg=TEXT,
                             insertbackground=TEXT, relief="flat",
                             font=self.F(12), highlightthickness=1,
                             highlightbackground=BORDER, highlightcolor=CYAN,
                             readonlybackground=BORDER, disabledforeground=MUTED,
                             show="•" if secret else "",
                             state="readonly" if readonly else "normal")

    def setup_style(self):
        ttk = self.ttk
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TCombobox", fieldbackground=BG2, background=BORDER,
                     foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, padding=4)
        st.map("TCombobox", fieldbackground=[("readonly", BG2)],
               foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", BG2)],
               selectforeground=[("readonly", TEXT)])
        st.configure("Seal.Horizontal.TProgressbar", troughcolor=BORDER,
                     background=CYAN, bordercolor=BORDER, lightcolor=CYAN,
                     darkcolor=CYAN, thickness=self.px(14))
        for opt, val in (("*TCombobox*Listbox.background", BG2),
                         ("*TCombobox*Listbox.foreground", TEXT),
                         ("*TCombobox*Listbox.selectBackground", BLUE),
                         ("*TCombobox*Listbox.font", self.F(12))):
            self.root.option_add(opt, val)

    # ---------- the frame around every screen ----------
    def build_shell(self):
        tk, root = self.tk, self.root
        # header
        head = tk.Frame(root, bg=BG)
        head.pack(fill="x")
        logo = tk.Canvas(head, width=self.px(62), height=self.px(70), bg=BG,
                         highlightthickness=0)
        logo.pack(side="left", padx=(self.px(28), self.px(12)),
                  pady=self.px(10))
        self.draw_logo(logo, self.px(31), self.px(35), self.px(58))
        brand = tk.Frame(head, bg=BG)
        brand.pack(side="left")
        name = tk.Frame(brand, bg=BG)
        name.pack(anchor="w")
        tk.Label(name, text="IT", font=self.F(30, True), fg="white",
                 bg=BG).pack(side="left")
        tk.Label(name, text="AD", font=self.F(30, True), fg=BLUE2,
                 bg=BG).pack(side="left")
        tk.Label(brand, text="S E C U R E   •   T E S T   •   "
                             "W I P E   •   R E D E P L O Y",
                 font=self.F(8), fg="#BFD3F2", bg=BG).pack(anchor="w")
        # status box (right)
        box = tk.Frame(head, bg=CARD2, highlightbackground=BORDER,
                       highlightthickness=1)
        box.pack(side="right", padx=self.px(28))
        self.icon(box, "monitor", 30, "#D8E6FA", CARD2).pack(
            side="left", padx=(self.px(16), self.px(8)), pady=self.px(10))
        s1 = tk.Frame(box, bg=CARD2)
        s1.pack(side="left", padx=(0, self.px(18)))
        tk.Label(s1, text="Booted from USB", font=self.F(10), fg=TEXT,
                 bg=CARD2).pack(anchor="w")
        self.h_ready = tk.Label(s1, text="", font=self.F(9), bg=CARD2)
        self.h_ready.pack(anchor="w")
        tk.Frame(box, bg=BORDER, width=1).pack(side="left", fill="y",
                                               pady=self.px(10))
        self.icon(box, "clock", 28, "#D8E6FA", CARD2).pack(
            side="left", padx=(self.px(16), self.px(8)))
        s2 = tk.Frame(box, bg=CARD2)
        s2.pack(side="left", padx=(0, self.px(18)))
        self.h_time = tk.Label(s2, text="", font=self.F(10), fg=TEXT,
                               bg=CARD2)
        self.h_time.pack(anchor="w")
        self.h_date = tk.Label(s2, text="", font=self.F(9), fg=MUTED,
                               bg=CARD2)
        self.h_date.pack(anchor="w")
        self.h_sep2 = tk.Frame(box, bg=BORDER, width=1)
        self.h_user_i = self.icon(box, "user", 26, "#D8E6FA", CARD2)
        self.h_user = tk.Label(box, text="", font=self.F(10, True), fg=TEXT,
                               bg=CARD2)
        if self.dry:
            tk.Label(head, text="  DRY RUN - nothing will be erased  ",
                     font=self.F(10, True), fg=NAVY, bg=WARN).pack(
                side="right", padx=4)
        self.h_test = tk.Label(head, text="  TEST MODE - no wipe, not for "
                                          "customer laptops  ",
                               font=self.F(10, True), fg="white", bg=RED_BAN)
        if self.test_mode:
            self.h_test.pack(side="right", padx=4)
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x")

        # footer
        foot = tk.Frame(root, bg=BG)
        foot.pack(side="bottom", fill="x")
        tk.Frame(root, bg=BORDER, height=1).pack(side="bottom", fill="x")
        tk.Label(foot, text="ITAD Laptop v%s" % VERSION, font=self.F(9),
                 fg=MUTED, bg=BG).pack(side="left", padx=self.px(30),
                                       pady=self.px(8))
        tk.Label(foot, text="Better devices. A cleaner planet.",
                 font=self.F(10), fg=TEXT, bg=BG).pack(side="right",
                                                       padx=(0, self.px(30)))
        self.icon(foot, "leaf", 20, OK, BG).pack(side="right",
                                                 padx=(0, self.px(8)))

        # left menu
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True)
        side = tk.Frame(body, bg=SIDE, width=self.px(230))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(body, bg=BORDER, width=1).pack(side="left", fill="y")
        tk.Frame(side, bg=SIDE, height=self.px(26)).pack()
        for key, text, ic, cmd in (
                ("home", "Home", "home", lambda: self.show("home")),
                ("job", "New Job", "plus", self.nav_new_job),
                ("settings", "Settings", "gear", lambda: self.show("settings")),
                ("about", "About", "info", lambda: self.show("about")),
                ("power", "Shutdown", "power", self.power_off)):
            self.nav_items[key] = self.nav_item(side, text, ic, cmd)

        # main area: screens on top, the 5 stages at the bottom
        main = tk.Frame(body, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        self.build_stagebar(main)
        self.content = tk.Frame(main, bg=BG)
        self.content.pack(fill="both", expand=True)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

    def draw_logo(self, c, cx, cy, s):
        h = s / 2.0
        pts = (cx, cy - h * .95, cx + h * .85, cy - h * .6, cx + h * .75,
               cy + h * .3, cx, cy + h * .95, cx - h * .75, cy + h * .3,
               cx - h * .85, cy - h * .6)
        c.create_polygon(*pts, fill="#0B2A5A", outline="#9FD8FF", width=3)
        q = h * .42
        round_rect(c, cx - q, cy - q * .8, cx + q, cy + q * .45, 4,
                   fill="", outline="white", width=3)
        c.create_line(cx - q * 1.25, cy + q * .75, cx + q * 1.25,
                      cy + q * .75, fill="white", width=3, capstyle="round")

    def nav_item(self, parent, text, ic, cmd):
        tk = self.tk
        f = tk.Frame(parent, bg=SIDE, highlightthickness=1,
                     highlightbackground=SIDE, cursor="hand2")
        f.pack(fill="x", padx=self.px(16), pady=self.px(6))
        c = tk.Canvas(f, width=self.px(40), height=self.px(40), bg=SIDE,
                      highlightthickness=0)
        c.pack(side="left", padx=(self.px(10), self.px(12)), pady=self.px(6))
        lab = tk.Label(f, text=text, font=self.F(12), fg=TEXT, bg=SIDE)
        lab.pack(side="left")
        item = {"frame": f, "canvas": c, "label": lab, "icon": ic,
                "active": False}

        def paint(hover=False):
            act = item["active"]
            bg = "#1767E6" if act else ("#0F2A52" if hover else SIDE)
            f.configure(bg=bg, highlightbackground="#3B8BFF" if act else bg)
            c.configure(bg=bg)
            lab.configure(bg=bg, font=self.F(12, act))
            c.delete("all")
            s = self.px(40)
            if not act:
                round_rect(c, 2, 2, s - 2, s - 2, self.px(9), fill="#0E2447",
                           outline="#21406E", width=1)
            draw_icon(c, ic, s / 2.0, s / 2.0, s * .48, "white", 2)
        item["paint"] = paint
        for w in (f, c, lab):
            w.bind("<Button-1>", lambda e: cmd())
            w.bind("<Enter>", lambda e: paint(True))
            w.bind("<Leave>", lambda e: paint(False))
        paint()
        return item

    def set_nav(self, key):
        for k, it in self.nav_items.items():
            it["active"] = (k == key)
            it["paint"]()

    def build_stagebar(self, parent):
        tk = self.tk
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(side="bottom", fill="x", padx=self.px(28),
                  pady=(self.px(4), self.px(16)))
        bar = tk.Frame(wrap, bg=NAVY, highlightbackground=BORDER,
                       highlightthickness=1)
        bar.pack(fill="x")
        col = 0
        for i, (key, title, sub, ic, color) in enumerate(STAGES):
            cell = tk.Frame(bar, bg=NAVY)
            cell.grid(row=0, column=col, sticky="w", padx=self.px(12),
                      pady=self.px(12))
            bar.grid_columnconfigure(col, weight=1, uniform="stage")
            s = self.px(58)
            c = tk.Canvas(cell, width=s, height=s, bg=NAVY,
                          highlightthickness=0)
            c.pack(side="left", padx=(0, self.px(12)))
            txt = tk.Frame(cell, bg=NAVY)
            txt.pack(side="left")
            t = tk.Label(txt, text="%d. %s" % (i + 1, title),
                         font=self.F(12, True), fg=color, bg=NAVY)
            t.pack(anchor="w")
            st = tk.Label(txt, text=sub, font=self.F(9), fg=MUTED, bg=NAVY,
                          justify="left", wraplength=self.px(128))
            st.pack(anchor="w")
            self.stage_w[key] = {"canvas": c, "title": t, "status": st,
                                 "color": color, "icon": ic, "sub": sub}
            col += 1
            if i < len(STAGES) - 1:
                ar = tk.Canvas(bar, width=self.px(18), height=self.px(30),
                               bg=NAVY, highlightthickness=0)
                draw_icon(ar, "arrow", self.px(9), self.px(15), self.px(26),
                          "#5D8FD6", 2)
                ar.grid(row=0, column=col)
                col += 1

    def set_stage(self, key, state, text=None):
        w = self.stage_w[key]
        c, s, color = w["canvas"], self.px(58), w["color"]
        c.delete("all")
        ring = {"run": CYAN, "ok": color, "warn": color, "bad": color,
                "idle": color}.get(state, color)
        if state == "idle":
            ring = mix(color, NAVY, .25)
        c.create_oval(4, 4, s - 4, s - 4, outline=mix(ring, NAVY, .7),
                      width=self.px(8))
        c.create_oval(5, 5, s - 5, s - 5, outline=ring, width=3,
                      fill="#081A35")
        draw_icon(c, w["icon"], s / 2.0, s / 2.0, s * .48, ring, 3)
        if state in ("ok", "bad", "warn"):
            # small status badge at the bottom right of the circle
            bc = {"ok": OK, "bad": BAD, "warn": WARN}[state]
            r = s * .17
            bx, by = s - r - 1, s - r - 1
            c.create_oval(bx - r, by - r, bx + r, by + r, fill=bc,
                          outline=NAVY, width=2)
            if state == "warn":
                c.create_text(bx, by, text="!", fill=NAVY,
                              font=self.F(9, True))
            else:
                draw_icon(c, "check" if state == "ok" else "cross", bx, by,
                          r * 1.5, NAVY, 2)
        fg = {"run": CYAN, "ok": OK, "bad": BAD, "warn": WARN}.get(state, MUTED)
        w["status"].configure(text=text or w["sub"], fg=fg)

    def reset_stages(self):
        for key in self.stage_w:
            self.set_stage(key, "idle")

    def tick(self):
        t = now()
        self.h_time.configure(text=t.strftime("%I:%M %p").lstrip("0"))
        self.h_date.configure(text="%d %s" % (t.day, t.strftime("%b %Y")))
        if self.persistent:
            self.h_ready.configure(text="● Ready", fg=OK)
        else:
            self.h_ready.configure(text="● No ITAD_DATA stick", fg=WARN)
        self.root.after(1000, self.tick)

    def new_page(self, name):
        f = self.tk.Frame(self.content, bg=BG)
        f.grid(row=0, column=0, sticky="nsew")
        self.pages[name] = f
        return f

    def bar(self, page):
        b = self.tk.Frame(page, bg=BG)
        b.pack(side="bottom", fill="x", padx=self.px(28),
               pady=(self.px(6), self.px(10)))
        left = self.tk.Frame(b, bg=BG)
        left.pack(side="left")
        right = self.tk.Frame(b, bg=BG)
        right.pack(side="right")
        mid = self.lbl(b, "", 10, fg=MUTED, bg=BG)
        mid.pack(side="left", padx=self.px(18))
        return left, right, mid

    def page_title(self, page, title, sub=""):
        f = self.tk.Frame(page, bg=BG)
        f.pack(fill="x", padx=self.px(28), pady=(self.px(18), self.px(8)))
        self.lbl(f, title, 20, True, bg=BG).pack(anchor="w")
        if sub:
            self.lbl(f, sub, 10, fg=MUTED, bg=BG).pack(anchor="w")
        return f

    def show(self, name):
        self.pages[name].tkraise()
        nav = {"home": "home", "settings": "settings",
               "about": "about"}.get(name, "job")
        self.set_nav(nav)
        if name in ("start", "checks", "wipe", "observe", "report"):
            self.job_page = name
        if name == "home":
            self.refresh_home()
        if name == "settings":
            self.refresh_settings()

    def toggle_fullscreen(self):
        try:
            cur = bool(self.root.attributes("-fullscreen"))
            self.root.attributes("-fullscreen", not cur)
        except Exception:
            pass

    # ---------- running the other tools ----------
    def pump(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def run_stage(self, cmd, on_line, on_done):
        def work():
            master, slave = pty.openpty()
            env = dict(os.environ)
            env.update(NO_COLOR="1", TERM="xterm", PYTHONUNBUFFERED="1")
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                     stdout=slave, stderr=slave, env=env,
                                     close_fds=True)
            except Exception as e:
                os.close(slave)
                os.close(master)
                msg = str(e)
                self.q.put(lambda: self._line(on_line, "ERROR: " + msg))
                self.q.put(lambda: on_done(127))
                return
            os.close(slave)
            buf = b""
            while True:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                parts = re.split(rb"[\r\n]", buf)
                buf = parts.pop()
                for part in parts:
                    ln = ANSI_RE.sub("", part.decode("utf-8", "replace")).strip()
                    if ln:
                        self.q.put(lambda l=ln: self._line(on_line, l))
            rc = p.wait()
            try:
                os.close(master)
            except OSError:
                pass
            self.q.put(lambda: on_done(rc))
        threading.Thread(target=work, daemon=True).start()

    def _line(self, on_line, ln):
        self.log.append(ln)
        on_line(ln)

    def common_args(self):
        return ["--job-id", self.job(), "--asset-id", self.asset(),
                "--tech", self.tech(), "--no-color"]

    def job(self):
        return self.v_job.get().strip()

    def asset(self):
        return self.v_asset.get().strip()

    def tech(self):
        return self.v_tech.get().strip()

    # ---------- Home ----------
    def build_home(self):
        tk = self.tk
        pg = self.new_page("home")
        self.v_tech = tk.StringVar(value="")
        self.v_job = tk.StringVar(value=self.s["job_id"])
        self.v_asset = tk.StringVar(value=self.s["serial"])
        self.v_loc = tk.StringVar(value=LOCATIONS[0])
        right = tk.Frame(pg, bg=BG, width=self.px(370))
        right.pack(side="right", fill="y", padx=(0, self.px(28)),
                   pady=(self.px(26), self.px(10)))
        right.pack_propagate(False)
        self.hero = tk.Canvas(pg, bg=BG, highlightthickness=0)
        self.hero.pack(side="left", fill="both", expand=True)
        self.hero_btn = Pill(self, self.hero, "Start New Job",
                             self.home_start, width=370, height=72,
                             kind="primary", icon="play")
        self.hero_img = None
        for nm in ("hero.png", "home.png", "background.png"):
            p = os.path.join(TOOLS_DIR, nm)
            if os.path.isfile(p):
                try:
                    self.hero_img = tk.PhotoImage(file=p)
                except Exception:
                    self.hero_img = None
                break
        self.hero.bind("<Configure>", lambda e: self.draw_hero())

        # right panel: technician + current job
        box = tk.Frame(right, bg=NAVY, highlightbackground="#2B5FA8",
                       highlightthickness=1)
        box.pack(fill="x")
        p = self.px
        top = tk.Frame(box, bg=NAVY, cursor="hand2")
        top.pack(fill="x", padx=p(22), pady=(p(18), p(12)))
        self.icon(top, "user", 30, TEXT, NAVY).pack(side="left",
                                                    padx=(0, p(12)))
        tk.Label(top, text="Technician Login", font=self.F(14, True),
                 fg=TEXT, bg=NAVY).pack(side="left")
        self.icon(top, "arrow", 18, TEXT, NAVY).pack(side="right")
        for w in top.winfo_children() + [top]:
            w.bind("<Button-1>", lambda e: self.home_login())
        tk.Frame(box, bg=BORDER, height=1).pack(fill="x", padx=p(22))
        who = tk.Frame(box, bg=NAVY)
        who.pack(fill="x", padx=p(22), pady=p(14))
        self.icon(who, "user", 28, MUTED, NAVY).pack(side="left",
                                                     padx=(0, p(12)))
        wt = tk.Frame(who, bg=NAVY)
        wt.pack(side="left")
        self.hm_who = tk.Label(wt, text="", font=self.F(12), fg=TEXT,
                               bg=NAVY)
        self.hm_who.pack(anchor="w")
        self.hm_who2 = tk.Label(wt, text="", font=self.F(9), fg=MUTED,
                                bg=NAVY)
        self.hm_who2.pack(anchor="w")
        tk.Frame(box, bg=BORDER, height=1).pack(fill="x", padx=p(22))
        jt = tk.Frame(box, bg=NAVY)
        jt.pack(fill="x", padx=p(22), pady=(p(14), p(6)))
        self.icon(jt, "doc", 28, TEXT, NAVY).pack(side="left",
                                                  padx=(0, p(12)))
        tk.Label(jt, text="Current Job", font=self.F(13, True), fg=TEXT,
                 bg=NAVY).pack(side="left")
        grid = tk.Frame(box, bg=NAVY)
        grid.pack(fill="x", padx=p(22))
        self.hm_rows = {}
        for r, k in enumerate(("Job ID", "Asset ID", "Serial Number",
                               "Location")):
            tk.Label(grid, text=k, font=self.F(11), fg=TEXT, bg=NAVY).grid(
                row=r, column=0, sticky="w", pady=p(4))
            tk.Label(grid, text=":", font=self.F(11), fg=TEXT, bg=NAVY).grid(
                row=r, column=1, padx=p(10))
            v = tk.Label(grid, text="-", font=self.F(11), fg=MUTED, bg=NAVY,
                         anchor="w", wraplength=p(170), justify="left")
            v.grid(row=r, column=2, sticky="w")
            self.hm_rows[k] = v
        self.hm_btn = Pill(self, box, "Login", self.home_login, width=320,
                           height=50, kind="outline", icon="user")
        self.hm_btn.pack(padx=p(22), pady=(p(16), p(20)), fill="x")

    def draw_hero(self):
        c = self.hero
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        c.delete("all")
        p = self.px
        # faint server racks in the background
        for k in range(7):
            x = w * (0.42 + k * 0.085)
            c.create_rectangle(x, h * .02, x + w * .05, h * .70,
                               fill=mix(BG, "#0D2448", .55), outline="")
            for j in range(10):
                y = h * (0.06 + j * 0.062)
                c.create_line(x + 4, y, x + w * .05 - 4, y,
                              fill=mix(BG, "#1C4A85", .5))
        x = p(40)
        # title size: never wider than about half of this area
        size = 50
        while size > 26 and x + self.measure("ITAD Laptop",
                                             self.F(size, True)) > w * .47:
            size -= 2
        f_big = self.F(size, True)
        if self.hero_img is not None:
            c.create_image(w * .70, h * .40, image=self.hero_img)
        else:
            s = min(w * 0.40, h * 0.66)
            # keep the icons on the right inside the picture area
            draw_laptop_art(c, min(w * .72, w - s * .84), h * .40, s)
        c.create_text(x, h * .17, text="Welcome to", anchor="w", fill="white",
                      font=self.F(size * 0.62, True))
        c.create_text(x, h * .31, text="ITAD", anchor="w", fill="white",
                      font=f_big)
        c.create_text(x + self.measure("ITAD ", f_big), h * .31,
                      text="Laptop", anchor="w", fill=BLUE2, font=f_big)
        c.create_text(x, h * .43, text="Secure Data. Sustainable Future.",
                      anchor="w", fill=TEXT, font=self.F(16))
        c.create_text(x, h * .50, anchor="nw", fill="#C9D8EE",
                      font=self.F(11), width=p(330),
                      text="This application helps you test, diagnose, wipe "
                           "and prepare laptops for their next life — "
                           "safely, securely and efficiently.")
        bx = x
        for ic, t1, t2, col in (("shield", "Data", "Security", "#4FA8FF"),
                                ("leaf", "Environmental", "Responsibility",
                                 "#34D399"),
                                ("recycle", "IT Asset", "Recycling",
                                 "#4FC3F7")):
            r = p(21)
            cy = h * .73
            c.create_oval(bx, cy - r, bx + 2 * r, cy + r, outline=col,
                          width=2, fill=mix(BG, col, .12))
            draw_icon(c, ic, bx + r, cy, r * 1.1, col, 2)
            c.create_text(bx + 2 * r + p(10), cy - p(8), text=t1, anchor="w",
                          fill=TEXT, font=self.F(9))
            c.create_text(bx + 2 * r + p(10), cy + p(8), text=t2, anchor="w",
                          fill=TEXT, font=self.F(9))
            bx += 2 * r + p(20) + max(self.measure(t1, self.F(9)),
                                      self.measure(t2, self.F(9))) + p(14)
        c.create_window(w * .55, h * .90, window=self.hero_btn.c)

    def refresh_home(self):
        name = self.tech()
        if name:
            self.hm_who.configure(text="Signed in as %s" % name, fg=OK)
            self.hm_who2.configure(text="Ready to start a job")
            self.hm_btn.configure(text="Sign out", command=self.sign_out)
            self.h_sep2.pack(side="left", fill="y", pady=self.px(10))
            self.h_user_i.pack(side="left", padx=(self.px(16), self.px(8)))
            self.h_user.configure(text=name)
            self.h_user.pack(side="left", padx=(0, self.px(18)))
        else:
            self.hm_who.configure(text="Not logged in", fg=TEXT)
            self.hm_who2.configure(text="Please login to continue")
            self.hm_btn.configure(text="Login", command=self.home_login)
            for w in (self.h_sep2, self.h_user_i, self.h_user):
                w.pack_forget()
        vals = {"Job ID": self.job() if self.job_active else "-",
                "Asset ID": (self.asset() or "-") if self.job_active else "-",
                "Serial Number": self.serial() or "-",
                "Location": self.v_loc.get() if self.job_active else "-"}
        for k, v in vals.items():
            self.hm_rows[k].configure(text=v, fg=TEXT if v != "-" else MUTED)
        self.hero_btn.configure(text="Continue Job" if self.job_active
                                else "Start New Job")

    def home_start(self):
        if self.job_active:
            self.show(self.job_page)
        elif not self.tech():
            self.show_login()
        else:
            self.show("start")

    def home_login(self):
        if self.tech():
            self.show("start")
        else:
            self.show_login()

    def nav_new_job(self):
        if self.wiping:
            self.mb.showinfo("Wipe running", "A wipe is running. Please wait "
                             "until it has finished.")
            return
        if self.job_active:
            if self.mb.askyesno("New job", "A job is open for this laptop.\n\n"
                                "Go back to it?\n\n(Choose No to start a new "
                                "job and lose the current one.)"):
                self.show(self.job_page)
            else:
                self.next_laptop()
            return
        self.home_start()

    # ---------- sign in ----------
    def build_login(self):
        tk = self.tk
        pg = self.new_page("login")
        self.v_user = tk.StringVar(value="")
        self.v_pass = tk.StringVar(value="")
        mid = tk.Frame(pg, bg=BG)
        mid.place(relx=0.5, rely=0.45, anchor="center")
        box = tk.Frame(mid, bg=NAVY, highlightbackground="#2B5FA8",
                       highlightthickness=1)
        box.pack()
        inner = tk.Frame(box, bg=NAVY)
        inner.pack(padx=self.px(44), pady=self.px(32))
        head = tk.Frame(inner, bg=NAVY)
        head.pack(anchor="w", fill="x")
        self.icon(head, "user", 36, CYAN, NAVY).pack(side="left",
                                                     padx=(0, self.px(14)))
        ht = tk.Frame(head, bg=NAVY)
        ht.pack(side="left")
        self.lbl(ht, "Technician sign in", 20, True).pack(anchor="w")
        self.lbl(ht, "Sign in with your own name and password.", 10,
                 fg=MUTED).pack(anchor="w")
        tk.Frame(inner, bg=NAVY, height=self.px(18)).pack()
        self.lbl(inner, "NAME", 9, True, fg=MUTED).pack(anchor="w")
        self.l_user = self.entry(inner, self.v_user)
        self.l_user.configure(width=32)
        self.l_user.pack(fill="x", pady=(2, self.px(12)), ipady=self.px(6))
        self.lbl(inner, "PASSWORD", 9, True, fg=MUTED).pack(anchor="w")
        self.l_pass = self.entry(inner, self.v_pass, secret=True)
        self.l_pass.pack(fill="x", pady=(2, self.px(6)), ipady=self.px(6))
        self.l_msg = self.lbl(inner, "", 10, fg=WARN, wraplength=380)
        self.l_msg.pack(anchor="w", pady=(self.px(4), self.px(12)))
        self.l_go = Pill(self, inner, "Sign in", self.do_login, width=380,
                         height=50, kind="primary")
        self.l_go.pack(fill="x")
        self.l_user.bind("<Return>", lambda e: self.l_pass.focus_set())
        self.l_pass.bind("<Return>", lambda e: self.do_login())
        left, right, msg = self.bar(pg)
        self.btn(left, "Back", lambda: self.show("home"), "ghost").pack()
        self.btn(right, "Add technician", self.add_tech_dialog,
                 "ghost").pack()
        self.l_where = msg

    def show_login(self):
        self.v_tech.set("")
        self.v_pass.set("")
        acc = load_accounts(self.out_dir)
        ready = [a for a in acc.values() if a["hash"]]
        if not ready:
            self.l_msg.configure(
                text="No technician accounts yet. Press 'Add technician' "
                     "(bottom right) to create the first one.", fg=WARN)
        else:
            self.l_msg.configure(text="", fg=WARN)
        self.l_where.configure(
            text="" if self.persistent else
            "No ITAD_DATA stick: new accounts and reports are lost at "
            "power off.", fg=WARN)
        self.show("login")
        self.root.after(200, self.l_user.focus_set)

    def do_login(self):
        left = self.locked_until - time.time()
        if left > 0:
            self.l_msg.configure(text="Too many wrong tries. Wait %d seconds."
                                 % (int(left) + 1), fg=BAD)
            return
        name = self.v_user.get().strip()
        pw = self.v_pass.get()
        if not name or not pw:
            self.l_msg.configure(text="Type your name and password.", fg=WARN)
            return
        acc = load_accounts(self.out_dir).get(name.lower())
        if acc and acc["hash"] and verify_password(pw, acc["hash"]):
            self.fails = 0
            self.v_pass.set("")
            self.signed_in(acc["name"])
            return
        self.v_pass.set("")
        self.fails += 1
        if acc and not acc["hash"]:
            text = ("%s has no password yet. Use 'Add technician' to set "
                    "one." % acc["name"])
        else:
            text = "Wrong name or password."
        if self.fails >= MAX_TRIES:
            self.fails = 0
            self.locked_until = time.time() + LOCK_SECONDS
            text += "  Locked for %d seconds." % LOCK_SECONDS
        self.l_msg.configure(text=text, fg=BAD)
        self.l_pass.focus_set()

    def signed_in(self, name):
        self.v_tech.set(name)
        os.environ["ITAD_SIGNED_IN"] = name
        self.start_msg.configure(text="")
        self.refresh_home()
        self.show("start")

    def sign_out(self):
        if self.wiping:
            self.mb.showinfo("Wipe running", "A wipe is running. Please wait.")
            return
        os.environ.pop("ITAD_SIGNED_IN", None)
        self.v_tech.set("")
        self.v_user.set("")
        self.refresh_home()
        self.show("home")

    def add_tech_dialog(self):
        tk = self.tk
        acc = load_accounts(self.out_dir)
        approvers = {k: a for k, a in acc.items() if a["hash"]}
        need_ok = bool(approvers)
        win = tk.Toplevel(self.root)
        win.title("Add technician")
        win.configure(bg=NAVY, highlightbackground="#2B5FA8",
                      highlightthickness=1)
        win.transient(self.root)
        inner = tk.Frame(win, bg=NAVY)
        inner.pack(padx=self.px(32), pady=self.px(26))
        self.lbl(inner, "Add or change a technician", 16, True).pack(
            anchor="w")
        self.lbl(inner, "The password is saved as a secure hash, never as "
                        "plain text.", 10, fg=MUTED).pack(
            anchor="w", pady=(2, self.px(14)))
        v = {k: tk.StringVar(value="") for k in
             ("name", "pw1", "pw2", "by", "bypw")}
        fields = [("NEW TECHNICIAN NAME", "name", False),
                  ("PASSWORD (at least %d characters)" % MIN_PASSWORD,
                   "pw1", True),
                  ("TYPE THE PASSWORD AGAIN", "pw2", True)]
        if need_ok:
            fields += [("APPROVED BY (an existing technician)", "by", False),
                       ("THEIR PASSWORD", "bypw", True)]
        first = None
        for label, key, secret in fields:
            self.lbl(inner, label, 9, True, fg=MUTED).pack(anchor="w")
            e = self.entry(inner, v[key], secret=secret)
            e.configure(width=34)
            e.pack(fill="x", pady=(2, self.px(10)), ipady=self.px(4))
            first = first or e
        msg = self.lbl(inner, "" if need_ok else
                       "This is the first account, so no approval is "
                       "needed.", 10, fg=MUTED, wraplength=360)
        msg.pack(anchor="w", pady=(0, self.px(10)))

        def save():
            name = v["name"].get().strip()
            p1, p2 = v["pw1"].get(), v["pw2"].get()
            if not NAME_RE.match(name):
                msg.configure(text="Name: 2-30 letters or numbers (spaces, "
                                   "dot, dash allowed).", fg=BAD)
                return
            if len(p1) < MIN_PASSWORD:
                msg.configure(text="Password is too short.", fg=BAD)
                return
            if p1 != p2:
                msg.configure(text="The two passwords are not the same.",
                              fg=BAD)
                return
            if need_ok:
                a = approvers.get(v["by"].get().strip().lower())
                if not a or not verify_password(v["bypw"].get(), a["hash"]):
                    v["bypw"].set("")
                    msg.configure(text="Approval name or password is wrong.",
                                  fg=BAD)
                    return
            try:
                path = save_account(self.out_dir, name, p1)
            except Exception as e:
                msg.configure(text="Could not save: %s" % e, fg=BAD)
                return
            for k in v:
                v[k].set("")
            win.destroy()
            note = "Saved %s in %s." % (name, path)
            if not self.persistent:
                note += ("  WARNING: no ITAD_DATA stick, this account is "
                         "lost at power off.")
            self.v_user.set(name)
            if self.pages["settings"].winfo_ismapped():
                self.refresh_settings()
            else:
                self.show_login()
                self.v_user.set(name)
            self.l_msg.configure(text=note + "  Now sign in.",
                                 fg=OK if self.persistent else WARN)
            self.l_pass.focus_set()

        bf = tk.Frame(inner, bg=NAVY)
        bf.pack(fill="x")
        self.btn(bf, "Save", save).pack(side="left")
        self.btn(bf, "Cancel", win.destroy, "ghost").pack(
            side="left", padx=self.px(8))
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry("+%d+%d" % (max(0, (sw - w) // 2), max(0, (sh - h) // 2)))
        try:
            win.grab_set()
        except Exception:
            pass
        first.focus_set()

    # ---------- job details ----------
    def build_start(self):
        tk = self.tk
        pg = self.new_page("start")
        self.page_title(pg, "New job", "Check the details, then start the "
                                       "checks.")
        form = tk.Frame(pg, bg=BG)
        form.pack(fill="x", padx=self.px(23), pady=(0, self.px(6)))
        for c in range(4):
            form.grid_columnconfigure(c, weight=1, uniform="f")
        fields = [("Technician (signed in)", "entry", self.v_tech, None,
                   True),
                  ("Job ID", "entry", self.v_job, None, False),
                  ("Asset ID", "entry", self.v_asset, None,
                   bool(self.s["serial"])),
                  ("Location", "combo", self.v_loc, LOCATIONS, False)]
        for c, (name, kind, var, vals, ro) in enumerate(fields):
            cell = tk.Frame(form, bg=NAVY, highlightbackground=BORDER,
                            highlightthickness=1)
            cell.grid(row=0, column=c, sticky="nsew", padx=self.px(5))
            tk.Label(cell, text=name.upper(), font=self.F(9, True), fg=MUTED,
                     bg=NAVY, anchor="w").pack(fill="x", padx=self.px(14),
                                               pady=(self.px(10), 2))
            if kind == "combo":
                w = self.ttk.Combobox(cell, textvariable=var, values=vals,
                                      state="readonly" if ro else "normal",
                                      font=self.F(12))
            else:
                w = self.entry(cell, var, readonly=ro)
            w.pack(fill="x", padx=self.px(14), pady=(0, self.px(12)),
                   ipady=self.px(3))
        hint = ("Asset ID is filled from the serial number."
                if self.s["serial"] else
                "No usable serial number found: type the Asset ID.")
        self.lbl(pg, hint, 10, fg=MUTED, bg=BG).pack(
            fill="x", padx=self.px(30), pady=(0, self.px(6)))
        body = tk.Frame(pg, bg=BG)
        body.pack(fill="both", expand=True, padx=self.px(23), pady=4)
        dev_outer, dev = self.card(body, "Detected laptop", "monitor")
        dev_outer.pack(fill="both", expand=True, padx=self.px(5))
        i = self.ident
        stor = ["%s (%s) %s GB" % (d.get("model") or "Drive",
                                   d.get("drive_type", "?"), d.get("size_gb"))
                for d in self.disks] or ["none found"]
        rows = [("Type", i.get("device_type") or "-"),
                ("Make", i.get("manufacturer") or "-"),
                ("Model", i.get("model") or "-"),
                ("Serial", self.s["serial"] or "not found"),
                ("CPU", i.get("cpu") or "-"),
                ("RAM", ram_text(self.ram)),
                ("Screen", screen_text(self.screen)),
                ("Storage", "\n".join(stor)),
                ("BIOS / boot", "%s %s  %s" % (i.get("bios_vendor", ""),
                                               i.get("bios_version", ""),
                                               i.get("boot_mode", "")))]
        half = (len(rows) + 1) // 2
        cols = [tk.Frame(dev, bg=NAVY), tk.Frame(dev, bg=NAVY)]
        for f in cols:
            f.pack(side="left", fill="both", expand=True, anchor="n")
        for n, (k, v) in enumerate(rows):
            f, r = (cols[0], n) if n < half else (cols[1], n - half)
            self.lbl(f, k, 10, fg=MUTED).grid(row=r, column=0, sticky="nw",
                                               padx=(0, self.px(14)),
                                               pady=self.px(4))
            self.lbl(f, v, 11, True, wraplength=330).grid(
                row=r, column=1, sticky="nw", pady=self.px(4),
                padx=(0, self.px(20)))
        left, right, mid = self.bar(pg)
        self.btn(left, "Sign out", self.sign_out, "ghost").pack(side="left")
        self.start_msg = mid
        self.test_only_btn = self.btn(right, "Test components only",
                                      self.start_test_only, "outline")
        self.start_pill = Pill(self, right, "Start checks", self.start_checks,
                               width=230, height=48, icon="play")
        self.start_pill.pack(side="right")
        self.refresh_test_mode()

    def refresh_test_mode(self):
        """Show or hide everything that belongs to Test mode."""
        if not hasattr(self, "test_only_btn"):
            return
        if self.test_mode:
            self.test_only_btn.pack(side="left", padx=(0, self.px(12)))
            self.h_test.pack(side="right", padx=4)
        else:
            self.test_only_btn.pack_forget()
            self.h_test.pack_forget()

    def job_ready(self):
        if not self.tech():
            self.show_login()
            return False
        if not self.job() or not self.asset():
            self.start_msg.configure(text="Job ID and Asset ID are needed.",
                                     fg=WARN)
            return False
        self.start_msg.configure(text="")
        return True

    def start_test_only(self):
        """Test mode: no check, no wipe, straight to the component tests."""
        if not self.test_mode or not self.job_ready():
            return
        self.job_active = True
        self.s["check"] = None
        self.set_stage("check", "warn", "Test mode - not checked")
        self.test_mode_skip_wipe()

    def test_mode_skip_wipe(self):
        """Test mode: never wipe, run the component tests."""
        self.s["wipe_state"] = "SKIPPED (TEST MODE)"
        self.show("wipe")
        self.set_stage("wipe", "warn", "Test mode - NOT wiped")
        self.w_title.configure(text="Test mode: wipe skipped (nothing erased)",
                               fg=WARN)
        self.w_targets.configure(text="")
        self.hide_all_wipe_parts()
        self.start_auto_tests()

    def toggle_test_mode(self):
        if not self.test_mode:
            if self.job_active:
                self.mb.showinfo("Test mode", "Finish this job (or press "
                                 "Next laptop) before turning on Test mode.")
                return
            if not self.mb.askyesno(
                    "Turn on Test mode?",
                    "Test mode lets you skip the Absolute / enrolment check "
                    "and the wipe, to test components on your own laptops."
                    "\n\nNothing will be erased. Reports will say TEST MODE "
                    "and will never show PASS.\n\nDo NOT use it for customer "
                    "laptops. Turn it on?"):
                return
            self.test_mode = True
            os.environ["ITAD_TEST_MODE"] = "1"
        else:
            self.test_mode = False
            os.environ.pop("ITAD_TEST_MODE", None)
        self.refresh_test_mode()
        self.refresh_settings()

    def start_checks(self):
        if not self.tech():
            self.show_login()
            return
        if not self.job() or not self.asset():
            self.start_msg.configure(text="Job ID and Asset ID are needed.",
                                     fg=WARN)
            return
        self.start_msg.configure(text="")
        self.job_active = True
        self.run_check()

    # ---------- checks ----------
    def build_checks(self):
        tk = self.tk
        pg = self.new_page("checks")
        self.c_banner = tk.Label(pg, text="Checking...", font=self.F(20, True),
                                 fg=TEXT, bg=BORDER, pady=self.px(14))
        self.c_banner.pack(fill="x", padx=self.px(28),
                           pady=(self.px(18), self.px(10)))
        row = tk.Frame(pg, bg=BG)
        row.pack(fill="both", expand=True, padx=self.px(23))
        a_outer, a = self.card(row, "Absolute lock", "lock")
        a_outer.pack(side="left", fill="both", padx=self.px(5))
        self.c_abs = self.lbl(a, "-", 22, True, fg=MUTED)
        self.c_abs.pack(anchor="w")
        self.c_abs_note = self.lbl(a, "", 10, fg=MUTED, wraplength=280)
        self.c_abs_note.pack(anchor="w", pady=(self.px(8), 0))
        e_outer, e = self.card(row, "Enrolment (Intune / Autopilot / SCCM)",
                               "shield")
        e_outer.pack(side="left", fill="both", expand=True, padx=self.px(5))
        self.c_enr = self.lbl(e, "-", 22, True, fg=MUTED)
        self.c_enr.pack(anchor="w")
        self.c_enr_note = self.lbl(e, "", 11, wraplength=560)
        self.c_enr_note.pack(anchor="w", pady=(self.px(8), 0))
        left, right, mid = self.bar(pg)
        self.btn(left, "Home", lambda: self.show("home"), "ghost").pack()
        self.c_status = mid
        self.c_again = self.btn(right, "Run check again", self.run_check,
                                "ghost")
        self.c_again.pack(side="left", padx=(0, self.px(10)))
        self.c_next = self.btn(right, "Continue to wipe", self.enter_wipe,
                               width=270)
        self.c_next.pack(side="left")

    def test_mode_next(self):
        """Set the 'next' button on the check screen for the current mode."""
        if self.test_mode:
            self.c_next.configure(text="Skip wipe - test components",
                                  command=self.test_mode_skip_wipe,
                                  state="normal")
        else:
            self.c_next.configure(text="Continue to wipe",
                                  command=self.enter_wipe)

    def banner(self, text, kind):
        bg, fg = {"ok": (GREEN_BAN, NAVY), "bad": (RED_BAN, "white"),
                  "warn": (WARN, NAVY), "wait": (BORDER, TEXT)}[kind]
        self.c_banner.configure(text=text, bg=bg, fg=fg)

    def run_check(self):
        self.show("checks")
        self.banner("Checking...", "wait")
        for w, t in ((self.c_abs, "-"), (self.c_enr, "-")):
            w.configure(text=t, fg=MUTED)
        self.c_abs_note.configure(text="")
        self.c_enr_note.configure(text="")
        self.c_again.configure(state="disabled")
        self.c_next.configure(state="disabled")
        if not self.test_mode:
            self.c_next.configure(text="Continue to wipe",
                                  command=self.enter_wipe)
        self.set_stage("check", "run", "Checking Absolute and enrolment...")
        if os.path.exists(self.p_check):
            try:
                os.remove(self.p_check)
            except OSError:
                pass
        cmd = [PY, CHECK] + self.common_args() + ["--json", self.p_check]
        self.run_stage(cmd, lambda ln: self.c_status.configure(
            text=ln[:90], fg=MUTED), self.check_done)

    def check_done(self, rc):
        data = load_json(self.p_check)
        self.s["check"] = data
        self.c_status.configure(text="")
        self.c_again.configure(state="normal")
        if not data:
            self.test_mode_next()
            self.banner("CHECK DID NOT RUN", "bad")
            self.c_enr_note.configure(text="\n".join(self.log[-6:]) or
                                      "laptop_check.py gave no result.")
            self.set_stage("check", "bad", "Check did not run")
            return
        self.test_mode_next()
        result = data.get("result", "?")
        ar = data.get("absolute_result", "?")
        er = data.get("autopilot_result", "?")
        colors = {"CLEAR": OK, "CLEAN": OK, "BLOCKED": BAD}
        self.c_abs.configure(text=ar, fg=colors.get(ar, WARN))
        enr_text = {"CLEAN": "CLEAN", "BLOCKED": "ACTIVE ENROLMENT",
                    "UNKNOWN": "NOT CHECKED"}.get(er, "PAST ENROLMENT")
        self.c_enr.configure(text=enr_text, fg=colors.get(er, WARN))
        reasons = data.get("block_reasons") or (
            list(data.get("absolute_signs") or [])
            + [e.get("description", "") for e in
               (data.get("autopilot_analysis") or {}).get("active", [])])
        if data.get("absolute_signs"):
            self.c_abs_note.configure(text="\n".join(data["absolute_signs"][:4]))
        if result == "BLOCKED":
            self.banner("BLOCKED  -  DO NOT WIPE", "bad")
            self.set_stage("check", "bad", "Blocked - do not wipe")
            self.c_enr_note.configure(
                text="Why it is blocked:\n- " + "\n- ".join(reasons[:6] or ["see report"])
                + "\n\nAsk the customer to remove this laptop from their "
                  "portal, then run the check again.")
        elif result == "CLEAR":
            self.banner("CLEAR  -  ready to wipe", "ok")
            self.set_stage("check", "ok", "Clear - ready to wipe")
            self.c_enr_note.configure(
                text="No on-disk sign of Autopilot or MDM.\n\nStill check the "
                     "serial number in the customer's Intune / ABM portal "
                     "before resale.")
            self.c_next.configure(state="normal")
        else:
            self.banner("CANNOT CHECK  -  manager decision needed", "warn")
            self.set_stage("check", "warn", "Cannot check - ask manager")
            notes = (data.get("notes") or []) + (data.get("warnings") or [])
            self.c_enr_note.configure(
                text="\n".join(notes[:6] or ["Some drives could not be read."]))

    # ---------- wipe and tests ----------
    def build_wipe(self):
        tk = self.tk
        pg = self.new_page("wipe")
        row = tk.Frame(pg, bg=BG)
        row.pack(fill="both", expand=True, padx=self.px(23),
                 pady=(self.px(18), self.px(6)))
        w_outer, w = self.card(row, "Wipe", "bin")
        w_outer.pack(side="left", fill="both", padx=self.px(5))
        self.w_title = self.lbl(w, "Checking drives...", 15, True)
        self.w_title.pack(anchor="w")
        self.w_targets = self.lbl(w, "", 10, fg=MUTED, wraplength=400)
        self.w_targets.pack(anchor="w", pady=(self.px(6), self.px(10)))
        self.w_progress = tk.Frame(w, bg=NAVY)
        self.w_pbar = self.ttk.Progressbar(
            self.w_progress, style="Seal.Horizontal.TProgressbar",
            mode="determinate", maximum=100, length=self.px(400))
        self.w_pbar.pack(anchor="w")
        self.w_pct = self.lbl(self.w_progress, "0%", 10, fg=MUTED)
        self.w_pct.pack(anchor="w", pady=(4, 0))
        self.w_confirm = tk.Frame(w, bg=NAVY)
        self.w_ask = self.lbl(self.w_confirm, "", 10, fg=WARN, wraplength=400)
        self.w_ask.pack(anchor="w")
        self.v_confirm = tk.StringVar(value="")
        self.w_entry = self.entry(self.w_confirm, self.v_confirm)
        self.w_entry.pack(fill="x", pady=self.px(8), ipady=self.px(4))
        self.v_confirm.trace_add("write", lambda *a: self.update_confirm())
        btns = tk.Frame(self.w_confirm, bg=NAVY)
        btns.pack(anchor="w")
        self.w_go = self.btn(btns, "Wipe now", self.do_wipe, "danger",
                             width=190)
        self.w_go.pack(side="left", padx=(0, self.px(8)))
        self.w_skip = self.btn(btns, "Skip wipe", self.skip_wipe, "ghost")
        self.w_skip.pack(side="left")
        t_outer, t = self.card(row, "Component tests", "chip")
        t_outer.pack(side="left", fill="both", expand=True, padx=self.px(5))
        self.t_status = self.lbl(t, "Waiting for the wipe step...", 10,
                                 fg=MUTED)
        self.t_status.grid(row=0, column=0, columnspan=4, sticky="w",
                           pady=(0, self.px(6)))
        half = (len(COMPONENTS) + 1) // 2
        for n, comp in enumerate(COMPONENTS):
            r, c = (n + 1, 0) if n < half else (n - half + 1, 2)
            self.lbl(t, comp, 11).grid(row=r, column=c, sticky="w",
                                       padx=(0, self.px(18)), pady=self.px(3))
            lab = self.lbl(t, "waiting", 11, True, fg=MUTED, cursor="hand2")
            lab.grid(row=r, column=c + 1, sticky="w", pady=self.px(3),
                     padx=(0, self.px(30)))
            lab.bind("<Button-1>", lambda e, cp=comp: self.change_result(cp))
            self.test_rows[comp] = lab
        self.lbl(t, "Tap a result to change it (PASS / FAIL / Not fitted).",
                 9, fg=MUTED).grid(row=half + 1, column=0, columnspan=4,
                                   sticky="w", pady=(self.px(6), 0))
        mrow = tk.Frame(t, bg=NAVY)
        mrow.grid(row=half + 2, column=0, columnspan=4, sticky="w",
                  pady=(self.px(10), 0))
        tests = (("Screen", self.manual_screen),
                 ("Keyboard", self.manual_keyboard),
                 ("Touchpad", self.manual_touchpad),
                 ("Touchscreen", self.manual_touchscreen),
                 ("Speakers", self.manual_speakers),
                 ("Microphone", self.manual_microphone),
                 ("Webcam", self.manual_webcam),
                 ("USB", self.manual_usb))
        for n, (comp, fn) in enumerate(tests):
            label = "USB ports test" if comp == "USB" else comp + " test"
            b = self.btn(mrow, label, fn, "ghost", width=190)
            b.configure(state="disabled")
            b.grid(row=n // 4, column=n % 4, sticky="w",
                   padx=(0, self.px(8)), pady=(0, self.px(8)))
            self.manual_btns[comp] = b
        left, right, mid = self.bar(pg)
        self.w_power = self.btn(left, "Home", lambda: self.show("home"),
                                "ghost")
        self.w_power.pack()
        self.w_status = mid
        self.w_next = self.btn(right, "Continue to observations",
                               self.enter_observe)
        self.w_next.configure(state="disabled")
        self.w_next.pack()

    def set_row(self, comp, status, detail=""):
        lab = self.test_rows.get(comp)
        if not lab:
            return
        text = {"RUN": "running...", "WAIT": "waiting", "DONE": "done",
                "PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL",
                "SKIP": "skipped", NOT_FITTED: "not fitted"}.get(status,
                                                                 status)
        if comp in self.s.get("override", {}):
            text += " (set)"
        if detail:
            text += "   " + detail
        color = {"RUN": CYAN, "PASS": OK, "WARN": WARN,
                 "FAIL": BAD}.get(status, MUTED)
        lab.configure(text=text, fg=color)

    def serial(self):
        chk = self.s.get("check") or {}
        ser = (chk.get("identity") or {}).get("serial") or self.s["serial"]
        return ser if valid_serial(ser) else ""

    def hide_all_wipe_parts(self):
        self.w_progress.pack_forget()
        self.w_confirm.pack_forget()

    def enter_wipe(self):
        if self.test_mode:
            self.test_mode_skip_wipe()
            return
        self.show("wipe")
        self.set_stage("wipe", "run", "Checking drives...")
        self.w_title.configure(text="Checking drives...", fg=TEXT)
        self.w_targets.configure(text="")
        self.hide_all_wipe_parts()
        cmd = ([PY, WIPE] + self.common_args() +
               ["--json", self.p_wipe_dry, "--check-report", self.p_check,
                "--method", "auto"])
        self.run_stage(cmd, lambda ln: self.w_status.configure(
            text=ln[:90], fg=MUTED), self.wipe_preview_done)

    def wipe_preview_done(self, rc):
        data = load_json(self.p_wipe_dry) or {}
        self.s["wipe_dry"] = data
        self.w_status.configure(text="")
        devs = data.get("devices") or []
        self.w_confirm.pack(anchor="w", fill="x")
        self.w_entry.pack(fill="x", pady=self.px(8), ipady=self.px(4),
                          before=self.w_go.c.master)
        self.w_go.pack(side="left", padx=(0, self.px(8)),
                       before=self.w_skip.c)
        if not devs:
            why = list(data.get("refusals") or [])
            if data.get("check_report"):
                why.append(str(data["check_report"]))
            self.w_title.configure(text="The wipe is not possible", fg=BAD)
            self.w_targets.configure(
                text="\n".join(why[:5] or ["No internal drive can be wiped."]))
            self.w_ask.configure(text="You can skip the wipe and run the "
                                      "tests only. The report will say "
                                      "the laptop was NOT wiped.")
            self.w_entry.pack_forget()
            self.w_go.pack_forget()
            self.set_stage("wipe", "bad", "Wipe not possible")
            return
        lines = ["%s   %s   %s GB   SN %s" % (
            d.get("path"), d.get("model") or "?", d.get("size_gb"),
            d.get("serial") or "-") for d in devs]
        self.w_targets.configure(text="These drives will be ERASED:\n" +
                                 "\n".join(lines))
        self.set_stage("wipe", "warn", "Ready - confirm the wipe")
        if self.dry:
            self.w_title.configure(text="Dry run: nothing will be erased",
                                   fg=WARN)
            self.w_ask.configure(text="Dry run mode is on.")
            self.w_entry.pack_forget()
            self.w_go.configure(text="Continue (dry run)", state="normal")
            return
        self.w_title.configure(text="Ready to wipe", fg=WARN)
        self.need = (self.serial()[-4:].upper() if self.serial() else "WIPE")
        self.w_ask.configure(
            text=("Serial %s.  Type the last 4 characters of the serial "
                  "number to allow the wipe:" % self.serial())
            if self.serial() else
            "Serial unknown.  Type WIPE to allow the wipe:")
        self.v_confirm.set("")
        self.w_go.configure(text="Wipe now", state="disabled")

    def update_confirm(self):
        if self.dry:
            return
        ok = self.v_confirm.get().strip().upper() == getattr(self, "need", "?")
        self.w_go.configure(state="normal" if ok else "disabled")

    def skip_wipe(self):
        if not self.mb.askyesno("Skip wipe", "Skip the wipe?\n\nThe report "
                                "will say this laptop was NOT wiped."):
            return
        self.s["wipe_state"] = "SKIPPED"
        self.set_stage("wipe", "warn", "Skipped - NOT wiped")
        self.w_title.configure(text="Wipe skipped", fg=WARN)
        self.hide_all_wipe_parts()
        self.start_auto_tests()

    def do_wipe(self):
        if self.test_mode:
            self.test_mode_skip_wipe()
            return
        if self.dry:
            self.s["wipe_state"] = "DRY RUN"
            self.s["wipe"] = self.s.get("wipe_dry")
            self.set_stage("wipe", "warn", "Dry run - nothing erased")
            self.w_title.configure(text="Dry run done (nothing erased)", fg=WARN)
            self.hide_all_wipe_parts()
            self.start_auto_tests()
            return
        self.wiping = True
        self.hide_all_wipe_parts()
        self.w_progress.pack(anchor="w")
        self.w_pbar.configure(value=0)
        self.w_pct.configure(text="0%")
        self.w_title.configure(text="Wiping... do not power off", fg=WARN)
        self.w_power.configure(state="disabled")
        self.set_stage("wipe", "run", "Wiping... 0%")
        cmd = ([PY, WIPE] + self.common_args() +
               ["--json", self.p_wipe, "--check-report", self.p_check,
                "--method", "auto", "--commit", "--yes-i-mean-it"])
        self.run_stage(cmd, self.wipe_line, self.wipe_done)

    def wipe_line(self, ln):
        m = re.findall(r"(\d{1,3})%", ln)
        if m:
            pct = min(100, int(m[-1]))
            self.w_pbar.configure(value=pct)
            self.w_pct.configure(text="%d%%   %s" % (pct, ln[-50:]))
            self.set_stage("wipe", "run", "Wiping... %d%%" % pct)
        self.w_status.configure(text=ln[:90], fg=MUTED)

    def wipe_done(self, rc):
        self.wiping = False
        self.w_power.configure(state="normal")
        data = load_json(self.p_wipe) or {}
        self.s["wipe"] = data
        self.w_status.configure(text="")
        if data.get("overall") == "PASS":
            self.s["wipe_state"] = "WIPED"
            self.w_title.configure(text="Wiped and verified", fg=OK)
            self.set_stage("wipe", "ok", "Wiped and verified")
        else:
            self.s["wipe_state"] = "FAILED"
            self.w_title.configure(text="WIPE FAILED - do not ship", fg=BAD)
            self.set_stage("wipe", "bad", "Wipe failed - do not ship")
        self.start_auto_tests()

    def start_auto_tests(self):
        self.set_stage("test", "run", "Running automatic tests...")
        self.t_status.configure(text="Running automatic tests...", fg=CYAN)
        for comp in COMPONENTS:
            self.set_row(comp, "WAIT")
        self.w_status.configure(text="")
        cpu, ram = ("5", "0") if self.quick else ("30", "2")
        cmd = ([PY, HW_TEST] + self.common_args() +
               ["--json", self.p_test, "--test", AUTO_TESTS,
                "--no-interactive", "--cpu-load", cpu, "--ram-minutes", ram])
        self.cur_test = None
        self.run_stage(cmd, self.test_line, self.tests_done)

    def test_line(self, ln):
        m = re.search(r"\[(\d+)/(\d+)\]\s+([A-Z]+)", ln)
        if m:
            if self.cur_test:
                self.set_row(self.cur_test, "DONE")
            comp = BANNER_NAMES.get(m.group(3))
            if comp:
                self.cur_test = comp
                self.set_row(comp, "RUN")
                self.t_status.configure(
                    text="Running automatic tests... %s of %s" % (
                        m.group(1), m.group(2)), fg=CYAN)
                self.set_stage("test", "run", "Testing %s (%s of %s)" % (
                    comp, m.group(1), m.group(2)))
        else:
            self.w_status.configure(text=ln[:90], fg=MUTED)

    def tests_done(self, rc):
        data = load_json(self.p_test) or {}
        self.s["auto"] = data.get("results") or []
        got = {}
        for r in self.s["auto"]:
            got[r.get("component")] = r
            self.set_row(r.get("component"), r.get("status", "WARN"),
                         self.result_detail(r))
        for comp in COMPONENTS:
            if comp not in MANUAL and comp not in got:
                self.set_row(comp, "WARN")
        self.w_status.configure(text="")
        # No touchscreen on this laptop: nothing to test, mark it N/A.
        if not find_touchscreen():
            self.finish_manual("Touchscreen", NOT_FITTED,
                               ["No touchscreen on this laptop"])
        todo = [c for c in MANUAL if c not in self.s["manual"]]
        self.t_status.configure(
            text="Automatic tests finished. Now run the %d on-screen tests."
                 % len(todo), fg=TEXT)
        self.set_stage("test", "warn", "Now run the on-screen tests")
        for comp, b in self.manual_btns.items():
            b.configure(state="disabled" if self.s["manual"].get(comp, {})
                        .get("status") == NOT_FITTED else "normal")
        self.w_next.configure(state="normal")

    def result_detail(self, r):
        """Short extra text next to a result (battery health %)."""
        if r.get("component") == "Battery":
            for b in (r.get("details") or {}).get("batteries") or []:
                if b.get("health_pct") is not None:
                    txt = "%.0f%% health" % b["health_pct"]
                    if b.get("runtime_h"):
                        txt += ", ~%.1f h" % b["runtime_h"]
                    return txt
            if r.get("status") == "SKIP":
                return "no battery"
        return ""

    def change_result(self, comp):
        """Let the technician set PASS / FAIL / Not fitted for a result."""
        if self.wiping:
            return
        tk = self.tk
        cur = None
        for t in self.collect_tests():
            if t.get("component") == comp:
                cur = t
        win = tk.Toplevel(self.root)
        win.title("Change result")
        win.configure(bg=NAVY)
        win.transient(self.root)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = self.px(560), self.px(330)
        win.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
        self.lbl(win, comp, 16, True).pack(pady=(self.px(16), 2))
        now_txt = "Now: %s" % ((cur or {}).get("status") or "not tested")
        notes = "; ".join((cur or {}).get("notes") or [])
        if notes:
            now_txt += "\n" + notes[:160]
        self.lbl(win, now_txt, 10, fg=MUTED, wraplength=w - 40).pack(
            pady=(0, self.px(10)))
        self.lbl(win, "Note (optional):", 10, fg=MUTED).pack(anchor="w",
                                                             padx=20)
        v = tk.StringVar(value="")
        e = self.entry(win, v)
        e.pack(fill="x", padx=20, pady=(2, self.px(12)), ipady=3)
        bf = tk.Frame(win, bg=NAVY)
        bf.pack()

        def choose(status):
            note = v.get().strip()
            msg = "Set to %s by %s" % (status if status != NOT_FITTED
                                       else "Not fitted", self.tech() or
                                       "technician")
            if note:
                msg += ": " + note
            win.destroy()
            if comp in MANUAL:
                self.s.setdefault("override", {}).pop(comp, None)
                self.finish_manual(comp, status, [msg])
            else:
                self.s.setdefault("override", {})[comp] = {
                    "status": status, "note": msg}
                self.set_row(comp, status,
                             self.result_detail(cur or {}))
        self.btn(bf, "PASS", lambda: choose("PASS")).pack(side="left",
                                                          padx=4)
        self.btn(bf, "FAIL", lambda: choose("FAIL"), "danger").pack(
            side="left", padx=4)
        self.btn(bf, "Not fitted", lambda: choose(NOT_FITTED),
                 "outline").pack(side="left", padx=4)
        self.btn(bf, "Cancel", win.destroy, "ghost").pack(side="left",
                                                          padx=4)
        win.grab_set()
        e.focus_set()

    def test_summary(self):
        # parts the laptop does not have (N/A) are left out of the count
        tests = [t for t in self.collect_tests()
                 if t.get("status") != NOT_FITTED]
        good = sum(1 for t in tests if t.get("status") == "PASS")
        bad = sum(1 for t in tests if t.get("status") == "FAIL")
        state = "bad" if bad else ("ok" if good == len(tests) else "warn")
        return state, "%d of %d passed" % (good, len(tests))

    # ---------- on-screen tests ----------
    def finish_manual(self, comp, status, notes):
        self.s["manual"][comp] = {"component": comp, "status": status,
                                  "notes": notes, "details": {}}
        self.set_row(comp, status)

    def manual_screen(self):
        tk = self.tk
        win = tk.Toplevel(self.root)
        try:
            win.attributes("-fullscreen", True)
        except Exception:
            pass
        st = {"i": 0, "fail": [], "seen": []}
        hint = tk.Label(win, text="", font=self.F(11))
        hint.place(relx=0.5, rely=0.98, anchor="s")

        def paint_now():
            name, col, fg = SCREEN_COLORS[st["i"]]
            win.configure(bg=col)
            hint.configure(text="%s    Enter / click = next    F = failure    "
                                "S = stop" % name, bg=col, fg=fg)

        def close():
            win.destroy()
            if st["fail"]:
                self.finish_manual("Screen", "FAIL",
                                   ["Problem on: " + ", ".join(st["fail"])])
            elif st["seen"]:
                self.finish_manual("Screen", "PASS",
                                   ["%d colour(s) shown" % len(st["seen"])])
            else:
                self.finish_manual("Screen", "WARN", ["Not tested"])

        def nxt(fail=False):
            name = SCREEN_COLORS[st["i"]][0]
            st["seen"].append(name)
            if fail:
                st["fail"].append(name)
            st["i"] += 1
            if st["i"] >= len(SCREEN_COLORS):
                close()
            else:
                paint_now()

        win.bind("<Return>", lambda e: nxt())
        win.bind("<space>", lambda e: nxt())
        win.bind("<Button-1>", lambda e: nxt())
        win.bind("f", lambda e: nxt(True))
        win.bind("F", lambda e: nxt(True))
        win.bind("s", lambda e: close())
        win.bind("S", lambda e: close())
        paint_now()
        win.focus_force()

    def manual_keyboard(self):
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.title("Keyboard test")
        win.configure(bg=NAVY)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(self.px(1320), sw), min(self.px(480), sh)
        win.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2),
                                      max(0, (sh - h) // 2)))
        tk.Label(win, text="Press every key. Keys turn green when they work.",
                 font=self.F(12, True), fg=TEXT, bg=NAVY).pack(
            pady=(self.px(14), self.px(8)))
        cells, seen = {}, set()
        pads = {}
        state = {"numpad": False}
        board = tk.Frame(win, bg=NAVY)
        board.pack()
        main = tk.Frame(board, bg=NAVY)
        main.pack(side="left", anchor="n")
        for row in KB_ROWS:
            rf = tk.Frame(main, bg=NAVY)
            rf.pack(pady=3)
            for label, kid in row:
                c = tk.Label(rf, text=label, font=self.F(10, True), fg=TEXT,
                             bg=BORDER, width=max(3, len(label) + 1), pady=6)
                c.pack(side="left", padx=2)
                cells[kid] = c
        # number pad on the right (greyed until a number-pad key is used)
        npf = tk.Frame(board, bg=NAVY)
        npf.pack(side="left", anchor="n", padx=(self.px(26), 0))
        np_title = tk.Label(npf, text="Number pad\n(only if fitted)",
                            font=self.F(9, True), fg=MUTED, bg=NAVY)
        np_title.pack(pady=(0, 4))
        for row in NUMPAD_ROWS:
            rf = tk.Frame(npf, bg=NAVY)
            rf.pack(anchor="w", pady=3)
            for label, kid in row:
                c = tk.Label(rf, text=label, font=self.F(10, True), fg=MUTED,
                             bg=CARD2, width=4, pady=6)
                c.pack(side="left", padx=2)
                pads[kid] = c
        counter = tk.Label(win, text="", font=self.F(10), fg=MUTED, bg=NAVY)
        counter.pack(pady=(self.px(10), 4))

        def needed():
            n = dict(cells)
            if state["numpad"]:
                n.update(pads)
            return n

        def update():
            need = needed()
            got = len([k for k in seen if k in need])
            counter.configure(text="%d of %d keys pressed%s" % (
                got, len(need), "  (number pad included)"
                if state["numpad"] else ""))

        def on_key(ev):
            kid = NUMPAD_KEYMAP.get(ev.keysym) or KEYMAP.get(ev.keysym)
            if not kid and len(ev.keysym) == 1:
                kid = ev.keysym.upper()
            if kid in pads and not state["numpad"]:
                # this laptop has a number pad: now it must be tested too
                state["numpad"] = True
                np_title.configure(text="Number pad\n(test every key)",
                                   fg=TEXT)
                for c in pads.values():
                    c.configure(bg=BORDER, fg=TEXT)
            box = cells.get(kid) or pads.get(kid)
            if box is not None and kid not in seen:
                seen.add(kid)
                box.configure(bg=OK, fg=NAVY)
                update()
            return "break"

        def done():
            win.destroy()
            need = needed()
            cells_all = dict(cells)
            cells.clear()
            cells.update(need)
            if all(k in seen for k in need):
                self.finish_manual("Keyboard", "PASS",
                                   ["All %d keys pressed%s" % (
                                       len(cells), " (with number pad)"
                                       if state["numpad"] else "")])
            else:
                # Not every key was pressed, so the keyboard is not proven.
                missing = sorted(set(cells) - seen)
                self.finish_manual("Keyboard", "WARN",
                                   ["Partial test: %d of %d keys pressed. "
                                    "Not pressed: %s" % (
                                        len(seen), len(cells),
                                        ", ".join(missing[:12])
                                        + (" ..." if len(missing) > 12
                                           else ""))])

        def failed():
            win.destroy()
            missing = sorted(set(needed()) - seen)
            self.finish_manual("Keyboard", "FAIL",
                               ["Key not working. Not pressed: "
                                + ", ".join(missing[:12])])

        bf = tk.Frame(win, bg=NAVY)
        bf.pack(pady=self.px(8))
        self.btn(bf, "Done", done).pack(side="left", padx=6)
        self.btn(bf, "A key does not work", failed, "danger").pack(side="left")
        update()
        win.bind("<KeyPress>", on_key)
        win.focus_force()

    def manual_speakers(self):
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.title("Speakers test")
        win.configure(bg=NAVY)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(self.px(760), sw), min(self.px(470), sh)
        win.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
        self.lbl(win, "Speakers test", 16, True).pack(pady=(self.px(12), 2))
        self.lbl(win, "Press LEFT, then RIGHT. A voice says \"Front left\" "
                      "or \"Front right\".\nListen: is it clear, from the "
                      "right side, with no crackling?", 10, fg=MUTED,
                 justify="center").pack()
        dev = speaker_device()
        info = self.lbl(win, "Output: %s" % (dev[1] if dev else
                                            "default sound card"),
                        10, fg=MUTED)
        info.pack(pady=(self.px(6), 0))
        now_l = self.lbl(win, "", 14, True, fg=CYAN)
        now_l.pack(pady=self.px(10))
        st = {"busy": False, "played": set(), "proc": None}
        unmute_audio()

        def play(which):
            if st["busy"]:
                return
            if not shutil.which("speaker-test"):
                now_l.configure(text="speaker-test is not installed",
                                fg=WARN)
                return
            st["busy"] = True
            now_l.configure(text={"1": "Playing LEFT...",
                                  "2": "Playing RIGHT...",
                                  "both": "Playing BOTH..."}[which], fg=CYAN)

            def work():
                cmd = ["speaker-test", "-c", "2", "-l", "1"]
                if dev:
                    cmd += ["-D", dev[0]]
                if which == "both":
                    cmd += ["-t", "sine", "-f", "440"]
                else:
                    cmd += ["-t", "wav", "-s", which]
                try:
                    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                    st["proc"] = p
                    p.wait(timeout=20)
                except Exception:
                    pass
                st["played"].add(which)
                st["busy"] = False
                self.q.put(lambda: now_l.configure(
                    text="Done - did it sound right?", fg=TEXT))
            threading.Thread(target=work, daemon=True).start()

        pf = tk.Frame(win, bg=NAVY)
        pf.pack(pady=self.px(6))
        self.btn(pf, "LEFT", lambda: play("1"), "outline",
                 width=150).pack(side="left", padx=6)
        self.btn(pf, "RIGHT", lambda: play("2"), "outline",
                 width=150).pack(side="left", padx=6)
        self.btn(pf, "BOTH (tone)", lambda: play("both"), "outline",
                 width=170).pack(side="left", padx=6)
        bf = tk.Frame(win, bg=NAVY)
        bf.pack(side="bottom", pady=self.px(14))

        def close(status, note):
            p = st.get("proc")
            if p:
                try:
                    p.kill()
                except Exception:
                    pass
            win.destroy()
            played = sorted({"1": "left", "2": "right",
                             "both": "both"}[x] for x in st["played"])
            self.finish_manual("Speakers", status, [
                note + (" (played: %s)" % ", ".join(played)
                        if played else " (nothing played)")])

        self.btn(bf, "Both work", lambda: close(
            "PASS", "Left and right speakers clear")).pack(side="left",
                                                           padx=4)
        self.btn(bf, "Left problem", lambda: close(
            "FAIL", "Left speaker problem"), "danger").pack(side="left",
                                                            padx=4)
        self.btn(bf, "Right problem", lambda: close(
            "FAIL", "Right speaker problem"), "danger").pack(side="left",
                                                             padx=4)
        self.btn(bf, "Crackling / no sound", lambda: close(
            "FAIL", "Crackling or no sound"), "danger", width=230).pack(
            side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", lambda: close(
            "WARN", "Speakers test closed without a result"))
        self.set_row("Speakers", "RUN")

    def _test_window(self, title, lines):
        """Full-screen test window with a title and help text at the top
        and a button row at the bottom. Returns (win, canvas, info, btns)."""
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=NAVY)
        # fill the whole screen, even if the window manager ignores
        # the fullscreen request
        win.geometry("%dx%d+0+0" % (win.winfo_screenwidth(),
                                    win.winfo_screenheight()))
        try:
            win.attributes("-fullscreen", True)
        except Exception:
            pass
        tk.Label(win, text=title, font=self.F(16, True), fg=TEXT,
                 bg=NAVY).pack(pady=(self.px(14), self.px(4)))
        for ln in lines:
            tk.Label(win, text=ln, font=self.F(11), fg=MUTED,
                     bg=NAVY).pack()
        info = tk.Label(win, text="", font=self.F(12, True), fg=CYAN,
                        bg=NAVY)
        info.pack(pady=(self.px(6), self.px(6)))
        btns = tk.Frame(win, bg=NAVY)
        btns.pack(side="bottom", pady=self.px(12))
        canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=self.px(20))
        return win, canvas, info, btns

    # ---------- webcam (live picture) ----------
    def manual_webcam(self):
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.title("Webcam test")
        win.configure(bg=NAVY)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(self.px(820), sw), min(self.px(680), sh)
        win.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
        self.lbl(win, "Webcam test", 16, True).pack(pady=(self.px(12), 2))
        self.lbl(win, "Look at the picture. Wave your hand in front of the "
                      "camera, then choose below.", 10, fg=MUTED).pack()
        pic = tk.Label(win, bg="black", text="Starting camera...",
                       fg=MUTED, font=self.F(12),
                       width=64, height=18)
        pic.pack(pady=self.px(10))
        info = self.lbl(win, "", 10, fg=MUTED)
        info.pack()
        bf = tk.Frame(win, bg=NAVY)
        bf.pack(side="bottom", pady=self.px(12))
        cam = {"proc": None, "frame": None, "img": None, "stop": False,
               "frames": 0, "dev": ""}

        def devices():
            out = []
            for p in sorted(glob.glob("/sys/class/video4linux/video*")):
                idx = ""
                try:
                    with open(p + "/index") as fh:
                        idx = fh.read().strip()
                except Exception:
                    pass
                name = ""
                try:
                    with open(p + "/name") as fh:
                        name = fh.read().strip()
                except Exception:
                    pass
                dev = "/dev/" + os.path.basename(p)
                if idx in ("", "0"):
                    out.append((dev, name))
            return out

        def read_ppm(fh):
            tokens = []
            while len(tokens) < 4:
                ln = fh.readline()
                if not ln:
                    return None
                tokens += ln.split(b"#")[0].split()
            fw, fh_ = int(tokens[1]), int(tokens[2])
            size = fw * fh_ * 3
            data = fh.read(size)
            if len(data) < size:
                return None
            return b"P6\n%d %d\n255\n" % (fw, fh_) + data

        def reader():
            devs = devices()
            if not devs:
                cam["error"] = "No camera found on this laptop."
                return
            if not shutil.which("ffmpeg"):
                cam["error"] = "ffmpeg is not installed on this USB."
                return
            for dev, name in devs:
                if cam["stop"]:
                    return
                cam["dev"] = "%s  %s" % (dev, name)
                try:
                    proc = subprocess.Popen(
                        ["ffmpeg", "-loglevel", "error", "-f", "v4l2",
                         "-i", dev, "-vf", "scale=640:-2", "-r", "12",
                         "-f", "image2pipe", "-vcodec", "ppm", "-"],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                except Exception as ex:
                    cam["error"] = "Camera could not start: %s" % ex
                    continue
                cam["proc"] = proc
                while not cam["stop"]:
                    fr = read_ppm(proc.stdout)
                    if fr is None:
                        break
                    cam["frame"] = fr
                    cam["frames"] += 1
                try:
                    proc.kill()
                except Exception:
                    pass
                if cam["frames"]:
                    return
            if not cam["frames"]:
                cam["error"] = "The camera gave no picture."

        def show():
            if cam["stop"]:
                return
            fr = cam.get("frame")
            if fr is not None:
                cam["frame"] = None
                try:
                    img = tk.PhotoImage(data=base64.b64encode(fr),
                                        format="PPM")
                    pic.configure(image=img, text="", width=img.width(),
                                  height=img.height())
                    cam["img"] = img
                    info.configure(text="Camera: %s" % cam["dev"], fg=OK)
                except Exception as ex:
                    info.configure(text="Picture error: %s" % ex, fg=WARN)
            elif cam.get("error"):
                pic.configure(text=cam["error"], fg=WARN)
            win.after(60, show)

        def close(status, note):
            cam["stop"] = True
            p = cam.get("proc")
            if p:
                try:
                    p.kill()
                except Exception:
                    pass
            win.destroy()
            note2 = note + (" (%s)" % cam["dev"].strip()
                            if cam["dev"] else "")
            self.finish_manual("Webcam", status, [note2])

        self.btn(bf, "Camera works", lambda: close(
            "PASS", "Live picture checked by technician")).pack(
            side="left", padx=4)
        self.btn(bf, "Picture bad / no picture", lambda: close(
            "FAIL", "Camera picture bad or missing"), "danger",
            width=250).pack(side="left", padx=4)
        self.btn(bf, "No camera on this laptop", lambda: close(
            NOT_FITTED, "No camera fitted"), "outline", width=250).pack(
            side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", lambda: close(
            "WARN", "Webcam test closed without a result"))
        threading.Thread(target=reader, daemon=True).start()
        win.after(200, show)

    # ---------- USB ports ----------
    def manual_usb(self):
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.title("USB ports test")
        win.configure(bg=NAVY)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(self.px(820), sw), min(self.px(600), sh)
        win.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
        self.lbl(win, "USB ports test", 16, True).pack(pady=(self.px(12), 2))
        self.lbl(win, "Plug a USB stick (or mouse) into EVERY USB port, one "
                      "after another, USB-C too.\nEach port is listed below "
                      "as soon as it works. The port with the boot USB "
                      "already works.", 10, fg=MUTED,
                 justify="center").pack()
        count = self.lbl(win, "Ports found: 0", 14, True, fg=CYAN)
        count.pack(pady=(self.px(12), self.px(6)))
        lst = self.lbl(win, "Waiting for a device...", 11, fg=TEXT,
                       justify="left", wraplength=w - 60)
        lst.pack(fill="both", expand=True, padx=30)
        bf = tk.Frame(win, bg=NAVY)
        bf.pack(side="bottom", pady=self.px(12))

        def rd(path):
            try:
                with open(path) as fh:
                    return fh.read().strip()
            except Exception:
                return ""

        def snapshot():
            out = {}
            for p in glob.glob("/sys/bus/usb/devices/*"):
                n = os.path.basename(p)
                if ":" in n or n.startswith("usb"):
                    continue
                out[n] = p
            return out

        base = set(snapshot())
        st = {"ports": {}, "stop": False}

        def speed_text(sp):
            try:
                v = int(float(sp))
            except Exception:
                return "USB"
            if v >= 10000:
                return "USB 3.2 (10 Gbps)"
            if v >= 5000:
                return "USB 3 (5 Gbps)"
            if v >= 480:
                return "USB 2"
            return "USB 1"

        def poll():
            if st["stop"]:
                return
            for n, p in snapshot().items():
                if n in base:
                    continue
                port = n.split(".")[0]
                if port in st["ports"]:
                    continue
                prod = (rd(p + "/manufacturer") + " " +
                        rd(p + "/product")).strip() or "USB device"
                st["ports"][port] = "Port %s  -  %s  -  %s" % (
                    port, speed_text(rd(p + "/speed")), prod)
            if st["ports"]:
                count.configure(text="Ports found: %d" % len(st["ports"]),
                                fg=OK)
                lst.configure(text="\n".join(
                    "OK   " + v for v in st["ports"].values()))
            win.after(600, poll)

        def close(status, note):
            st["stop"] = True
            win.destroy()
            ports = list(st["ports"].values())
            notes = [note + " - %d port(s) seen working" % len(ports)]
            notes += ports[:8]
            self.finish_manual("USB", status, notes)

        self.btn(bf, "All ports work", lambda: close(
            "PASS", "All USB ports checked")).pack(side="left", padx=4)
        self.btn(bf, "A port does not work", lambda: close(
            "FAIL", "A USB port does not work"), "danger",
            width=240).pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", lambda: close(
            "WARN", "USB test closed without a result"))
        win.after(300, poll)

    def manual_touchpad(self):
        win, c, info, btns = self._test_window(
            "Touchpad test",
            ["Move over every box, then left click and right click."])
        cols, rows = 8, 5
        st = {"cells": set(), "left": False, "right": False, "scroll": False,
              "done": False}

        def draw(_e=None):
            c.delete("all")
            w, h = max(c.winfo_width(), 1), max(c.winfo_height(), 1)
            for i in range(cols):
                for j in range(rows):
                    x1, y1 = i * w / cols, j * h / rows
                    on = (i, j) in st["cells"]
                    c.create_rectangle(x1 + 2, y1 + 2, x1 + w / cols - 2,
                                       y1 + h / rows - 2,
                                       fill=OK if on else NAVY,
                                       outline=BORDER)
            update()

        def update():
            info.configure(text="%d / %d      Left click: %s      Right click: %s"
                                % (len(st["cells"]), cols * rows,
                                   "OK" if st["left"] else "-",
                                   "OK" if st["right"] else "-"))

        def motion(e):
            w, h = max(c.winfo_width(), 1), max(c.winfo_height(), 1)
            cell = (min(cols - 1, max(0, int(e.x * cols / w))),
                    min(rows - 1, max(0, int(e.y * rows / h))))
            if cell not in st["cells"]:
                st["cells"].add(cell)
                draw()

        def click(e, which):
            motion(e)
            st[which] = True
            update()

        def scroll(_e):
            st["scroll"] = True

        def result(faulty):
            if st["done"]:
                return
            st["done"] = True
            win.destroy()
            total = cols * rows
            missing = []
            if len(st["cells"]) < total:
                missing.append("%d of %d areas not reached"
                               % (total - len(st["cells"]), total))
            if not st["left"]:
                missing.append("left click not seen")
            if not st["right"]:
                missing.append("right click not seen")
            if faulty:
                self.finish_manual("Touchpad", "FAIL",
                                   ["Technician reported a fault"
                                    + ("; " + "; ".join(missing)
                                       if missing else "")])
            elif missing:
                self.finish_manual("Touchpad", "WARN",
                                   ["Partial test: " + "; ".join(missing)])
            else:
                self.finish_manual("Touchpad", "PASS",
                                   ["Whole pad, left and right click OK"
                                    + (", scroll OK" if st["scroll"] else "")])

        c.bind("<Configure>", draw)
        c.bind("<Motion>", motion)
        c.bind("<Button-1>", lambda e: click(e, "left"))
        c.bind("<Button-3>", lambda e: click(e, "right"))
        for ev in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            c.bind(ev, scroll)
        self.btn(btns, "Done", lambda: result(False)).pack(side="left",
                                                           padx=6)
        self.btn(btns, "Touchpad faulty", lambda: result(True),
                 "danger").pack(side="left", padx=6)
        win.bind("<Escape>", lambda e: result(False))
        self.set_row("Touchpad", "RUN")
        win.focus_force()

    def manual_touchscreen(self):
        name = find_touchscreen()
        if not name:
            self.finish_manual("Touchscreen", NOT_FITTED,
                               ["No touchscreen on this laptop"])
            b = self.manual_btns.get("Touchscreen")
            if b:
                b.configure(state="disabled")
            self.mb.showinfo("Touchscreen", "No touchscreen was found on "
                             "this laptop, so there is nothing to test.")
            return
        win, c, info, btns = self._test_window(
            "Touchscreen test",
            ["Touch every circle with your finger."])
        cols, rows = 4, 3
        st = {"hit": set(), "done": False, "pts": {}}

        def draw(_e=None):
            c.delete("all")
            w, h = max(c.winfo_width(), 1), max(c.winfo_height(), 1)
            r = max(18, min(w / cols, h / rows) * 0.22)
            st["r"] = r
            # circles near the edges and corners as well as the middle
            for i in range(cols):
                for j in range(rows):
                    x = r + 6 + i * (w - 2 * r - 12) / (cols - 1)
                    y = r + 6 + j * (h - 2 * r - 12) / (rows - 1)
                    st["pts"][(i, j)] = (x, y)
                    on = (i, j) in st["hit"]
                    c.create_oval(x - r, y - r, x + r, y + r,
                                  fill=OK if on else NAVY,
                                  outline=CYAN, width=3)
            info.configure(text="%d / %d" % (len(st["hit"]), cols * rows))

        def touch(e):
            r = st.get("r", 30)
            for key, (x, y) in st["pts"].items():
                if (e.x - x) ** 2 + (e.y - y) ** 2 <= (r * 1.2) ** 2:
                    if key not in st["hit"]:
                        st["hit"].add(key)
                        draw()
                        if len(st["hit"]) == cols * rows:
                            win.after(500, lambda: result(False))

        def result(faulty):
            if st["done"]:
                return
            st["done"] = True
            win.destroy()
            total = cols * rows
            got = len(st["hit"])
            if faulty:
                self.finish_manual("Touchscreen", "FAIL",
                                   ["Technician reported a fault; %d of %d "
                                    "points touched" % (got, total)])
            elif got < total:
                self.finish_manual("Touchscreen", "WARN",
                                   ["Partial test: %d of %d points touched"
                                    % (got, total)])
            else:
                self.finish_manual("Touchscreen", "PASS",
                                   ["All %d points touched" % total])

        c.bind("<Configure>", draw)
        c.bind("<Button-1>", touch)
        c.bind("<B1-Motion>", touch)
        self.btn(btns, "Done", lambda: result(False)).pack(side="left",
                                                           padx=6)
        self.btn(btns, "Touchscreen faulty", lambda: result(True),
                 "danger").pack(side="left", padx=6)
        win.bind("<Escape>", lambda e: result(False))
        self.set_row("Touchscreen", "RUN")
        win.focus_force()

    def manual_microphone(self):
        if not shutil.which("arecord"):
            self.finish_manual("Microphone", "WARN",
                               ["arecord not installed (alsa-utils)"])
            return
        if not self.mb.askokcancel(
                "Microphone", "Press OK, then talk for 4 seconds."):
            return
        self.set_row("Microphone", "RUN")
        self.t_status.configure(text="Recording... talk or clap now "
                                     "(4 seconds)", fg=CYAN)

        def work():
            unmute_audio()
            # Record from every recording device at the same time, so the
            # technician only has to talk once. Keep the loudest.
            procs = []
            for n, dev in enumerate(capture_devices()[:6]):
                path = "/tmp/itad_mic_%d.wav" % n
                try:
                    p = subprocess.Popen(
                        ["arecord", "-q", "-D", dev, "-d", "4", "-f",
                         "S16_LE", "-r", "44100", "-c", "1", path],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    procs.append((p, dev, path))
                except Exception:
                    pass
            for p, _, _ in procs:
                try:
                    p.wait(timeout=10)
                except Exception:
                    p.kill()
            best, best_dev, best_path = 0.0, "", ""
            for _, dev, path in procs:
                lvl = wav_level(path)
                if lvl > best:
                    best, best_dev, best_path = lvl, dev, path
            if best >= 0.03 and shutil.which("aplay"):
                self.q.put(lambda: self.t_status.configure(
                    text="Playing the recording back...", fg=CYAN))
                try:
                    subprocess.call(["aplay", "-q", best_path], timeout=10,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
                except Exception:
                    pass
            for _, _, path in procs:
                try:
                    os.remove(path)
                except OSError:
                    pass
            self.q.put(lambda: ask(best, best_dev))

        def ask(level, dev):
            self.t_status.configure(text="", fg=TEXT)
            pct = int(round(level * 100))
            if level < 0.03:
                self.finish_manual("Microphone", "FAIL",
                                   ["No sound picked up (level %d%%)" % pct])
                self.mb.showwarning("Microphone", "The microphone picked "
                                    "up no sound (level %d%%)." % pct)
                return
            if self.mb.askyesno("Microphone", "Sound level %d%%.\n\nDid you "
                                "hear your recording clearly?" % pct):
                self.finish_manual("Microphone", "PASS",
                                   ["Recording heard, level %d%% (%s)"
                                    % (pct, dev)])
            else:
                self.finish_manual("Microphone", "FAIL",
                                   ["Recording not clear, level %d%% (%s)"
                                    % (pct, dev)])
        threading.Thread(target=work, daemon=True).start()

    # ---------- observations ----------
    def build_observe(self):
        tk = self.tk
        pg = self.new_page("observe")
        self.page_title(pg, "Observations", "Record the condition of the "
                        "laptop. This goes into the report.")
        row = tk.Frame(pg, bg=BG)
        row.pack(fill="both", expand=True, padx=self.px(23))
        left = tk.Frame(row, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        g_outer, g = self.card(left, "Cosmetic grade", "check")
        g_outer.pack(fill="x", padx=self.px(5), pady=(0, self.px(10)))
        self.v_grade = tk.StringVar(value="")
        gr = tk.Frame(g, bg=NAVY)
        gr.pack(anchor="w")
        for code, text in GRADES:
            tk.Radiobutton(gr, text="%s  %s" % (code, text),
                           variable=self.v_grade, value=code,
                           indicatoron=0, font=self.F(11, True), fg=TEXT,
                           bg=BG2, selectcolor="#1767E6",
                           activebackground="#0F2A52", activeforeground=TEXT,
                           relief="flat", bd=0, padx=self.px(14),
                           pady=self.px(8), highlightthickness=1,
                           highlightbackground=BORDER, cursor="hand2"
                           ).pack(side="left", padx=(0, self.px(8)))
        i_outer, iw = self.card(left, "Issues found (tick all that apply)",
                                "search")
        i_outer.pack(fill="both", expand=True, padx=self.px(5))
        self.v_issues = {}
        for n, name in enumerate(ISSUES):
            v = tk.BooleanVar(value=False)
            self.v_issues[name] = v
            tk.Checkbutton(iw, text=name, variable=v, font=self.F(11),
                           fg=TEXT, bg=NAVY, selectcolor=BG2,
                           activebackground=NAVY, activeforeground=TEXT,
                           highlightthickness=0, bd=0, anchor="w",
                           cursor="hand2").grid(
                row=n // 2, column=n % 2, sticky="w",
                padx=(0, self.px(30)), pady=self.px(2))
        right = tk.Frame(row, bg=BG, width=self.px(430))
        right.pack(side="left", fill="both")
        right.pack_propagate(False)
        n_outer, nw = self.card(right, "Notes", "doc")
        n_outer.pack(fill="both", expand=True, padx=self.px(5),
                     pady=(0, self.px(10)))
        self.o_notes = tk.Text(nw, height=5, bg=BG2, fg=TEXT,
                               insertbackground=TEXT, relief="flat",
                               font=self.F(11), wrap="word",
                               highlightthickness=1,
                               highlightbackground=BORDER,
                               highlightcolor=CYAN)
        self.o_notes.pack(fill="both", expand=True)
        p_outer, pw = self.card(right, "Photos", "camera")
        p_outer.pack(fill="x", padx=self.px(5))
        self.o_photos = self.lbl(pw, "No photos added.", 10, fg=MUTED,
                                 wraplength=380)
        self.o_photos.pack(anchor="w", pady=(0, self.px(8)))
        pb = tk.Frame(pw, bg=NAVY)
        pb.pack(anchor="w")
        self.btn(pb, "Add photos", self.add_photos, "outline").pack(
            side="left", padx=(0, self.px(8)))
        self.btn(pb, "Clear", self.clear_photos, "ghost").pack(side="left")
        left_b, right_b, mid = self.bar(pg)
        self.btn(left_b, "Back to tests", lambda: self.show("wipe"),
                 "ghost").pack()
        self.o_msg = mid
        Pill(self, right_b, "Create report", self.finish, width=230,
             height=48, icon="doc").pack()

    def enter_observe(self):
        state, txt = self.test_summary()
        self.set_stage("test", state, txt)
        self.set_stage("obs", "run", "Add grade, issues, notes")
        self.show("observe")

    def add_photos(self):
        try:
            from tkinter import filedialog
        except Exception:
            return
        start = self.out_dir
        for c in ("/mnt/itad/photos", os.path.join(self.out_dir, "photos")):
            if os.path.isdir(c):
                start = c
                break
        files = filedialog.askopenfilenames(
            parent=self.root, title="Choose photos", initialdir=start,
            filetypes=[("Photos", "*.jpg *.jpeg *.png *.JPG *.JPEG *.PNG"),
                       ("All files", "*")])
        for f in files or []:
            if f not in self.s["obs"]["photos"]:
                self.s["obs"]["photos"].append(f)
        self.show_photos()

    def clear_photos(self):
        self.s["obs"]["photos"] = []
        self.show_photos()

    def show_photos(self):
        ph = self.s["obs"]["photos"]
        self.o_photos.configure(
            text=("%d photo(s): " % len(ph) + ", ".join(
                os.path.basename(p) for p in ph[:6])
                  + (" ..." if len(ph) > 6 else "")) if ph else
            "No photos added. Put photos in a 'photos' folder on the "
            "ITAD_DATA stick, then press Add photos.",
            fg=TEXT if ph else MUTED)

    def read_observations(self):
        o = self.s["obs"]
        o["grade"] = self.v_grade.get()
        o["issues"] = [k for k, v in self.v_issues.items() if v.get()]
        o["notes"] = self.o_notes.get("1.0", "end").strip()
        return o

    # ---------- report ----------
    def build_report(self):
        tk = self.tk
        pg = self.new_page("report")
        self.r_banner = tk.Label(pg, text="", font=self.F(26, True), fg=NAVY,
                                 bg=GREEN_BAN, pady=self.px(12))
        self.r_banner.pack(fill="x", padx=self.px(28),
                           pady=(self.px(18), self.px(8)))
        self.r_reasons = self.lbl(pg, "", 10, fg=WARN, bg=BG)
        self.r_reasons.pack(fill="x", padx=self.px(30))
        row = tk.Frame(pg, bg=BG)
        row.pack(fill="both", expand=True, padx=self.px(23), pady=6)
        l_outer, self.r_left = self.card(row, "Laptop and job", "monitor")
        l_outer.pack(side="left", fill="both", expand=True, padx=self.px(5))
        r_outer, self.r_right = self.card(row, "Results", "doc")
        r_outer.pack(side="left", fill="both", expand=True, padx=self.px(5))
        self.r_files = self.lbl(pg, "", 10, fg=MUTED, bg=BG, wraplength=900)
        self.r_files.pack(fill="x", padx=self.px(30), pady=(0, 4))
        left, right, mid = self.bar(pg)
        self.btn(left, "Home", lambda: self.show("home"), "ghost").pack()
        Pill(self, right, "Next laptop", self.next_laptop, width=230,
             height=48, icon="play").pack()

    def collect_tests(self):
        tests = []
        got = set()
        for r in self.s["auto"]:
            if r.get("component") in MANUAL:
                continue
            tests.append(r)
            got.add(r.get("component"))
        # An automatic test that gave no result (crash, stopped early)
        # must show in the report, not silently disappear.
        for comp in COMPONENTS:
            if comp not in MANUAL and comp not in got:
                tests.append({"component": comp, "status": "WARN",
                              "notes": ["No result from the automatic test"],
                              "details": {}})
        for comp in MANUAL:
            tests.append(self.s["manual"].get(comp) or {
                "component": comp, "status": "WARN",
                "notes": ["Not tested"], "details": {}})
        out = []
        for t in tests:
            ov = self.s.get("override", {}).get(t.get("component"))
            if ov:
                t = dict(t)
                t["notes"] = [ov["note"] + " (automatic result was %s)" %
                              t.get("status", "?")] + list(t.get("notes") or [])
                t["status"] = ov["status"]
            out.append(t)
        return out

    def build_report_data(self):
        chk = self.s.get("check") or {}
        wipe = self.s.get("wipe") or {}
        tests = self.collect_tests()
        i = self.ident
        stor = "; ".join("%s (%s) %s GB" % (d.get("model") or "Drive",
                                            d.get("drive_type", "?"),
                                            d.get("size_gb"))
                         for d in self.disks) or "-"
        overall, reasons = compute_overall(self.s["wipe_state"],
                                           wipe.get("overall"), tests)
        if self.test_mode:
            if overall == "PASS":
                overall = "INCOMPLETE"
            reasons.insert(0, "TEST MODE - check / wipe skipped, NOT for "
                              "customer or resale use")
        er = chk.get("autopilot_result", "?")
        obs = dict(self.read_observations())
        return {
            "tool": "laptop_window.py", "version": VERSION,
            "time": now().strftime("%Y-%m-%d %H:%M:%S") + " (Europe/Dublin)",
            "job_id": self.job(), "asset_id": self.asset(),
            "serial": self.serial() or "UNKNOWN", "technician": self.tech(),
            "location": self.v_loc.get().strip(),
            "make_model": ("%s %s" % (i.get("manufacturer", ""),
                                      i.get("model", ""))).strip(),
            "cpu": i.get("cpu", ""),
            "ram": ram_text(self.ram).replace("\n", "; "),
            "ram_details": self.ram,
            "screen": screen_text(self.screen),
            "screen_details": self.screen or {},
            "storage": stor,
            "absolute": (chk.get("absolute_result", "?") if chk else
                         "NOT CHECKED (TEST MODE)"),
            "enrolment": ({"CLEAN": "CLEAN", "BLOCKED": "ACTIVE",
                           "UNKNOWN": "NOT CHECKED"}.get(er, "PAST ENROLMENT")
                          if chk else "NOT CHECKED (TEST MODE)"),
            "test_mode": self.test_mode,
            "check_result": chk.get("result", "?"),
            "wipe_state": self.s["wipe_state"],
            "wipe_devices": wipe.get("devices") or [],
            "certificate": wipe.get("certificate_sha256", ""),
            "tests": tests, "observations": obs,
            "overall": overall, "reasons": reasons,
        }

    def finish(self):
        rep = self.build_report_data()
        obs = rep["observations"]
        n_iss = len(obs.get("issues") or [])
        self.set_stage("obs", "ok" if not n_iss else "warn",
                       "Grade %s, %d issue%s" % (obs.get("grade") or "-",
                                                 n_iss,
                                                 "" if n_iss == 1 else "s"))
        self.show("report")
        bg, fg = {"PASS": (GREEN_BAN, NAVY), "FAIL": (RED_BAN, "white")}.get(
            rep["overall"], (WARN, NAVY))
        self.r_banner.configure(text=rep["overall"], bg=bg, fg=fg)
        # Short reasons on screen; the full detail is in the TXT/PDF report.
        short = [r.split(":")[0] for r in rep["reasons"]]
        if len(short) > 4:
            short = short[:3] + ["+%d more (see report)" % (len(short) - 3)]
        self.r_reasons.configure(text="     ".join(short), anchor="w")
        for frame in (self.r_left, self.r_right):
            for w in frame.winfo_children():
                w.destroy()
        left = [("Job ID", rep["job_id"]), ("Asset ID", rep["asset_id"]),
                ("Serial", rep["serial"]), ("Technician", rep["technician"]),
                ("Location", rep["location"]), ("Time", rep["time"]),
                ("Model", rep["make_model"]),
                ("RAM", ram_text(self.ram, short=True)),
                ("Screen", rep["screen"])]
        for r, (k, v) in enumerate(left):
            self.lbl(self.r_left, k, 10, fg=MUTED).grid(
                row=r, column=0, sticky="nw", padx=(0, self.px(14)), pady=2)
            self.lbl(self.r_left, v, 11, True, wraplength=380).grid(
                row=r, column=1, sticky="nw", pady=2)
        good = sum(1 for t in rep["tests"] if t.get("status") == "PASS")
        fitted = sum(1 for t in rep["tests"] if t.get("status") != NOT_FITTED)
        right = [("Absolute", rep["absolute"]), ("Enrolment", rep["enrolment"]),
                 ("Wipe", rep["wipe_state"]),
                 ("Components", "%d of %d PASS" % (good, fitted)),
                 ("Grade", obs.get("grade") or "-"),
                 ("Issues", ", ".join(obs.get("issues") or []) or "none"),
                 ("Photos", str(len(obs.get("photos") or [])))]
        for r, (k, v) in enumerate(right):
            self.lbl(self.r_right, k, 10, fg=MUTED).grid(
                row=r, column=0, sticky="nw", padx=(0, self.px(14)), pady=2)
            col = OK if v in ("CLEAR", "CLEAN", "WIPED") else TEXT
            self.lbl(self.r_right, v, 11, True, fg=col, wraplength=360).grid(
                row=r, column=1, sticky="nw", pady=2)
        saved = self.save_reports(rep)
        kinds = ", ".join(os.path.splitext(p)[1][1:].upper() for p in saved
                          if not p.endswith((".json", ".log")))
        if not saved:
            msg = "Report NOT saved"
        elif self.persistent:
            msg = "Report saved (%s)%s" % (
                kinds, " - digitally signed" if (rep.get("integrity") or {})
                .get("signed") else " - NOT signed")
        else:
            msg = ("Report saved to memory only - copy it before "
                   "powering off (no ITAD_DATA stick)")
        self.r_files.configure(text=msg, fg=BAD if not saved else (
            MUTED if self.persistent else WARN))
        has_pdf = any(p.endswith(".pdf") for p in saved)
        self.set_stage("report", "ok" if has_pdf else (
            "warn" if saved else "bad"),
            "Saved TXT + PDF" if has_pdf else (
                "Saved (no PDF)" if saved else "Not saved"))

    def save_reports(self, rep):
        # The date and time in the name keeps an earlier report of the
        # same laptop (a re-test) instead of writing over it.
        base = "SN_%s_%s" % (
            safe_name(rep["serial"] if rep["serial"] != "UNKNOWN"
                      else rep["asset_id"]),
            now().strftime("%Y%m%d-%H%M%S"))
        obs = rep["observations"]
        copied = []
        for n, src in enumerate(obs.get("photos") or [], start=1):
            ext = os.path.splitext(src)[1].lower() or ".jpg"
            dst = os.path.join(self.out_dir, "%s_photo%d%s" % (base, n, ext))
            try:
                if os.path.realpath(src) != os.path.realpath(dst):
                    shutil.copyfile(src, dst)
                copied.append(dst)
            except Exception:
                pass
        obs["photo_files"] = copied
        # sign last: the fingerprint covers everything in the report
        sign_report(rep, self.out_dir, self.persistent)
        saved = []
        for ext, fn in (("txt", lambda p: open(p, "w").write(render_txt(rep))),
                        ("json", lambda p: json.dump(rep, open(p, "w"),
                                                     indent=2)),
                        ("pdf", lambda p: write_pdf(p, rep)),
                        ("log", lambda p: open(p, "w").write(
                            "\n".join(self.log) + "\n"))):
            path = os.path.join(self.out_dir, "%s.%s" % (base, ext))
            try:
                fn(path)
                saved.append(path)
            except Exception:
                pass
        return saved

    # ---------- settings and about ----------
    def build_settings(self):
        tk = self.tk
        pg = self.new_page("settings")
        self.page_title(pg, "Settings")
        row = tk.Frame(pg, bg=BG)
        row.pack(fill="both", expand=True, padx=self.px(23))
        s_outer, s = self.card(row, "Reports and accounts", "doc")
        s_outer.pack(side="left", fill="both", expand=True, padx=self.px(5))
        self.st_store = self.lbl(s, "", 11, wraplength=380)
        self.st_store.pack(anchor="w")
        self.lbl(s, "Plug in a USB stick named ITAD_DATA before booting to "
                    "keep reports, photos and technician accounts.", 10,
                 fg=MUTED, wraplength=380).pack(anchor="w",
                                                pady=(self.px(10), 0))
        t_outer, t = self.card(row, "Technicians", "user")
        t_outer.pack(side="left", fill="both", expand=True, padx=self.px(5))
        self.st_techs = self.lbl(t, "", 11, wraplength=360)
        self.st_techs.pack(anchor="w", pady=(0, self.px(12)))
        self.btn(t, "Add technician", self.add_tech_dialog,
                 "outline").pack(anchor="w")
        j_outer, j = self.card(row, "Job defaults", "gear")
        j_outer.pack(side="left", fill="both", expand=True, padx=self.px(5))
        self.lbl(j, "LOCATION", 9, True, fg=MUTED).pack(anchor="w")
        self.ttk.Combobox(j, textvariable=self.v_loc, values=LOCATIONS,
                          font=self.F(12)).pack(fill="x", pady=(2, self.px(14)))
        self.lbl(j, "MODE", 9, True, fg=MUTED).pack(anchor="w")
        mode = []
        mode.append("Dry run (nothing erased)" if self.dry else
                    "Normal (wipe is real)")
        mode.append("Quick tests" if self.quick else "Full tests")
        self.lbl(j, "\n".join(mode), 11).pack(anchor="w")
        self.lbl(j, "TEST MODE", 9, True, fg=MUTED).pack(
            anchor="w", pady=(self.px(14), 0))
        self.st_test = self.lbl(j, "", 11, True, wraplength=320)
        self.st_test.pack(anchor="w")
        self.st_test_btn = self.btn(j, "Turn Test mode ON",
                                    self.toggle_test_mode, "outline",
                                    width=230)
        self.st_test_btn.pack(anchor="w", pady=(self.px(6), 0))
        left, right, mid = self.bar(pg)
        self.btn(left, "Home", lambda: self.show("home"), "ghost").pack()

    def refresh_settings(self):
        self.st_store.configure(
            text=("Saved to the ITAD_DATA stick:\n%s" % self.out_dir)
            if self.persistent else
            ("No ITAD_DATA stick found.\nFiles go to %s and are lost at "
             "power off." % self.out_dir),
            fg=OK if self.persistent else WARN)
        acc = load_accounts(self.out_dir)
        ready = sorted(a["name"] for a in acc.values() if a["hash"])
        nopw = sorted(a["name"] for a in acc.values() if not a["hash"])
        txt = ("%d technician%s: %s" % (len(ready), "" if len(ready) == 1
                                         else "s", ", ".join(ready))
               if ready else "No technician accounts yet.")
        if nopw:
            txt += "\nNo password yet: " + ", ".join(nopw)
        self.st_techs.configure(text=txt)
        if self.test_mode:
            self.st_test.configure(text="ON - check and wipe can be skipped, "
                                        "nothing is erased", fg=BAD)
            self.st_test_btn.configure(text="Turn Test mode OFF")
        else:
            self.st_test.configure(text="OFF - normal work", fg=OK)
            self.st_test_btn.configure(text="Turn Test mode ON")

    def build_about(self):
        tk = self.tk
        pg = self.new_page("about")
        self.page_title(pg, "About")
        o, b = self.card(pg, "ITAD Laptop v%s" % VERSION, "info")
        o.pack(fill="x", padx=self.px(28))
        lines = [
            "Tests, wipes and certifies laptops so they can be reused.",
            "",
            "Order of work:  1 Check (Absolute lock, enrolment)  >  2 Wipe  "
            ">  3 Component tests  >  4 Observations  >  5 Report",
            "",
            "Reports are named after the serial number and saved as TXT, "
            "PDF, JSON and LOG.",
            "Technician passwords are stored as salted PBKDF2-SHA256 hashes "
            "only.",
            "",
            "Keys:  Esc = full screen on/off    Ctrl+Q = close the window",
            "Boot mode: %s    Tools folder: %s" % (
                self.ident.get("boot_mode") or "-", TOOLS_DIR),
        ]
        self.lbl(b, "\n".join(lines), 11, wraplength=900).pack(anchor="w")
        left, right, mid = self.bar(pg)
        self.btn(left, "Home", lambda: self.show("home"), "ghost").pack()

    # ---------- misc ----------
    def next_laptop(self):
        if self.wiping:
            self.mb.showinfo("Wipe running", "A wipe is running. Please wait.")
            return
        # keeps ITAD_SIGNED_IN so the technician stays signed in
        os.execv(PY, [PY] + sys.argv)

    def power_off(self):
        if self.wiping:
            self.mb.showinfo("Wipe running", "A wipe is running. Do not power "
                             "off until it has finished.")
            return
        if self.mb.askyesno("Shut down", "Shut down this laptop now?"):
            if subprocess.call(["systemctl", "poweroff"]) != 0:
                subprocess.call(["poweroff"])

    def quit(self):
        if self.wiping:
            self.mb.showinfo("Wipe running", "A wipe is running. Please wait.")
            return
        self.root.destroy()
        sys.exit(0)


def main():
    ap = argparse.ArgumentParser(description="ITAD Laptop window")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--windowed", action="store_true")
    args = ap.parse_args()

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        if shutil.which("sudo"):
            os.execvp("sudo", ["sudo", "-E", PY] + sys.argv)
        print("Must run as root.")
        sys.exit(3)
    for p in (CHECK, WIPE, HW_TEST):
        if not os.path.isfile(p):
            print("Missing tool: %s" % p)
            sys.exit(1)
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        fallback_terminal("no screen session", args.dry_run)
    try:
        import tkinter as tk
        import tkinter.font  # noqa: F401  (used for text sizes)
        from tkinter import messagebox, ttk
    except Exception as e:
        fallback_terminal("Tk is not installed: %s" % e, args.dry_run)
    try:
        root = tk.Tk()
    except Exception as e:
        fallback_terminal("cannot open the window: %s" % e, args.dry_run)
    App(tk, ttk, messagebox, root, args.dry_run, args.quick, args.windowed)
    root.mainloop()


if __name__ == "__main__":
    main()
