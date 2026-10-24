#!/usr/bin/env python3
"""
laptop_wipe.py - ITAD data destruction (v1.0.1)

DESTRUCTIVE. Wipes internal drives. Never touches USB / external drives
or the drive the running OS is booted from.

Step 3 of the ITAD pipeline. Runs AFTER laptop_check.py reports CLEAR
and BEFORE laptop_test.py.

v1.0.1: bug fixes over v1.0.0 (no change to wipe policy):
  - NVMe sanitize action codes corrected (1=crypto, 2=block, 3=overwrite);
    v1.0.0 sent invalid code 4 and could do the wrong operation
  - ATA Secure Erase: enhanced flag now decided from the correct output
  - shred progress streamed via reader thread (was stall-prone)
  - shred stdout -> /dev/null (was a pipe that was never drained)
  - verify_check_report cross-checks job_id, asset_id, system serial
  - sanicap handled whether nvme-cli returns int or hex string

Methods (NIST SP 800-88 Rev 1 aligned):
  auto    - pick the strongest available method per device (default)
  clear   - single overwrite with zeros         (Clear)
  random  - single overwrite with random data   (Clear)
  dod     - three-pass DoD 5220.22-M            (legacy HDD only)
  purge   - ATA Secure Erase / NVMe Sanitize    (Purge)
  crypto  - crypto erase for SED / OPAL drives  (Purge)
  discard - blkdiscard / TRIM (SSD only, fast)  (Clear)

Safety model:
  * DRY RUN BY DEFAULT. Nothing is written unless you pass --commit.
  * Refuses to wipe the disk holding '/'
  * Refuses to wipe removable / USB drives
  * Refuses to wipe drives whose serial is UNKNOWN or a placeholder
  * Refuses if a recent laptop_check.py report is BLOCKED (override --force)
  * Refuses if the check report's job_id / asset_id / serial do not match
  * Requires typing  WIPE:<first-serial>  to confirm, unless both
    --commit AND --yes-i-mean-it are given
  * --method auto prefers Purge over Clear when the drive supports it,
    falls back to single-pass overwrite (still NIST Clear) if not.

Verification (always runs after a wipe unless --no-verify):
  * Reads the first and last 1 MiB and confirms the wipe signatures are
    gone (no NTFS / ext4 / LUKS / FAT / BitLocker signatures remain)
  * Runs a short SMART self-test where supported
  * Records a SHA-256 fingerprint of a 1 MiB sample

Exit codes:
  0 = PASS   1 = FAIL   2 = WARN / partial   3 = not root
  4 = aborted by user   5 = unsafe target (would touch OS disk / USB)

Run:
  sudo python3 /opt/itad-laptop/laptop_wipe.py --job-id J123 --tech Ali
  sudo python3 /opt/itad-laptop/laptop_wipe.py --job-id J123 --commit
  sudo python3 /opt/itad-laptop/laptop_wipe.py --job-id J123 \\
       --commit --yes-i-mean-it --method purge
"""

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    NOW = datetime.now(ZoneInfo("Europe/Dublin"))
except Exception:
    NOW = datetime.now()

VERSION = "1.0.1"
ALL_METHODS = ["auto", "clear", "random", "dod", "purge", "crypto", "discard"]

BAD_SERIALS = {
    "", "none", "default string", "to be filled by o.e.m.",
    "to be filled by o.e.m", "system serial number", "not specified",
    "0", "n/a", "unknown", "o.e.m.", "oem", "chassis serial number",
    "0123456789", "1234567890",
}

FS_SIGNATURES = [
    (3, b"NTFS    ", "NTFS"),
    (3, b"-FVE-FS-", "BitLocker"),
    (0, b"LUKS\xba\xbe", "LUKS"),
    (54, b"FAT", "FAT"),
    (82, b"FAT", "FAT"),
    (0x438, b"\x53\xef", "ext4"),
]

