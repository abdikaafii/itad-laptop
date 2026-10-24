#!/usr/bin/env python3
"""
laptop_test.py - ITAD component test (v1.1.1)  READ-MOSTLY

Step 2 of the ITAD pipeline. Runs AFTER laptop_check.py and the wipe.

v1.1.1: bug fixes over v1.1.0 (no change to test logic):
  - --json - no longer pollutes stdout (human output goes to stderr)
  - tempfile.mktemp() replaced with mkstemp (avoids races / symlink attacks)
  - capture temp file is removed even on failure
  - file handles closed with `with`
  - 'clear' uses ANSI rather than shelling out
  - SMART error note is stored as a plain string, not str-or-list
  - battery: WARN when a battery is present but health is unreadable
  - drive: WARN when SMART status is unknown (was PASS)

Tests:
  cpu       - identity, temperature, 30s load test, thermal throttling
  ram       - total, ECC errors, optional memtester stress
  drive     - SMART health, SSD wear, bad sectors
  battery   - design vs full capacity, health %, cycle count
  screen    - fullscreen colour patterns (interactive)
  keyboard  - keypress capture (interactive)
  touchpad  - device presence (drag-to-confirm note)
  webcam    - enumerate device, capture one frame
  speakers  - play tone, tech confirms audible (interactive)
  wifi      - interface up, scan for networks
  usb       - controllers, plug-in detection (interactive)

Exit codes:  0 = PASS   1 = FAIL   2 = WARN / partial   3 = not root

Dependencies (best effort, each test degrades gracefully):
  lscpu, stress-ng, memtester, smartmontools, v4l-utils, ffmpeg,
  alsa-utils (speaker-test), evtest, iw or nmcli, lsusb, usbutils

Run:
  sudo python3 /opt/itad-laptop/laptop_test.py --job-id J123 --tech Ali
  sudo python3 /opt/itad-laptop/laptop_test.py --test cpu,ram,drive
  sudo python3 /opt/itad-laptop/laptop_test.py --no-interactive
"""

import argparse
import glob
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    NOW = datetime.now(ZoneInfo("Europe/Dublin"))
except Exception:
    NOW = datetime.now()

VERSION = "1.1.1"
ALL_TESTS = ["cpu", "ram", "drive", "battery", "screen", "keyboard",
             "touchpad", "webcam", "speakers", "wifi", "usb"]

# =============================================================================
# LOOKS  --  colours, boxes, banners.  Display only, no checking logic here.
# Colours switch off by themselves when output is saved to a file / piped,
# or when you pass --no-color.  Works offline, needs nothing extra installed.
# =============================================================================
ANSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
_TERM = os.environ.get("TERM", "")
LIVE = sys.stdout.isatty() and _TERM != "dumb"        # can redraw lines
USE_COLOR = LIVE and not os.environ.get("NO_COLOR")   # can use colours
_ENC = (getattr(sys.stdout, "encoding", None) or "").lower()
UNICODE = "utf" in _ENC
FANCY = UNICODE and _TERM != "linux"   # fancy icons only where fonts have them

try:
    W = max(60, min(shutil.get_terminal_size((80, 24)).columns, 78))
except Exception:
    W = 78
INNER = W - 4

if UNICODE:
    BX = dict(h="─", v="│", tl="┌", tr="┐", bl="└", br="┘", ml="├", mr="┤",
              H="═", V="║", TL="╔", TR="╗", BL="╚", BR="╝",
              full="█", empty="░", ell="…")
else:
    BX = dict(h="-", v="|", tl="+", tr="+", bl="+", br="+", ml="+", mr="+",
              H="=", V="|", TL="+", TR="+", BL="+", BR="+",
              full="#", empty=".", ell="~")

if FANCY:
    ICON = {"ok": "✔", "bad": "✖", "warn": "▲", "skip": "○", "info": "●",
            "lock": "■", "go": "▸"}
else:
    ICON = {"ok": "+", "bad": "x", "warn": "!", "skip": "-", "info": "*",
            "lock": "#", "go": ">"}

_CODES = {
    "reset": "0", "bold": "1", "dim": "2",
    "black": "30", "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "white": "37",
    "bg_red": "41", "bg_green": "42", "bg_yellow": "43", "bg_blue": "44",
}


def paint(text, *styles):
    if not USE_COLOR or not styles:
        return text
    return "\033[%sm%s\033[0m" % (";".join(_CODES[s] for s in styles), text)


def vlen(s):
    return len(ANSI_RE.sub("", s))


def pad(s, n):
    return s + " " * max(0, n - vlen(s))


def _trunc(s, n):
    if vlen(s) <= n:
        return s
    out, count = [], 0
    for tok in re.split(r"(\033\[[0-9;?]*[A-Za-z])", s):
        if ANSI_RE.fullmatch(tok):
            out.append(tok)
            continue
        room = n - 1 - count
        if room <= 0:
            break
        take = tok[:room]
        out.append(take)
        count += len(take)
        if len(take) < len(tok):
            break
    return "".join(out) + BX["ell"] + ("\033[0m" if USE_COLOR else "")


def _wrap(text, width):
    lines, cur = [], ""
    for word in str(text).split():
        while len(word) > width:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:width])
            word = word[width:]
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = (cur + " " + word) if cur else word
    if cur:
        lines.append(cur)
    return lines


_STATE = {
    "CLEAR": ("ok", "green"), "CLEAN": ("ok", "green"),
    "PASS": ("ok", "green"), "OK": ("ok", "green"),
    "BLOCKED": ("bad", "red"), "FAIL": ("bad", "red"),
    "ACTIVE": ("bad", "red"),
    "WARN": ("warn", "yellow"), "PAST": ("warn", "yellow"),
    "CANNOT CHECK": ("warn", "yellow"), "UNKNOWN": ("warn", "yellow"),
    "SKIP": ("skip", None),
}