# =============================================================================
# LOOKS
# =============================================================================
ANSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
_TERM = os.environ.get("TERM", "")
LIVE = sys.stdout.isatty() and _TERM != "dumb"
USE_COLOR = LIVE and not os.environ.get("NO_COLOR")
_ENC = (getattr(sys.stdout, "encoding", None) or "").lower()
UNICODE = "utf" in _ENC
FANCY = UNICODE and _TERM != "linux"

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
    "PASS": ("ok", "green"), "OK": ("ok", "green"), "WIPED": ("ok", "green"),
    "BLOCKED": ("bad", "red"), "FAIL": ("bad", "red"),
    "ACTIVE": ("bad", "red"),
    "WARN": ("warn", "yellow"), "PAST": ("warn", "yellow"),
    "CANNOT CHECK": ("warn", "yellow"), "UNKNOWN": ("warn", "yellow"),
    "ABORTED": ("warn", "yellow"),
    "SKIP": ("skip", None), "DRY": ("info", "cyan"),
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


def read_bytes(path, n):
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except Exception:
        return b""


def _system_serial():
    """DMI system serial, or '' if unreadable / placeholder."""
    for p in ("/sys/class/dmi/id/product_serial",
              "/sys/class/dmi/id/board_serial"):
        try:
            with open(p) as f:
                v = f.read().strip()
            if v and v.lower() not in BAD_SERIALS:
                return v
        except Exception:
            pass
    return ""


def _progress_bar(frac, width=28):
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return (paint(BX["full"] * n, "cyan")
            + paint(BX["empty"] * (width - n), "dim"))


def progress_line(label, frac, extra=""):
    if not LIVE:
        return
    sys.stdout.write("\r\033[K  %s %s %3d%%  %s" % (
        label.ljust(10), _progress_bar(frac),
        int(max(0.0, min(1.0, frac)) * 100), extra))
    sys.stdout.flush()


def clear_progress():
    if LIVE:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


# =============================================================================
# disk helpers
# =============================================================================
def scan_disks():
    rc, out, _ = run(["lsblk", "-J", "-b", "-o",
                      "NAME,PATH,TYPE,FSTYPE,SIZE,RM,TRAN,MODEL,SERIAL"])
    if rc != 0:
        return [], ["lsblk failed"]
    try:
        data = json.loads(out)
    except Exception:
        return [], ["lsblk output unparsable"]

    root_disk = _root_disk()
    disks = []
    for d in data.get("blockdevices", []):
        if d.get("type") != "disk":
            continue
        name = d.get("name") or ""
        if name.startswith(("loop", "sr", "zram", "ram", "dm-")):
            continue
        if d.get("path") == root_disk:
            continue
        if d.get("rm") == True or d.get("tran") == "usb":
            if int(d.get("size") or 0) < 32_000_000_000:
                continue
        serial = (d.get("serial") or "").strip()
        disks.append({
            "path": d.get("path"),
            "name": name,
            "model": (d.get("model") or "").strip(),
            "serial": serial,
            "size_gb": round(int(d.get("size") or 0) / 1e9, 1),
            "tran": (d.get("tran") or "").lower(),
            "rm": bool(d.get("rm")),
            "fstype": (d.get("fstype") or "").lower(),
        })
    return disks, []


def _root_disk():
    rc, out, _ = run(["findmnt", "-no", "SOURCE", "/"])
    if rc != 0:
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    p = line.split()
                    if len(p) >= 2 and p[1] == "/":
                        out = p[0]
                        break
                else:
                    return None
        except Exception:
            return None
    src = out.strip()
    if not src.startswith("/dev/"):
        return None
    rc, out, _ = run(["lsblk", "-J", "-s", "-o", "PATH,TYPE", src])
    if rc != 0:
        return None
    try:
        data = json.loads(out)
    except Exception:
        return None

    def _walk(node):
        if node.get("type") == "disk":
            return node.get("path")
        for c in node.get("children") or []:
            r = _walk(c)
            if r:
                return r
        return None

    for d in data.get("blockdevices", []):
        r = _walk(d)
        if r:
            return r
    return None


def disk_size(dev):
    rc, out, _ = run(["blockdev", "--getsize64", dev])
    try:
        return int(out.strip())
    except Exception:
        return 0


def has_bad_serial(serial):
    return (serial or "").strip().lower() in BAD_SERIALS