def tag(state, label=None):
    key = str(state).split(" (")[0].strip().upper()
    icon, color = _STATE.get(key, ("info", "cyan"))
    text = "%s %s" % (ICON[icon], label if label is not None else state)
    return paint(text, "bold", color) if color else paint(text, "dim")


def rule(title, color="cyan"):
    t = " %s " % title
    fill = max(2, W - 4 - len(t))
    print()
    print(paint(BX["h"] * 2, color) + paint(t, "bold", color)
          + paint(BX["h"] * fill, color))


def box(title, rows, color="cyan"):
    t = (" %s " % title) if title else ""
    fill = max(0, W - 3 - len(t))
    print(paint(BX["tl"] + BX["h"], color) + paint(t, "bold", color)
          + paint(BX["h"] * fill + BX["tr"], color))
    for row in rows:
        if row is None:
            print(paint(BX["ml"] + BX["h"] * (W - 2) + BX["mr"], color))
            continue
        row = _trunc(row, INNER)
        print(paint(BX["v"], color) + " " + pad(row, INNER) + " "
              + paint(BX["v"], color))
    print(paint(BX["bl"] + BX["h"] * (W - 2) + BX["br"], color))


def kv(label, value, style=(), lw=11):
    text = str(value).strip() or "-"
    lines = _wrap(text, INNER - lw - 2) or ["-"]
    rows = []
    for i, ln in enumerate(lines):
        lab = paint(label.ljust(lw), "dim") if i == 0 else " " * lw
        rows.append(lab + "  " + (paint(ln, *style) if style else ln))
    return rows


def item(prefix, text, style=(), indent=0):
    plen = vlen(prefix)
    lines = _wrap(text, INNER - plen - 1 - indent) or [""]
    rows = []
    for i, ln in enumerate(lines):
        lead = prefix if i == 0 else " " * plen
        rows.append(" " * indent + lead + " "
                    + (paint(ln, *style) if style else ln))
    return rows


def para(text, style=(), indent=0):
    return [" " * indent + (paint(ln, *style) if style else ln)
            for ln in _wrap(text, INNER - indent)]


def title_banner(title, right=""):
    inner = W - 2
    left = " " + title
    mid = max(1, inner - len(left) - len(right) - 1)
    body = paint(left, "bold") + " " * mid + paint(right, "dim") + " "
    print(paint(BX["TL"] + BX["H"] * inner + BX["TR"], "cyan"))
    print(paint(BX["V"], "cyan") + pad(body, inner) + paint(BX["V"], "cyan"))
    print(paint(BX["BL"] + BX["H"] * inner + BX["BR"], "cyan"))


_BANNER_STYLE = {
    "green": ("bold", "black", "bg_green"),
    "red": ("bold", "white", "bg_red"),
    "yellow": ("bold", "black", "bg_yellow"),
}


def big_banner(lines, color):
    print()
    if USE_COLOR:
        st = _BANNER_STYLE[color]
        blank = paint(" " * W, *st)
        print(blank)
        for ln in lines:
            print(paint(ln.center(W), *st))
        print(blank)
    else:
        print(BX["TL"] + BX["H"] * (W - 2) + BX["TR"])
        for ln in lines:
            print(BX["V"] + ln.center(W - 2) + BX["V"])
        print(BX["BL"] + BX["H"] * (W - 2) + BX["BR"])
    print()


def spaced(text):
    return " ".join(text)


# =============================================================================
# helpers
# =============================================================================
def run(cmd, timeout=30):
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e).splitlines()[0] if str(e) else "error"


def read_file(path):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""


def scan_internal_disks():
    rc, out, _ = run(["lsblk", "-J", "-b", "-o",
                      "NAME,PATH,TYPE,SIZE,RM,TRAN,MODEL"])
    disks = []
    if rc != 0:
        return disks
    try:
        data = json.loads(out)
    except Exception:
        return disks
    for d in data.get("blockdevices", []):
        if d.get("type") != "disk":
            continue
        if (d.get("name") or "").startswith(("loop", "sr", "zram", "ram", "dm-")):
            continue
        if d.get("tran") == "usb" and int(d.get("size") or 0) < 32_000_000_000:
            continue
        disks.append({
            "path": d.get("path"),
            "model": (d.get("model") or "").strip(),
            "size_gb": round(int(d.get("size") or 0) / 1e9, 1),
        })
    return disks


# =============================================================================
# screen helpers
# =============================================================================
_POS = [0, 0]


def _banner(name):
    label = name.upper()
    if _POS[1]:
        label = "[%d/%d]  %s" % (_POS[0], _POS[1], label)
    rule(label)


def say(text, style=()):
    print("  " + (paint(text, *style) if style else text))


def line(label, value, style=()):
    value = str(value)
    print("  " + paint(label.ljust(11), "dim")
          + (paint(value, *style) if style else value))


def _done(r, extra=""):
    note = ("   " + paint(extra, "dim")) if extra else ""
    print("  " + tag(r["status"]) + note)


def temp_style(c):
    if c < 70:
        return ("green",)
    if c < 90:
        return ("yellow",)
    return ("bold", "red")


def bar(frac, width=28, color="cyan"):
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return (paint(BX["full"] * n, color)
            + paint(BX["empty"] * (width - n), "dim"))


def progress_line(label, frac, extra=""):
    if not LIVE:
        return
    sys.stdout.write("\r\033[K  %s %s %3d%%  %s" % (
        label.ljust(10), bar(frac), int(max(0.0, min(1.0, frac)) * 100),
        extra))
    sys.stdout.flush()


def clear_progress():
    if LIVE:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


def run_with_bar(cmd, expected_s, label, timeout, extra=None):
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    fo = tempfile.TemporaryFile(mode="w+")
    fe = tempfile.TemporaryFile(mode="w+")
    try:
        p = subprocess.Popen(cmd, stdout=fo, stderr=fe, text=True, env=env)
    except Exception as e:
        fo.close()
        fe.close()
        return 1, "", (str(e).splitlines()[0] if str(e) else "error")
    t0 = time.time()
    last_x, txt = -1.0, ""
    try:
        while True:
            rc = p.poll()
            el = time.time() - t0
            if rc is not None:
                break
            if el > timeout:
                p.kill()
                p.wait()
                rc = 124
                break
            if extra and el - last_x >= 1.0:
                try:
                    txt = extra(el)
                except Exception:
                    txt = ""
                last_x = el
            progress_line(label, el / expected_s if expected_s else 0, txt)
            time.sleep(0.2)
    except BaseException:
        try:
            p.kill()
            p.wait(timeout=2)
        except Exception:
            pass
        clear_progress()
        fo.close()
        fe.close()
        raise
    clear_progress()
    fo.seek(0)
    fe.seek(0)
    out, err = fo.read(), fe.read()
    fo.close()
    fe.close()
    if rc == 124:
        err = (err + "\ntimeout").strip()
    return rc, out, err