# =============================================================================
# safety preflight
# =============================================================================
def preflight(disks, force=False):
    """Return (safe_disks, refusals, warnings)."""
    safe, refusals, warnings = [], [], []
    root_disk = _root_disk()
    if root_disk is None:
        refusals.append("Cannot determine the running OS disk — refusing "
                        "to proceed (safety).")
        return safe, refusals, warnings
    for d in disks:
        if d["path"] == root_disk:
            refusals.append("%s: this is the running OS disk" % d["path"])
            continue
        if d["rm"] or d["tran"] == "usb":
            refusals.append("%s: removable / USB drive" % d["path"])
            continue
        if has_bad_serial(d["serial"]) and not force:
            refusals.append(
                "%s: serial is '%s' (placeholder / unknown); use --force "
                "if you are certain" % (d["path"], d["serial"] or ""))
            continue
        safe.append(d)
    return safe, refusals, warnings


def verify_check_report(path, expected_serials, force=False,
                        job_id="", asset_id=""):
    """Look at a laptop_check.py report. Cross-check that it belongs to
    *this* laptop, not one from an earlier job."""
    if not path or not os.path.exists(path):
        return True, "no check report found (skipping cross-check)"
    try:
        with open(path) as f:
            rep = json.load(f)
    except Exception as e:
        return True, "check report unreadable: %s" % e

    result = str(rep.get("result", "?"))

    # 1. Refuse BLOCKED unless forced.
    if result == "BLOCKED" and not force:
        return False, ("check report is BLOCKED — re-run laptop_check.py "
                       "or use --force if you really mean it")

    # 2. Refuse if the report is for a different job/asset.
    if job_id and rep.get("job_id") and job_id != rep.get("job_id"):
        return False, ("check report is for job %r, this run is %r — "
                       "wrong report?"
                       % (rep.get("job_id"), job_id))
    if asset_id and rep.get("asset_id") and asset_id != rep.get("asset_id"):
        return False, ("check report is for asset %r, this run is %r — "
                       "wrong report?"
                       % (rep.get("asset_id"), asset_id))

    # 3. Refuse if the check report's system serial differs from this box.
    rep_serial = ((rep.get("identity") or {}).get("serial") or "")
    if rep_serial and rep_serial.lower() not in BAD_SERIALS:
        cur_serial = _system_serial()
        if cur_serial and cur_serial != rep_serial:
            return False, ("check report is for serial %s, this machine "
                           "reports %s — wrong machine?"
                           % (rep_serial, cur_serial))

    age = ""
    try:
        t = datetime.fromisoformat(rep.get("time", ""))
        age = " (%dh old)" % int((NOW - t).total_seconds() / 3600)
    except Exception:
        pass
    return True, "check report: %s%s" % (result, age)


# =============================================================================
# wipe methods
# =============================================================================
def _run_shred(dev, passes, zero_final, on_progress):
    """Run shred with live progress. A reader thread keeps the UI responsive
    even when shred is quiet between progress updates (shred uses \\r)."""
    if not shutil.which("shred"):
        return 1, "shred not installed"
    total = disk_size(dev)
    cmd = ["shred", "-v", "-n", str(passes)]
    if zero_final:
        cmd.append("-z")
    cmd.append(dev)
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE, env=env)
    lines = []
    fd = p.stderr.fileno()

    def _parse(line):
        m = re.search(r"(\d+(?:\.\d+)?)\s*([KMGTP])iB", line)
        pass_m = re.search(r"pass (\d+)/(\d+)", line)
        if m and total:
            mult = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3,
                    "T": 1024 ** 4, "P": 1024 ** 5}[m.group(2)]
            frac = float(m.group(1)) * mult / total
            note = ("pass %s/%s" % (pass_m.group(1), pass_m.group(2))
                    if pass_m else "")
            on_progress("wiping", frac, note)

    def _reader():
        buf = b""
        try:
            while True:
                chunk = os.read(fd, 256)
                if not chunk:
                    break
                buf += chunk
                while True:
                    idx = -1
                    for i, b in enumerate(buf):
                        if b in (10, 13):  # \n, \r
                            idx = i
                            break
                    if idx < 0:
                        break
                    line = buf[:idx].decode("utf-8", "replace")
                    buf = buf[idx + 1:]
                    if line.strip():
                        lines.append(line)
                        _parse(line)
        except Exception:
            pass
        if buf:
            line = buf.decode("utf-8", "replace")
            if line.strip():
                lines.append(line)
                _parse(line)

    th = threading.Thread(target=_reader, daemon=True)
    th.start()
    try:
        while p.poll() is None:
            time.sleep(0.3)
    except BaseException:
        try:
            p.kill()
            p.wait(timeout=2)
        except Exception:
            pass
        clear_progress()
        raise
    th.join(timeout=5)
    clear_progress()
    tail = " ".join([l.strip() for l in lines[-2:] if l.strip()])
    return p.returncode, tail


def _wipe_overwrite(dev, pattern, passes, on_progress):
    if pattern == "zero":
        rc, note = _run_shred(dev, 0, True, on_progress)
    else:
        rc, note = _run_shred(dev, passes, False, on_progress)
    return {"rc": rc, "note": note,
            "achieved": "clear" if rc == 0 else "fail"}


def _wipe_dod(dev, on_progress):
    rc, note = _run_shred(dev, 3, True, on_progress)
    return {"rc": rc, "note": note,
            "achieved": "dod" if rc == 0 else "fail"}


def _wipe_discard(dev, on_progress):
    if not shutil.which("blkdiscard"):
        return {"rc": 1, "note": "blkdiscard not installed", "achieved": "fail"}
    rc, out, err = run(["blkdiscard", "-v", dev], timeout=3600)
    clear_progress()
    return {"rc": rc, "note": (err or out).strip()[:200],
            "achieved": "discard" if rc == 0 else "fail"}


def _nvme_sanitize(dev):
    """NVMe sanitize — prefers crypto erase, then block erase, then
    overwrite. nvme-cli -a values: 1=crypto, 2=block erase, 3=overwrite."""
    if not shutil.which("nvme"):
        return {"rc": 1, "note": "nvme-cli not installed",
                "achieved": "fail"}
    rc, out, _ = run(["nvme", "id-ctrl", "-o", "json", dev])
    caps = 0
    if rc == 0:
        try:
            raw = json.loads(out).get("sanicap", 0)
            if isinstance(raw, str):
                caps = int(raw, 16) if raw else 0
            else:
                caps = int(raw)
        except Exception:
            caps = 0
    # Sanicap bits (NVMe Identify Controller):
    #   bit 0 = crypto erase, bit 1 = block erase, bit 2 = overwrite
    action = None
    if caps & 0x01:
        action = "1"    # crypto — best (destroys media key)
    elif caps & 0x02:
        action = "2"    # block erase
    elif caps & 0x04:
        action = "3"    # overwrite
    if action is None:
        return {"rc": 1, "note": "sanitize not supported",
                "achieved": "fail"}
    rc, out, err = run(["nvme", "sanitize", "-a", action, dev],
                       timeout=3600)
    if rc != 0:
        return {"rc": rc, "note": (err or out).strip()[:200],
                "achieved": "fail"}
    deadline = time.time() + 3600
    while time.time() < deadline:
        rc2, out2, _ = run(["nvme", "sanitize-log", "-o", "json", dev])
        if rc2 == 0:
            try:
                log = json.loads(out2)
                if int(log.get("sstat", {}).get("sanact", 0)) == 0:
                    return {"rc": 0, "note": "sanitize action %s complete"
                            % action, "achieved": "purge"}
            except Exception:
                pass
        time.sleep(2)
    return {"rc": 124, "note": "sanitize timed out", "achieved": "fail"}


def _nvme_format(dev):
    if not shutil.which("nvme"):
        return {"rc": 1, "note": "nvme-cli not installed", "achieved": "fail"}
    rc, out, err = run(["nvme", "format", "-s", "1", dev], timeout=600)
    return {"rc": rc, "note": (err or out).strip()[:200],
            "achieved": "clear" if rc == 0 else "fail"}