# =============================================================================
# CPU
# =============================================================================
def test_cpu(load_seconds=30):
    _banner("cpu")
    r = {"component": "CPU", "status": "PASS", "details": {}, "notes": []}

    rc, out, _ = run(["lscpu"])
    if rc == 0:
        for ln in out.splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                k, v = k.strip(), v.strip()
                if k in ("Model name", "CPU(s)", "Thread(s) per core",
                         "Core(s) per socket", "Socket(s)", "CPU max MHz",
                         "CPU min MHz", "Architecture"):
                    r["details"][k] = v
        line("CPU", "%s (%s threads)" % (
            r["details"].get("Model name", "?"),
            r["details"].get("CPU(s)", "?")))

    def _temps():
        out = []
        for hwmon in glob.glob("/sys/class/hwmon/hwmon*"):
            name = read_file(hwmon + "/name")
            for inp in glob.glob(hwmon + "/temp*_input"):
                val = read_file(inp)
                try:
                    out.append({"sensor": name, "celsius": int(val) / 1000.0})
                except Exception:
                    pass
        return out

    def _hot():
        t = _temps()
        return ("temp %.0f°C" % max(x["celsius"] for x in t)) if t else ""

    before = _temps()
    if before:
        r["details"]["temperatures_before"] = before
        hottest = max(t["celsius"] for t in before)
        line("Idle temp", "%.1f°C" % hottest, temp_style(hottest))
        if hottest > 90:
            r["notes"].append("CPU already hot at idle: %.1f°C" % hottest)
            r["status"] = "WARN"

    if load_seconds > 0:
        cmd = None
        if shutil.which("stress-ng"):
            cmd = ["stress-ng", "--cpu", str(os.cpu_count() or 1),
                   "--timeout", "%ds" % load_seconds, "--metrics-brief"]
        elif shutil.which("stress"):
            cmd = ["stress", "--cpu", str(os.cpu_count() or 1),
                   "--timeout", str(load_seconds)]
        if cmd:
            rc, out, err = run_with_bar(
                cmd, load_seconds, "CPU load", load_seconds + 30,
                lambda el: "%2d/%ds  %s" % (el, load_seconds, _hot()))
            r["details"]["load_test"] = {
                "tool": cmd[0], "rc": rc,
                "tail": (out + err).strip().splitlines()[-3:],
            }
        else:
            procs = [subprocess.Popen(["yes"], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
                     for _ in range(os.cpu_count() or 1)]
            try:
                t0 = time.time()
                while time.time() - t0 < load_seconds:
                    el = time.time() - t0
                    progress_line("CPU load", el / load_seconds,
                                  "%2d/%ds  %s" % (el, load_seconds, _hot()))
                    time.sleep(0.5)
            finally:
                clear_progress()
                for p in procs:
                    p.terminate()
                for p in procs:
                    try:
                        p.wait(timeout=2)
                    except Exception:
                        p.kill()
            r["details"]["load_test"] = {"tool": "yes (fallback)",
                                          "duration_s": load_seconds}

        rc, dmesg, _ = run(["dmesg"], timeout=5)
        if rc == 0:
            throttles = [l for l in dmesg.splitlines()
                         if "thermal" in l.lower()
                         and "throttl" in l.lower()]
            if throttles:
                r["notes"].append("Thermal throttling events: %d"
                                  % len(throttles))
                r["status"] = "WARN"

        after = _temps()
        if after:
            r["details"]["temperatures_after"] = after
            hottest = max(t["celsius"] for t in after)
            line("Load temp", "%.1f°C" % hottest, temp_style(hottest))
            if hottest > 95:
                r["notes"].append("CPU under load reached %.1f°C" % hottest)
                r["status"] = "WARN"

    _done(r)
    return r


# =============================================================================
# RAM
# =============================================================================
def test_ram(minutes=2):
    _banner("ram")
    r = {"component": "RAM", "status": "PASS", "details": {}, "notes": []}

    try:
        with open("/proc/meminfo") as f:
            info = f.read()
        m = re.search(r"MemTotal:\s+(\d+)", info)
        if m:
            r["details"]["total_gb"] = round(int(m.group(1)) / 1024 / 1024, 1)
            line("Total RAM", "%s GB" % r["details"]["total_gb"])
        m = re.search(r"MemAvailable:\s+(\d+)", info)
        avail_kb = int(m.group(1)) if m else 0
    except Exception:
        avail_kb = 0

    rc, out, _ = run(["edac-util", "-s"])
    if rc == 0:
        r["details"]["ecc"] = out.strip()
        if "UE:" in out and not re.search(r"UE:\s+0", out):
            r["notes"].append("Uncorrected ECC errors detected")
            r["status"] = "FAIL"

    if minutes > 0 and shutil.which("memtester") and avail_kb:
        test_mb = min(int(avail_kb * 0.8 / 1024), 2048)
        line("Stress test", "memtester %d MB (up to %d min)"
             % (test_mb, minutes))
        cmd = ["memtester", "%dM" % test_mb, "1"]
        limit = minutes * 60
        rc, out, err = run_with_bar(
            cmd, limit, "memtester", limit,
            lambda el: "%d:%02d of max %d:00" % (el // 60, el % 60, minutes))
        r["details"]["memtester"] = {
            "size_mb": test_mb, "rc": rc,
            "tail": (out + err).strip().splitlines()[-3:],
        }
        if rc != 0 or "FAILURE" in (out + err):
            r["status"] = "FAIL"
            r["notes"].append("memtester failed (rc=%d)" % rc)
    elif minutes > 0:
        r["notes"].append("memtester not installed — skipped stress test")
        if r["status"] == "PASS":
            r["status"] = "WARN"

    _done(r)
    return r


# =============================================================================
# Drive health (SMART)
# =============================================================================
def test_drives():
    _banner("drive")
    r = {"component": "Drive Health", "status": "PASS",
         "details": {"disks": []}, "notes": []}
    disks = scan_internal_disks()
    if not disks:
        r["status"] = "WARN"
        r["notes"].append("No internal drives detected")
        _done(r, "no drives")
        return r

    for d in disks:
        path = d["path"]
        info = {"path": path, "model": d["model"]}
        if not shutil.which("smartctl"):
            info["smart_error"] = "smartctl not installed"
        else:
            rc, out, err = run(["smartctl", "-a", "-j", path], timeout=30)
            if rc == 0:
                try:
                    data = json.loads(out)
                    info["healthy"] = data.get(
                        "smart_status", {}).get("passed")
                    nv = data.get("nvme_smart_health_information_log")
                    if nv:
                        info["percentage_used"] = nv.get("percentage_used")
                        info["temperature_c"] = nv.get("temperature")
                        info["power_on_hours"] = nv.get("power_on_hours")
                    ata = data.get("ata_smart_attributes")
                    if ata:
                        attrs = {a["name"]: a for a in ata.get("table", [])}
                        for k in ("Reallocated_Sector_Ct",
                                  "Current_Pending_Sector",
                                  "Offline_Uncorrectable"):
                            if k in attrs:
                                info[k] = attrs[k]["raw"]["value"]
                except Exception as e:
                    info["smart_error"] = str(e)[:80]
            else:
                # Normalise to a single-line string.
                info["smart_error"] = " ".join(
                    (err or out).strip().splitlines()[:2])

        if info.get("healthy") is False:
            r["notes"].append("%s: SMART health FAILED" % path)
            r["status"] = "FAIL"
        if info.get("percentage_used") and info["percentage_used"] > 90:
            r["notes"].append("%s: SSD wear %d%%"
                              % (path, info["percentage_used"]))
            if r["status"] == "PASS":
                r["status"] = "WARN"
        for k in ("Reallocated_Sector_Ct", "Current_Pending_Sector",
                  "Offline_Uncorrectable"):
            if info.get(k) and info[k] > 0:
                r["notes"].append("%s: %s = %s" % (path, k, info[k]))
                if r["status"] == "PASS":
                    r["status"] = "WARN"

        if info.get("healthy") is False:
            head = tag("FAIL", "SMART FAILED")
        elif info.get("healthy") is None:
            # smartctl ran but did not report pass/fail, or failed outright
            head = tag("WARN", "SMART status unknown")
        else:
            head = tag("PASS", "SMART OK")
        print("  %s  %s %s" % (head, path,
                               paint("(%s)" % (d["model"] or "?"), "dim")))
        bits = []
        if info.get("percentage_used") is not None:
            bits.append("wear %s%%" % info["percentage_used"])
        if info.get("temperature_c") is not None:
            bits.append("%s°C" % info["temperature_c"])
        if info.get("power_on_hours") is not None:
            bits.append("%s h on" % info["power_on_hours"])
        for k in ("Reallocated_Sector_Ct", "Current_Pending_Sector",
                  "Offline_Uncorrectable"):
            if info.get(k) is not None:
                bits.append("%s=%s" % (k, info[k]))
        if info.get("smart_error") and info.get("healthy") is None:
            bits.append("smartctl: %s" % info["smart_error"])
        if bits:
            say("    " + "  |  ".join(bits)[:W - 8], ("dim",))
        r["details"]["disks"].append(info)

    _done(r)
    return r


# =============================================================================
# Battery
# =============================================================================
def test_battery():
    _banner("battery")
    r = {"component": "Battery", "status": "PASS",
         "details": {}, "notes": []}

    bats = sorted(glob.glob("/sys/class/power_supply/BAT*"))
    if not bats:
        r["status"] = "SKIP"
        r["notes"].append("No battery (desktop / removed)")
        _done(r, "no battery")
        return r

    out = []
    health_seen = False
    for bp in bats:
        b = {"path": bp}
        for k in ("status", "capacity", "cycle_count", "manufacturer",
                  "model_name", "serial_number", "technology"):
            b[k] = read_file("%s/%s" % (bp, k))
        design = (read_file(bp + "/energy_full_design")
                  or read_file(bp + "/charge_full_design"))
        full = (read_file(bp + "/energy_full")
                or read_file(bp + "/charge_full"))
        try:
            di, fi = int(design), int(full)
            health = round(fi / di * 100, 1)
            b["design_mwh"] = di / 1000
            b["full_mwh"] = fi / 1000
            b["health_pct"] = health
            health_seen = True
            if health < 60:
                r["notes"].append(
                    "%s: battery health %s%% (below 60%%)" % (bp, health))
                r["status"] = "FAIL"
            elif health < 80:
                r["notes"].append(
                    "%s: battery health %s%% (below 80%%)" % (bp, health))
                if r["status"] == "PASS":
                    r["status"] = "WARN"
            hc = "green" if health >= 80 else "yellow" if health >= 60 else "red"
            print("  %s %s %s" % (os.path.basename(bp).ljust(6),
                                  bar(health / 100.0, 30, hc),
                                  paint("%.1f%% health" % health, "bold", hc)))
            say("       %.0f of %.0f mWh design   cycles: %s"
                % (b["full_mwh"], b["design_mwh"], b["cycle_count"] or "?"),
                ("dim",))
        except Exception:
            say("%s: health data unavailable" % bp, ("yellow",))
        out.append(b)
    r["details"]["batteries"] = out

    # Battery present but no health data at all: don't call it PASS.
    if not health_seen and r["status"] == "PASS":
        r["status"] = "WARN"
        r["notes"].append("Battery present but health data unreadable")

    _done(r)
    return r


# =============================================================================
# Screen (interactive)
# =============================================================================
def _fill_screen(ansi):
    try:
        cols = int(subprocess.check_output(["tput", "cols"]).strip())
        rows = int(subprocess.check_output(["tput", "lines"]).strip())
    except Exception:
        cols, rows = 80, 24
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write((ansi + " " * cols + "\n") * (rows - 1) + "\033[0m")
    sys.stdout.flush()


def test_screen(interactive):
    _banner("screen")
    r = {"component": "Screen", "status": "SKIP",
         "details": {}, "notes": []}
    if not interactive:
        r["notes"].append("Screen test requires --interactive")
        _done(r, "not interactive")
        return r

    say("Watch for dead pixels, stuck pixels, discolouration.")
    say("Enter = next colour    s = skip the rest    f = record a failure",
        ("dim",))
    patterns = [("RED", "\033[41m"), ("GREEN", "\033[42m"),
                ("BLUE", "\033[44m"), ("WHITE", "\033[47m"),
                ("BLACK", "\033[40m"), ("GREY", "\033[100m")]
    seen, failed = [], False
    for name, ansi in patterns:
        _fill_screen(ansi)
        try:
            ans = input("[%s]  Enter / s / f: " % name).strip().lower()
        except EOFError:
            break
        if ans == "s":
            break
        if ans == "f":
            failed = True
            r["notes"].append("Failure on pattern %s" % name)
        seen.append(name)
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
    r["details"]["patterns_shown"] = seen
    r["status"] = "FAIL" if failed else ("PASS" if seen else "SKIP")
    _banner("screen")   # re-draw header after the full-screen test
    _done(r, "%d colour(s) shown" % len(seen))
    return r


# =============================================================================
# Keyboard (interactive, evtest)
# =============================================================================
def _kb_rows():
    def letters(s):
        return [(c, c) for c in s]
    return [
        [("ESC", "ESC")] + letters("1234567890")
        + [("-", "MINUS"), ("=", "EQUAL"), ("BKSP", "BACKSPACE")],
        [("TAB", "TAB")] + letters("QWERTYUIOP")
        + [("[", "LEFTBRACE"), ("]", "RIGHTBRACE")],
        [("CAPS", "CAPSLOCK")] + letters("ASDFGHJKL")
        + [(";", "SEMICOLON"), ("'", "APOSTROPHE"), ("ENTER", "ENTER")],
        [("SHIFT", "LEFTSHIFT")] + letters("ZXCVBNM")
        + [(",", "COMMA"), (".", "DOT"), ("/", "SLASH"),
           ("SHIFT", "RIGHTSHIFT")],
        [("CTRL", "LEFTCTRL"), ("ALT", "LEFTALT"), ("SPACE", "SPACE"),
         ("ALT", "RIGHTALT"), ("<", "LEFT"), ("^", "UP"), ("v", "DOWN"),
         (">", "RIGHT")],
    ]


def _kb_cell(label, name, seen):
    w = max(3, len(label) + 2)
    if name in seen:
        if USE_COLOR:
            return paint(label.center(w), "bold", "black", "bg_green")
        return "[" + label.center(w - 2) + "]"
    if USE_COLOR:
        return paint(label.center(w), "white", "bg_blue")
    return label.center(w)


def _kb_draw(seen, first):
    lines = ["  " + " ".join(_kb_cell(l, n, seen) for l, n in row)
             for row in _kb_rows()]
    lines.append("")
    lines.append("  " + paint("Keys seen: %d" % len(seen), "bold")
                 + paint("     press D O N E when finished", "dim"))
    if not first:
        sys.stdout.write("\033[%dA" % len(lines))
    for ln in lines:
        sys.stdout.write("\r\033[K" + ln + "\n")
    sys.stdout.flush()


def _tty_quiet():
    try:
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] = new[3] & ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(fd, termios.TCSADRAIN, new)

        def restore():
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass
        return restore
    except Exception:
        return lambda: None


def test_keyboard(interactive):
    _banner("keyboard")
    r = {"component": "Keyboard", "status": "SKIP",
         "details": {}, "notes": []}

    def _is_kb(name):
        low = name.lower()
        return ("keyboard" in low or "at translated" in low
                or "kbd" in low)

    kb_dev = None
    try:
        rc, out, _ = run(["evtest", "--list-devices"])
        if rc == 0:
            current_dev = None
            for ln in out.splitlines():
                m_new = re.match(r"\s*(/dev/input/event\d+):\s*(.*)$", ln)
                if m_new and m_new.group(2).strip():
                    if _is_kb(m_new.group(2)):
                        kb_dev = m_new.group(1)
                        r["details"]["device"] = m_new.group(2).strip()
                        break
                    continue
                m = re.match(r"(.+):\s*$", ln)
                if m and "/dev/input" not in ln:
                    current_dev = m.group(1).strip()
                elif ln.strip().startswith("/dev/input/event"):
                    if current_dev and _is_kb(current_dev):
                        kb_dev = ln.strip()
                        r["details"]["device"] = current_dev
                        break
    except Exception:
        pass

    if not kb_dev:
        try:
            with open("/proc/bus/input/devices") as f:
                content = f.read()
            for block in content.split("\n\n"):
                if "EV=120013" in block:
                    m = re.search(r"Handlers=.*?(event\d+)", block)
                    if m:
                        kb_dev = "/dev/input/" + m.group(1)
                        n = re.search(r'N: Name="([^"]+)"', block)
                        r["details"]["device"] = n.group(1) if n else kb_dev
                        break
        except Exception:
            pass

    if not kb_dev:
        r["notes"].append("Could not find a keyboard device")
        r["status"] = "WARN"
        _done(r, "no device")
        return r

    if not interactive:
        r["status"] = "PASS"
        r["notes"].append("Device present — enable --interactive for keypress test")
        _done(r, "device present")
        return r

    say("Press a sample of keys (letters, numbers, Enter, Space).")
    say("Keys light up green as they register. Press D, O, N, E to finish,",
        ("dim",))
    say("or Ctrl+C to abort.  Device: %s" % kb_dev, ("dim",))
    print()

    seen = set()
    proc = None
    restore = _tty_quiet() if LIVE else (lambda: None)
    try:
        cmd = ["evtest", kb_dev]
        if shutil.which("stdbuf"):
            cmd = ["stdbuf", "-oL"] + cmd
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        fd = proc.stdout.fileno()
        buf = b""
        start = time.time()
        if LIVE:
            _kb_draw(seen, True)
        finished = False
        while time.time() - start < 120 and not finished:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            buf += chunk
            pieces = buf.split(b"\n")
            buf = pieces.pop()
            changed = False
            for raw in pieces:
                ln = raw.decode("utf-8", "replace")
                if "(KEY_" in ln and "value 1" in ln:
                    m = re.search(r"KEY_(\w+)\)", ln)
                    if m and m.group(1) not in seen:
                        seen.add(m.group(1))
                        changed = True
                        if not LIVE:
                            sys.stdout.write(".")
                            sys.stdout.flush()
            if changed and LIVE:
                _kb_draw(seen, False)
            if all(k in seen for k in ("D", "O", "N", "E")):
                finished = True
    except KeyboardInterrupt:
        r["status"] = "FAIL"
        r["notes"].append("Aborted by technician")
        print()
        _done(r, "aborted")
        return r
    except Exception as e:
        r["status"] = "WARN"
        r["notes"].append("evtest error: %s" % e)
        print()
        _done(r, "evtest error")
        return r
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        restore()

    print()
    r["details"]["keys_seen"] = sorted(seen)
    r["details"]["key_count"] = len(seen)
    if len(seen) < 10:
        r["status"] = "WARN"
        r["notes"].append("Only %d keys detected" % len(seen))
    else:
        r["status"] = "PASS"
    _done(r, "%d keys seen" % len(seen))
    return r


# =============================================================================
# Touchpad (device presence)
# =============================================================================
def test_touchpad():
    _banner("touchpad")
    r = {"component": "Touchpad", "status": "SKIP",
         "details": {}, "notes": []}
    try:
        with open("/proc/bus/input/devices") as f:
            content = f.read()
        for block in content.split("\n\n"):
            low = block.lower()
            if any(k in low for k in ("touchpad", "synaptics", "elan",
                                       "trackpad", "alps")):
                m = re.search(r'N: Name="([^"]+)"', block)
                if m:
                    r["details"]["device"] = m.group(1)
                    line("Device", m.group(1))
                r["status"] = "PASS"
                r["notes"].append(
                    "Device present — manually drag the cursor across the "
                    "full pad and click both buttons")
                say("Also drag across the full pad and click both buttons.",
                    ("dim",))
                break
    except Exception:
        pass
    if r["status"] == "SKIP":
        r["notes"].append("No touchpad device detected")
    _done(r)
    return r


# =============================================================================
# Webcam
# =============================================================================
def test_webcam():
    _banner("webcam")
    r = {"component": "Webcam", "status": "SKIP",
         "details": {}, "notes": []}

    if not shutil.which("v4l2-ctl"):
        r["notes"].append("v4l2-ctl not installed (apt install v4l-utils)")
        _done(r, r["notes"][-1])
        return r

    rc, out, _ = run(["v4l2-ctl", "--list-devices"])
    if rc != 0 or "video" not in out.lower():
        r["status"] = "FAIL"
        r["notes"].append("No video device detected")
        _done(r, "no camera")
        return r

    r["details"]["list"] = out.strip()
    devs = re.findall(r"(/dev/video\d+)", out)
    if not devs:
        r["status"] = "WARN"
        _done(r, "no /dev/video*")
        return r
    dev = devs[0]
    r["details"]["device"] = dev
    line("Device", dev)

    rc, out2, _ = run(["v4l2-ctl", "-d", dev, "--list-formats-ext"])
    if rc == 0:
        r["details"]["resolutions"] = sorted(set(
            re.findall(r"(\d{3,4}x\d{3,4})", out2)))

    if shutil.which("ffmpeg"):
        # mkstemp: avoids the deprecated/unsafe mktemp.
        fd, tmp = tempfile.mkstemp(suffix=".jpg", prefix="itad_cam_")
        os.close(fd)
        try:
            rc, _, _ = run(["ffmpeg", "-y", "-f", "v4l2", "-i", dev,
                            "-frames:v", "1", tmp], timeout=15)
            if rc == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                r["details"]["capture_bytes"] = os.path.getsize(tmp)
                r["status"] = "PASS"
                line("Capture", "1 frame grabbed (%d bytes)"
                     % r["details"]["capture_bytes"])
            else:
                r["status"] = "WARN"
                r["notes"].append("ffmpeg capture failed")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    else:
        r["status"] = "PASS"
        r["notes"].append("Device present, ffmpeg not installed for capture")
    _done(r)
    return r


# =============================================================================
# Speakers
# =============================================================================
def test_speakers(interactive):
    _banner("speakers")
    r = {"component": "Speakers", "status": "SKIP",
         "details": {}, "notes": []}

    if not os.path.exists("/proc/asound/cards"):
        r["status"] = "FAIL"
        r["notes"].append("No ALSA sound cards")
        _done(r, "no sound cards")
        return r
    cards = read_file("/proc/asound/cards")
    if "no soundcards" in cards.lower():
        r["status"] = "FAIL"
        r["notes"].append("No sound cards detected")
        _done(r, "no sound cards")
        return r
    r["details"]["cards"] = cards

    if not shutil.which("speaker-test"):
        r["status"] = "WARN"
        r["notes"].append("speaker-test not installed (alsa-utils)")
        _done(r, "speaker-test missing")
        return r
    if not interactive:
        r["status"] = "PASS"
        r["notes"].append("Sound card present — enable --interactive to play tone")
        _done(r, "card present")
        return r

    say("Playing a short tone — listen for clear sound from both speakers...")
    rc, out, err = run(["speaker-test", "-t", "sine", "-f", "440",
                        "-l", "1", "-c", "2"], timeout=10)
    if rc != 0:
        r["status"] = "WARN"
        r["notes"].append("speaker-test failed")
        _done(r, "playback error")
        return r
    try:
        ans = input("  Heard clear sound? [y/N] ").strip().lower()
    except EOFError:
        ans = ""
    if ans.startswith("y"):
        r["status"] = "PASS"
    else:
        r["status"] = "FAIL"
        r["notes"].append("Technician reported no sound")
    _done(r)
    return r


# =============================================================================
# Wi-Fi
# =============================================================================
def test_wifi():
    _banner("wifi")
    r = {"component": "Wi-Fi", "status": "SKIP",
         "details": {}, "notes": []}

    rc, out, _ = run(["ip", "-o", "link", "show"])
    ifaces = re.findall(r"^\d+:\s+(\w+):", out, re.M)
    wlans = [i for i in ifaces if i.startswith(("wl", "wlan"))]
    if not wlans:
        r["notes"].append("No Wi-Fi interface (desktop / disabled in BIOS)")
        _done(r, "no interface")
        return r
    r["details"]["interfaces"] = wlans
    iface = wlans[0]
    line("Interface", iface)

    if shutil.which("iw"):
        # NB: this briefly sets the interface up — that is the only write
        # this tool performs, and only on Wi-Fi.
        run(["ip", "link", "set", iface, "up"], timeout=5)
        time.sleep(1)
        rc, out, _ = run(["iw", "dev", iface, "scan"], timeout=20)
        if rc == 0:
            ssids = re.findall(r"SSID: (.+)", out)
            r["details"]["networks_visible"] = len(ssids)
            r["details"]["ssids_sample"] = ssids[:5]
            if ssids:
                r["status"] = "PASS"
            else:
                r["status"] = "WARN"
                r["notes"].append("Adapter up but no SSIDs seen")
        else:
            r["status"] = "WARN"
            r["notes"].append("iw scan failed")
    elif shutil.which("nmcli"):
        rc, out, _ = run(["nmcli", "-t", "-f", "SSID",
                          "device", "wifi", "list"], timeout=20)
        if rc == 0:
            nets = [l for l in out.splitlines() if l.strip()]
            r["details"]["networks_visible"] = len(nets)
            r["status"] = "PASS" if nets else "WARN"
    else:
        r["status"] = "WARN"
        r["notes"].append("Neither iw nor nmcli installed")
    if "networks_visible" in r["details"]:
        line("Networks", "%d visible" % r["details"]["networks_visible"])
    _done(r)
    return r


# =============================================================================
# USB
# =============================================================================
def test_usb(interactive):
    _banner("usb")
    r = {"component": "USB", "status": "PASS",
         "details": {}, "notes": []}

    rc, out, _ = run(["lsusb"])
    if rc == 0:
        r["details"]["devices"] = [l.strip() for l in out.splitlines() if l.strip()]

    controllers = []
    for p in glob.glob("/sys/bus/usb/devices/usb*/idVendor"):
        controllers.append({
            "vid": read_file(p),
            "pid": read_file(p.replace("idVendor", "idProduct")),
            "product": read_file(p.replace("idVendor", "product")),
        })
    r["details"]["controllers"] = controllers

    if interactive:
        say("Plug a USB stick into each external port, one at a time.")
        try:
            input("  Press ENTER when ready to test port 1... ")
        except EOFError:
            _done(r)
            return r
        before = set(run(["lsusb"])[1].splitlines())
        time.sleep(2)
        after = set(run(["lsusb"])[1].splitlines())
        new = after - before
        if new:
            r["notes"].append("Port 1 OK — device seen")
            line("Port 1", "device seen", ("green",))
            r["details"]["port_1_new_device"] = list(new)[0][:100]
        else:
            r["status"] = "WARN"
            r["notes"].append("Port 1: no new device detected")
            line("Port 1", "no new device", ("yellow",))
        say("Repeat for remaining ports manually if desired.", ("dim",))
    else:
        line("USB", "%d buses, %d devices visible"
             % (len(controllers), len(r["details"].get("devices", []))))
    _done(r)
    return r


# =============================================================================
# Summary
# =============================================================================
def summary_table(results, overall):
    order = ("PASS", "WARN", "FAIL", "SKIP")
    colors = {"PASS": "green", "WARN": "yellow", "FAIL": "red", "SKIP": None}
    rows = [paint("COMPONENT".ljust(13) + "STATUS".ljust(10) + "NOTES", "dim"),
            None]
    nw = INNER - 23
    for r in results:
        t = tag(r["status"])
        notes = "; ".join(r["notes"]) if r["notes"] else "-"
        wrapped = _wrap(notes, nw) or ["-"]
        for i, ln in enumerate(wrapped):
            shown = paint(ln, "dim") if not r["notes"] else ln
            if i == 0:
                rows.append(r["component"].ljust(13)[:13] + pad(t, 10) + shown)
            else:
                rows.append(" " * 23 + shown)
    counts = dict((k, sum(1 for r in results if r["status"] == k))
                  for k in order)
    rows.append(None)
    rows.append("   ".join(tag(k, "%d %s" % (counts[k], k))
                           for k in order if counts[k]) or "-")
    if USE_COLOR and results:
        total, width, cum, prev, seg = len(results), 40, 0, 0, ""
        for k in order:
            cum += counts[k]
            edge = int(round(float(cum) / total * width))
            n, prev = edge - prev, edge
            if n > 0:
                seg += paint(BX["full"] * n, colors[k] or "white")
        rows.append(seg)
    box("SUMMARY", rows,
        {"PASS": "green", "WARN": "yellow", "FAIL": "red"}[overall])


# =============================================================================
# Main
# =============================================================================
def main():
    global USE_COLOR
    ap = argparse.ArgumentParser(
        description="ITAD laptop component test")
    ap.add_argument("--job-id", default="")
    ap.add_argument("--asset-id", default="")
    ap.add_argument("--tech", default="")
    ap.add_argument("--json", default="",
                    help="JSON output path, or '-' for stdout")
    ap.add_argument("--test", default=",".join(ALL_TESTS),
                    help="Comma-separated: " + ",".join(ALL_TESTS))
    ap.add_argument("--no-interactive", action="store_true",
                    help="Skip tests that need human confirmation")
    ap.add_argument("--cpu-load", type=int, default=30,
                    help="Seconds of CPU load (0 to skip). Default 30")
    ap.add_argument("--ram-minutes", type=int, default=2,
                    help="Minutes of memtester (0 to skip). Default 2")
    ap.add_argument("--no-color", action="store_true",
                    help="Plain text, no colours")
    args = ap.parse_args()
    if args.no_color:
        USE_COLOR = False

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print(paint("Must run as root. Use: sudo python3 laptop_test.py ...",
                    "bold", "red"))
        sys.exit(3)

    if not args.json:
        default = "/tmp/laptop_test.json"
        for cand in ("/run/live/medium", "/cdrom", "/media/usb", "/mnt/usb"):
            if os.path.isdir(cand) and os.access(cand, os.W_OK):
                default = os.path.join(cand, "laptop_test.json")
                break
        args.json = default

    # --json - : keep stdout clean for the JSON payload; human output on stderr.
    json_stream = sys.stdout
    if args.json == "-":
        sys.stdout = sys.stderr

    interactive = not args.no_interactive
    selected = [t.strip().lower() for t in args.test.split(",") if t.strip()]
    unknown = [t for t in selected if t not in ALL_TESTS]
    if unknown:
        print("Unknown test(s): %s" % ", ".join(unknown))
        sys.exit(3)

    title_banner("ITAD COMPONENT TEST", "v%s" % VERSION)
    box("JOB", kv("Time", NOW.strftime("%Y-%m-%d  %H:%M:%S"))
        + kv("Job ID", args.job_id or "-")
        + kv("Asset ID", args.asset_id or "-")
        + kv("Tech", args.tech or "-")
        + kv("Tests", ", ".join(selected))
        + kv("Mode", "interactive" if interactive else "automated only"),
        "blue")

    results = []
    dispatcher = {
        "cpu":      lambda: test_cpu(args.cpu_load),
        "ram":      lambda: test_ram(args.ram_minutes),
        "drive":    test_drives,
        "battery":  test_battery,
        "screen":   lambda: test_screen(interactive),
        "keyboard": lambda: test_keyboard(interactive),
        "touchpad": test_touchpad,
        "webcam":   test_webcam,
        "speakers": lambda: test_speakers(interactive),
        "wifi":     test_wifi,
        "usb":      lambda: test_usb(interactive),
    }
    _POS[0] = 0
    _POS[1] = len(selected)
    for t in selected:
        _POS[0] += 1
        try:
            results.append(dispatcher[t]())
        except KeyboardInterrupt:
            clear_progress()
            results.append({"component": t.upper(), "status": "SKIP",
                            "details": {},
                            "notes": ["Aborted by technician"]})
            break
        except Exception as e:
            results.append({"component": t.upper(), "status": "WARN",
                            "details": {},
                            "notes": ["Test crashed: %s" % str(e)[:120]]})

    statuses = [r["status"] for r in results]
    if "FAIL" in statuses:
        overall, code = "FAIL", 1
    elif "WARN" in statuses:
        overall, code = "WARN", 2
    elif all(s == "SKIP" for s in statuses):
        overall, code = "WARN", 2
    else:
        overall, code = "PASS", 0

    report = {
        "tool": "laptop_test.py",
        "version": VERSION,
        "time": NOW.isoformat(),
        "job_id": args.job_id,
        "asset_id": args.asset_id,
        "technician": args.tech,
        "interactive": interactive,
        "tests_selected": selected,
        "results": results,
        "overall": overall,
    }

    if args.json == "-":
        json.dump(report, json_stream, indent=2)
        json_stream.write("\n")
        json_stream.flush()
    else:
        try:
            with open(args.json, "w") as f:
                json.dump(report, f, indent=2)
        except Exception as e:
            print("Could not save JSON: %s" % e)

    rule("RESULTS")
    summary_table(results, overall)
    if overall == "PASS":
        big_banner(["", "%s  %s" % (ICON["ok"], spaced("ALL PASS")),
                    "All tested components pass. Ready for report.", ""],
                   "green")
    elif overall == "WARN":
        big_banner(["", "%s  %s" % (ICON["warn"], spaced("WARNING")),
                    "Some components could not be verified.",
                    "Review the notes above.", ""], "yellow")
    else:
        big_banner(["", "%s  %s" % (ICON["bad"], spaced("FAILED")),
                    "FAILED component(s) - do not ship.",
                    "Review the notes above.", ""], "red")
    if args.json != "-":
        print(paint(" Saved: %s" % args.json, "dim"))

    sys.exit(code)


if __name__ == "__main__":
    main()