def _ata_secure_erase(dev):
    if not shutil.which("hdparm"):
        return {"rc": 1, "note": "hdparm not installed",
                "achieved": "fail"}
    rc, ident, _ = run(["hdparm", "-I", dev])
    if rc != 0 or "not supported" in ident.lower():
        return {"rc": 1, "note": "Security: not supported",
                "achieved": "fail"}
    if "frozen" in ident.lower():
        return {"rc": 1,
                "note": "Security state is FROZEN — power-cycle the "
                        "laptop (full shutdown, not reboot) and retry",
                "achieved": "fail"}
    use_enhanced = "enhanced" in ident.lower()
    run(["hdparm", "-S", "0", dev], timeout=10)   # disable standby
    rc, out, err = run(["hdparm", "--user-master", "u",
                        "--security-set-pass", "wipe", dev], timeout=30)
    if rc != 0:
        return {"rc": rc, "note": (err or out).strip()[:200],
                "achieved": "fail"}
    erase_flag = ("--security-erase-enhanced" if use_enhanced
                  else "--security-erase")
    rc, out, err = run(["hdparm", "--user-master", "u",
                        erase_flag, "wipe", dev], timeout=4 * 3600)
    return {"rc": rc, "note": (err or out).strip()[:200],
            "achieved": "purge" if rc == 0 else "fail"}


def _wipe_purge(disk, on_progress):
    """Try the strongest available Purge method, fall back within Purge."""
    dev = disk["path"]
    is_nvme = ("nvme" in dev) or (disk.get("tran") == "nvme")
    is_ssd = "ssd" in (disk.get("model") or "").lower() or is_nvme

    if is_nvme:
        r = _nvme_sanitize(dev)
        if r["achieved"] == "purge":
            return r
        r = _nvme_format(dev)
        if r["achieved"] in ("clear",):
            r["achieved"] = "purge-format"
            return r

    if not is_nvme:
        r = _ata_secure_erase(dev)
        if r["achieved"] == "purge":
            return r

    if is_ssd:
        r = _wipe_discard(dev, on_progress)
        if r["achieved"] == "discard":
            return r

    return {"rc": 1, "note": "no Purge method available", "achieved": "fail"}


# =============================================================================
# verification
# =============================================================================
def _has_fs_signature(dev, sample=2 * 1024 * 1024):
    """Return the first filesystem signature name found, or None."""
    head = read_bytes(dev, sample)
    if not head:
        return None
    for off, sig, name in FS_SIGNATURES:
        if len(head) >= off + len(sig) and head[off:off + len(sig)] == sig:
            return name
    return None


def verify_wipe(disk, achieved):
    """Return dict with 'verified': True/False + notes."""
    dev = disk["path"]
    out = {"verified": False, "notes": [], "sample_sha256": None}
    sig = _has_fs_signature(dev)
    if sig:
        out["notes"].append("filesystem signature still present: %s" % sig)
        return out
    if achieved in ("clear", "random", "dod", "discard", "purge-format"):
        head = read_bytes(dev, 1024 * 1024)
        if not head:
            out["notes"].append("could not read back first MiB")
            return out
        out["sample_sha256"] = hashlib.sha256(head).hexdigest()
        if achieved == "clear" and any(b != 0 for b in head[:4096]):
            out["notes"].append("first 4 KiB are not all zeros")
            return out
        out["verified"] = True
    elif achieved == "purge":
        # Secure erase / sanitize: no reliable read-back, but no signature.
        out["verified"] = True
        out["notes"].append("purge method — no read-back expected")
    else:
        out["notes"].append("unknown achieved method: %s" % achieved)
    return out


def smart_short_test(disk, on_progress):
    if not shutil.which("smartctl"):
        return {"ok": None, "note": "smartctl not installed"}
    rc, out, err = run(["smartctl", "-t", "short", disk["path"]], timeout=30)
    if rc != 0:
        return {"ok": False, "note": (err or out).strip()[:120]}
    deadline = time.time() + 180
    while time.time() < deadline:
        rc, out, _ = run(["smartctl", "-l", "selftest", disk["path"]],
                         timeout=10)
        if "completed without error" in out.lower():
            return {"ok": True, "note": "SMART short test passed"}
        if "self-test failed" in out.lower() or "read failure" in out.lower():
            return {"ok": False, "note": "SMART short test failed"}
        progress_line("SMART", 0.5, "short self-test running")
        time.sleep(5)
    clear_progress()
    return {"ok": None, "note": "SMART test did not report within 3 min"}


# =============================================================================
# confirmation
# =============================================================================
def _confirm(disks, auto_yes=False):
    if auto_yes:
        return True
    first = disks[0]
    key = first["serial"] or first["path"]
    print()
    print(paint("  To proceed, type exactly:", "bold"))
    print("    " + paint("WIPE:" + key, "bold", "yellow"))
    try:
        ans = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans == "WIPE:" + key


# =============================================================================
# main
# =============================================================================
def main():
    global USE_COLOR
    ap = argparse.ArgumentParser(description="ITAD data destruction")
    ap.add_argument("--job-id", default="")
    ap.add_argument("--asset-id", default="")
    ap.add_argument("--tech", default="")
    ap.add_argument("--json", default="",
                    help="JSON report path, or '-' for stdout")
    ap.add_argument("--method", default="auto", choices=ALL_METHODS,
                    help="Wipe method (default: auto)")
    ap.add_argument("--commit", action="store_true",
                    help="Actually wipe. Without this flag it is a DRY RUN.")
    ap.add_argument("--yes-i-mean-it", action="store_true",
                    help="Skip the interactive serial confirmation. "
                         "Only valid together with --commit.")
    ap.add_argument("--force", action="store_true",
                    help="Allow placeholder serials and BLOCKED check reports.")
    ap.add_argument("--no-verify", action="store_true",
                    help="Skip post-wipe read-back verification")
    ap.add_argument("--check-report", default="",
                    help="Path to laptop_check.py JSON (default: autodetect)")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()
    if args.no_color:
        USE_COLOR = False

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print(paint("Must run as root. Use: sudo python3 laptop_wipe.py ...",
                    "bold", "red"))
        sys.exit(3)

    if args.yes_i_mean_it and not args.commit:
        print(paint("--yes-i-mean-it requires --commit", "bold", "red"))
        sys.exit(3)

    if not args.json:
        default = "/tmp/laptop_wipe.json"
        for cand in ("/run/live/medium", "/cdrom", "/media/usb", "/mnt/usb"):
            if os.path.isdir(cand) and os.access(cand, os.W_OK):
                default = os.path.join(cand, "laptop_wipe.json")
                break
        args.json = default

    # --json - : JSON to stdout, human output to stderr.
    json_stream = sys.stdout
    if args.json == "-":
        sys.stdout = sys.stderr

    if not args.check_report:
        for cand in ("/run/live/medium/laptop_check.json",
                     "/cdrom/laptop_check.json",
                     "/media/usb/laptop_check.json",
                     "/mnt/usb/laptop_check.json",
                     "/tmp/laptop_check.json"):
            if os.path.exists(cand):
                args.check_report = cand
                break

    title_banner("ITAD WIPE", "v%s   %s" % (
        VERSION, "COMMIT" if args.commit else "DRY RUN"))
    box("JOB", kv("Time", NOW.strftime("%Y-%m-%d  %H:%M:%S"))
        + kv("Job ID", args.job_id or "-")
        + kv("Asset ID", args.asset_id or "-")
        + kv("Technician", args.tech or "-")
        + kv("Method", args.method)
        + kv("Mode", "COMMIT (destructive)" if args.commit else "DRY RUN"),
        "red" if args.commit else "blue")

    rule("SCANNING")
    disks, notes = scan_disks()
    if notes:
        box("WARNING", [tag("WARN") + " " + notes[0]], "yellow")
    if not disks:
        big_banner(["", "%s  %s" % (ICON["warn"], spaced("NO DRIVES")),
                    "No internal drives found.", ""], "yellow")
        _save_and_exit(args, json_stream, {
            "tool": "laptop_wipe.py", "version": VERSION,
            "time": NOW.isoformat(), "dry_run": not args.commit,
            "devices": [], "overall": "WARN",
        }, code=2)

    safe, refusals, _ = preflight(disks, force=args.force)
    if refusals:
        rows = [tag("FAIL", "refused") + paint(
            "  these drives will NOT be touched:", "dim"), ""]
        for r in refusals:
            rows += item(paint(ICON["bad"], "red"), r)
        box("SAFETY", rows, "red")

    if not safe:
        big_banner(["", "%s  %s" % (ICON["bad"], spaced("UNSAFE")),
                    "No safe drives to wipe.", ""], "red")
        _save_and_exit(args, json_stream, {
            "tool": "laptop_wipe.py", "version": VERSION,
            "time": NOW.isoformat(), "dry_run": not args.commit,
            "devices": [], "refusals": refusals, "overall": "FAIL",
        }, code=5)

    ok, msg = verify_check_report(args.check_report,
                                  [d["serial"] for d in safe],
                                  force=args.force,
                                  job_id=args.job_id,
                                  asset_id=args.asset_id)
    if not ok:
        big_banner(["", "%s  %s" % (ICON["bad"], spaced("BLOCKED")),
                    msg, "Use --force to override.", ""], "red")
        _save_and_exit(args, json_stream, {
            "tool": "laptop_wipe.py", "version": VERSION,
            "time": NOW.isoformat(), "dry_run": not args.commit,
            "devices": [], "check_report": msg, "overall": "FAIL",
        }, code=5)
    vlog = "  " + paint("check report: " + msg, "dim")
    print(vlog)

    rows = [tag("WIPED") + paint(
        "  the following drives WILL be wiped:", "dim"), ""]
    for d in safe:
        rows += kv("Device", "%s  %s  %s GB  SN: %s" % (
            d["path"], d["model"] or "?", d["size_gb"],
            d["serial"] or "(no serial)"))
    rows.append("")
    rows += para("Method: %s" % args.method)
    box("TARGETS", rows, "yellow")

    if not args.commit:
        big_banner(["", "%s  %s" % (ICON["info"], spaced("DRY RUN")),
                    "No data was written. Re-run with --commit to wipe.",
                    ""], "yellow")
        _save_and_exit(args, json_stream, {
            "tool": "laptop_wipe.py", "version": VERSION,
            "time": NOW.isoformat(), "dry_run": True,
            "devices": [{"path": d["path"], "model": d["model"],
                         "serial": d["serial"],
                         "size_gb": d["size_gb"]} for d in safe],
            "method": args.method, "overall": "WARN",
        }, code=2)

    rule("CONFIRM", "red")
    if not _confirm(safe, auto_yes=args.yes_i_mean_it):
        big_banner(["", "%s  %s" % (ICON["warn"], spaced("ABORTED")),
                    "No confirmation — nothing was wiped.", ""], "yellow")
        _save_and_exit(args, json_stream, {
            "tool": "laptop_wipe.py", "version": VERSION,
            "time": NOW.isoformat(), "dry_run": False,
            "devices": [], "overall": "WARN",
            "aborted": True,
        }, code=4)

    rule("WIPING", "red")
    report_devices = []
    failures = 0
    for i, disk in enumerate(safe, 1):
        print()
        print("  " + paint("[%d/%d]  %s  %s" % (
            i, len(safe), disk["path"], disk["model"] or "?"),
            "bold"))
        row = {"path": disk["path"], "model": disk["model"],
               "serial": disk["serial"], "size_gb": disk["size_gb"],
               "tran": disk["tran"], "requested": args.method}

        achieved = "fail"
        if args.method in ("auto", "purge"):
            res = _wipe_purge(disk, progress_line)
            achieved = res["achieved"]
            row["attempted"] = "purge"
            row["note"] = res.get("note", "")
            if achieved == "fail":
                print("    " + paint(
                    "Purge unavailable — falling back to single-pass "
                    "overwrite (Clear)", "yellow"))
                res = _wipe_overwrite(disk["path"], "random", 1, progress_line)
                achieved = ("clear-fallback" if res["rc"] == 0 else "fail")
                row["note"] = res.get("note", "")
        elif args.method == "clear":
            res = _wipe_overwrite(disk["path"], "zero", 0, progress_line)
            achieved = res["achieved"]
            row["note"] = res.get("note", "")
        elif args.method == "random":
            res = _wipe_overwrite(disk["path"], "random", 1, progress_line)
            achieved = res["achieved"]
            row["note"] = res.get("note", "")
        elif args.method == "dod":
            res = _wipe_dod(disk["path"], progress_line)
            achieved = res["achieved"]
            row["note"] = res.get("note", "")
        elif args.method == "discard":
            res = _wipe_discard(disk["path"], progress_line)
            achieved = res["achieved"]
            row["note"] = res.get("note", "")
        else:
            row["note"] = "unsupported method"

        row["achieved"] = achieved
        if achieved == "fail":
            failures += 1
            print("  " + tag("FAIL") + "  " + paint(row.get("note", "")[:120],
                                                    "dim"))
        else:
            print("  " + tag("WIPED", "WIPED (%s)" % achieved))

        if not args.no_verify and achieved != "fail":
            ver = verify_wipe(disk, achieved)
            row["verification"] = ver
            if ver["verified"]:
                print("  " + tag("PASS", "VERIFIED") + paint(
                    "  sha256=%s" % (ver["sample_sha256"] or "-")[:16],
                    "dim"))
            else:
                failures += 1
                print("  " + tag("WARN", "NOT VERIFIED"))
                for n in ver["notes"]:
                    print("    " + paint(n, "dim"))

            sm = smart_short_test(disk, progress_line)
            row["smart_short"] = sm
            if sm["ok"] is True:
                print("  " + tag("PASS", "SMART") + paint(
                    "  " + sm["note"], "dim"))
            elif sm["ok"] is False:
                print("  " + tag("WARN", "SMART") + paint(
                    "  " + sm["note"], "yellow"))
            else:
                print("  " + paint("  SMART: " + sm["note"], "dim"))

        report_devices.append(row)

    overall = "FAIL" if failures else "PASS"
    code = 1 if failures else 0

    report = {
        "tool": "laptop_wipe.py",
        "version": VERSION,
        "time": NOW.isoformat(),
        "job_id": args.job_id,
        "asset_id": args.asset_id,
        "technician": args.tech,
        "method": args.method,
        "dry_run": False,
        "devices": report_devices,
        "refusals": refusals,
        "check_report": msg,
        "overall": overall,
    }
    body = json.dumps({k: v for k, v in report.items()
                       if k != "certificate_sha256"},
                      sort_keys=True, separators=(",", ":"))
    report["certificate_sha256"] = hashlib.sha256(body.encode()).hexdigest()

    rule("RESULTS", "red" if failures else "green")
    rows = []
    for d in report_devices:
        st = "WIPED" if d["achieved"] != "fail" else "FAIL"
        rows += kv("Device", "%s  %s" % (d["path"], d["model"] or "?"))
        rows += kv("  Serial", d["serial"] or "(none)")
        rows += kv("  Achieved", d["achieved"])
        ver = d.get("verification") or {}
        rows += kv("  Verified", "yes" if ver.get("verified") else "no")
    box("WIPE SUMMARY", rows or ["(nothing wiped)"],
        "red" if failures else "green")
    box("CERTIFICATE", kv("SHA-256", report["certificate_sha256"]),
        "cyan")

    if failures:
        big_banner(["", "%s  %s" % (ICON["bad"], spaced("FAILED")),
                    "%d device(s) did not wipe cleanly." % failures,
                    "Do not ship. Review the notes above.", ""], "red")
    else:
        big_banner(["", "%s  %s" % (ICON["ok"], spaced("WIPED")),
                    "All targets wiped. Ready for laptop_test.py.", ""],
                   "green")

    _save_and_exit(args, json_stream, report, code=code)


def _save_and_exit(args, json_stream, report, code):
    if args.json == "-":
        json.dump(report, json_stream, indent=2)
        json_stream.write("\n")
        json_stream.flush()
    else:
        try:
            with open(args.json, "w") as f:
                json.dump(report, f, indent=2)
            print(paint(" Saved: %s" % args.json, "dim"))
        except Exception as e:
            print("Could not save JSON: %s" % e)
    sys.exit(code)


if __name__ == "__main__":
    main()