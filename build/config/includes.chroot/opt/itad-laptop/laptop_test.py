#!/usr/bin/env python3
"""
laptop_test.py - ITAD component test (v1.0.0)  READ-MOSTLY

Step 2 of the ITAD pipeline. Runs AFTER laptop_check.py and the wipe.

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

VERSION = "1.0.0"
ALL_TESTS = ["cpu", "ram", "drive", "battery", "screen", "keyboard",
             "touchpad", "webcam", "speakers", "wifi", "usb"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
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


def _wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def _banner(name):
    print("\n" + "-" * 60)
    print(" TEST: %s" % name.upper())
    print("-" * 60)


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------
def test_cpu(load_seconds=30):
    _banner("cpu")
    r = {"component": "CPU", "status": "PASS", "details": {}, "notes": []}

    rc, out, _ = run(["lscpu"])
    if rc == 0:
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                k, v = k.strip(), v.strip()
                if k in ("Model name", "CPU(s)", "Thread(s) per core",
                         "Core(s) per socket", "Socket(s)", "CPU max MHz",
                         "CPU min MHz", "Architecture"):
                    r["details"][k] = v
        print("  CPU: %s (%s threads)" % (
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

    before = _temps()
    if before:
        r["details"]["temperatures_before"] = before
        hottest = max(t["celsius"] for t in before)
        print("  Idle temp: %.1f°C" % hottest)
        if hottest > 90:
            r["notes"].append("CPU already hot at idle: %.1f°C" % hottest)
            r["status"] = "WARN"

    if load_seconds > 0:
        print("  Running %ds load test..." % load_seconds)
        cmd = None
        if shutil.which("stress-ng"):
            cmd = ["stress-ng", "--cpu", str(os.cpu_count() or 1),
                   "--timeout", "%ds" % load_seconds, "--metrics-brief"]
        elif shutil.which("stress"):
            cmd = ["stress", "--cpu", str(os.cpu_count() or 1),
                   "--timeout", str(load_seconds)]
        if cmd:
            rc, out, err = run(cmd, timeout=load_seconds + 30)
            r["details"]["load_test"] = {
                "tool": cmd[0], "rc": rc,
                "tail": (out + err).strip().splitlines()[-3:],
            }
        else:
            procs = [subprocess.Popen(["yes"], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
                     for _ in range(os.cpu_count() or 1)]
            try:
                time.sleep(load_seconds)
            finally:
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
            print("  Load temp: %.1f°C" % hottest)
            if hottest > 95:
                r["notes"].append("CPU under load reached %.1f°C" % hottest)
                r["status"] = "WARN"

    print("  Result: %s" % r["status"])
    return r


# ---------------------------------------------------------------------------
# RAM
# ---------------------------------------------------------------------------
def test_ram(minutes=2):
    _banner("ram")
    r = {"component": "RAM", "status": "PASS", "details": {}, "notes": []}

    try:
        with open("/proc/meminfo") as f:
            info = f.read()
        m = re.search(r"MemTotal:\s+(\d+)", info)
        if m:
            r["details"]["total_gb"] = round(int(m.group(1)) / 1024 / 1024, 1)
            print("  Total RAM: %s GB" % r["details"]["total_gb"])
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
        print("  memtester %d MB (up to %d min)..." % (test_mb, minutes))
        cmd = ["memtester", "%dM" % test_mb, "1"]
        rc, out, err = run(cmd, timeout=minutes * 60)
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

    print("  Result: %s" % r["status"])
    return r


# ---------------------------------------------------------------------------
# Drive health (SMART)
# ---------------------------------------------------------------------------
def test_drives():
    _banner("drive")
    r = {"component": "Drive Health", "status": "PASS",
         "details": {"disks": []}, "notes": []}
    disks = scan_internal_disks()
    if not disks:
        r["status"] = "WARN"
        r["notes"].append("No internal drives detected")
        print("  Result: WARN (no drives)")
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
                info["smart_error"] = (err or out).strip().splitlines()[:2]

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

        print("  %s (%s): %s" % (path, d["model"] or "?",
                                 "OK" if info.get("healthy") is not False
                                 else "SMART FAILED"))
        r["details"]["disks"].append(info)

    print("  Result: %s" % r["status"])
    return r


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------
def test_battery():
    _banner("battery")
    r = {"component": "Battery", "status": "PASS",
         "details": {}, "notes": []}

    bats = sorted(glob.glob("/sys/class/power_supply/BAT*"))
    if not bats:
        r["status"] = "SKIP"
        r["notes"].append("No battery (desktop / removed)")
        print("  SKIP (no battery)")
        return r

    out = []
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
            if health < 60:
                r["notes"].append(
                    "%s: battery health %s%% (below 60%%)" % (bp, health))
                r["status"] = "FAIL"
            elif health < 80:
                r["notes"].append(
                    "%s: battery health %s%% (below 80%%)" % (bp, health))
                if r["status"] == "PASS":
                    r["status"] = "WARN"
            print("  %s: health %.1f%% (%.0f/%.0f mWh) cycles=%s"
                  % (bp, health, b["full_mwh"], b["design_mwh"],
                     b["cycle_count"] or "?"))
        except Exception:
            print("  %s: health data unavailable" % bp)
        out.append(b)
    r["details"]["batteries"] = out
    print("  Result: %s" % r["status"])
    return r


# ---------------------------------------------------------------------------
# Screen (interactive)
# ---------------------------------------------------------------------------
def _fill_screen(ansi):
    try:
        cols = int(subprocess.check_output(["tput", "cols"]).strip())
        rows = int(subprocess.check_output(["tput", "lines"]).strip())
    except Exception:
        cols, rows = 80, 24
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write((ansi + " " * cols + "\n") * rows + "\033[0m")
    sys.stdout.flush()


def test_screen(interactive):
    _banner("screen")
    r = {"component": "Screen", "status": "SKIP",
         "details": {}, "notes": []}
    if not interactive:
        r["notes"].append("Screen test requires --interactive")
        print("  SKIP (not interactive)")
        return r

    print("  Watch for dead pixels, stuck pixels, discolouration.")
    print("  [Enter]=next  s=skip rest  f=record failure")
    patterns = [("RED", "\033[41m"), ("GREEN", "\033[42m"),
                ("BLUE", "\033[44m"), ("WHITE", "\033[47m"),
                ("BLACK", "\033[40m"), ("GREY", "\033[100m")]
    seen, failed = [], False
    for name, ansi in patterns:
        _fill_screen(ansi)
        try:
            ans = input("\n[%s] Enter/s/f: " % name).strip().lower()
        except EOFError:
            break
        if ans == "s":
            break
        if ans == "f":
            failed = True
            r["notes"].append("Failure on pattern %s" % name)
        seen.append(name)
    os.system("clear")
    r["details"]["patterns_shown"] = seen
    r["status"] = "FAIL" if failed else ("PASS" if seen else "SKIP")
    print("  Result: %s" % r["status"])
    return r


# ---------------------------------------------------------------------------
# Keyboard (interactive, evtest)
# ---------------------------------------------------------------------------
def test_keyboard(interactive):
    _banner("keyboard")
    r = {"component": "Keyboard", "status": "SKIP",
         "details": {}, "notes": []}

    kb_dev = None
    try:
        rc, out, _ = run(["evtest", "--list-devices"])
        if rc == 0:
            current_dev = None
            for line in out.splitlines():
                m = re.match(r"(.+):\s*$", line)
                if m and "/dev/input" not in line:
                    current_dev = m.group(1).strip()
                elif line.strip().startswith("/dev/input/event"):
                    if current_dev and ("keyboard" in current_dev.lower()
                                        or "at translated" in current_dev.lower()
                                        or "kbd" in current_dev.lower()):
                        kb_dev = line.strip()
                        r["details"]["device"] = current_dev
                        break
    except Exception:
        pass

    if not kb_dev:
        r["notes"].append("Could not find a keyboard device")
        r["status"] = "WARN"
        print("  WARN (no device)")
        return r

    if not interactive:
        r["status"] = "PASS"
        r["notes"].append("Device present — enable --interactive for keypress test")
        print("  PASS (device present)")
        return r

    print("  Press a sample of keys (letters, numbers, Enter, Space).")
    print("  Type 'DONE' when finished, or Ctrl+C to abort.")
    print("  Device: %s" % kb_dev)

    seen = set()
    try:
        proc = subprocess.Popen(
            ["evtest", kb_dev],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        start = time.time()
        while time.time() - start < 120:
            line = proc.stdout.readline()
            if not line:
                break
            if "(KEY_" in line and "value 1" in line:
                m = re.search(r"KEY_(\w+)\)", line)
                if m:
                    seen.add(m.group(1))
                    sys.stdout.write(".")
                    sys.stdout.flush()
            if all(k in seen for k in ("D", "O", "N", "E")):
                break
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
    except KeyboardInterrupt:
        r["status"] = "FAIL"
        r["notes"].append("Aborted by technician")
        return r
    except Exception as e:
        r["status"] = "WARN"
        r["notes"].append("evtest error: %s" % e)
        return r

    print()
    r["details"]["keys_seen"] = sorted(seen)
    r["details"]["key_count"] = len(seen)
    if len(seen) < 10:
        r["status"] = "WARN"
        r["notes"].append("Only %d keys detected" % len(seen))
    else:
        r["status"] = "PASS"
    print("  Result: %s (%d keys seen)" % (r["status"], len(seen)))
    return r


# ---------------------------------------------------------------------------
# Touchpad (device presence)
# ---------------------------------------------------------------------------
def test_touchpad():
    _banner("touchpad")
    r = {"component": "Touchpad", "status": "SKIP",
         "details": {}, "notes": []}
    try:
        for block in open("/proc/bus/input/devices").read().split("\n\n"):
            low = block.lower()
            if any(k in low for k in ("touchpad", "synaptics", "elan",
                                       "trackpad", "alps")):
                m = re.search(r'N: Name="([^"]+)"', block)
                if m:
                    r["details"]["device"] = m.group(1)
                r["status"] = "PASS"
                r["notes"].append(
                    "Device present — manually drag the cursor across the "
                    "full pad and click both buttons")
                break
    except Exception:
        pass
    if r["status"] == "SKIP":
        r["notes"].append("No touchpad device detected")
    print("  Result: %s" % r["status"])
    return r


# ---------------------------------------------------------------------------
# Webcam
# ---------------------------------------------------------------------------
def test_webcam():
    _banner("webcam")
    r = {"component": "Webcam", "status": "SKIP",
         "details": {}, "notes": []}

    if not shutil.which("v4l2-ctl"):
        r["notes"].append("v4l2-ctl not installed (apt install v4l-utils)")
        print("  SKIP (%s)" % r["notes"][-1])
        return r

    rc, out, _ = run(["v4l2-ctl", "--list-devices"])
    if rc != 0 or "video" not in out.lower():
        r["status"] = "FAIL"
        r["notes"].append("No video device detected")
        print("  FAIL (no camera)")
        return r

    r["details"]["list"] = out.strip()
    devs = re.findall(r"(/dev/video\d+)", out)
    if not devs:
        r["status"] = "WARN"
        print("  WARN (no /dev/video*)")
        return r
    dev = devs[0]
    r["details"]["device"] = dev

    rc, out2, _ = run(["v4l2-ctl", "-d", dev, "--list-formats-ext"])
    if rc == 0:
        r["details"]["resolutions"] = sorted(set(
            re.findall(r"(\d{3,4}x\d{3,4})", out2)))

    if shutil.which("ffmpeg"):
        tmp = tempfile.mktemp(suffix=".jpg")
        rc, _, _ = run(["ffmpeg", "-y", "-f", "v4l2", "-i", dev,
                        "-frames:v", "1", tmp], timeout=15)
        if rc == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            r["details"]["capture_bytes"] = os.path.getsize(tmp)
            os.remove(tmp)
            r["status"] = "PASS"
        else:
            r["status"] = "WARN"
            r["notes"].append("ffmpeg capture failed")
    else:
        r["status"] = "PASS"
        r["notes"].append("Device present, ffmpeg not installed for capture")
    print("  Result: %s" % r["status"])
    return r


# ---------------------------------------------------------------------------
# Speakers
# ---------------------------------------------------------------------------
def test_speakers(interactive):
    _banner("speakers")
    r = {"component": "Speakers", "status": "SKIP",
         "details": {}, "notes": []}

    if not os.path.exists("/proc/asound/cards"):
        r["status"] = "FAIL"
        r["notes"].append("No ALSA sound cards")
        print("  FAIL (no sound cards)")
        return r
    cards = read_file("/proc/asound/cards")
    if "no soundcards" in cards.lower():
        r["status"] = "FAIL"
        r["notes"].append("No sound cards detected")
        print("  FAIL (no sound cards)")
        return r
    r["details"]["cards"] = cards

    if not shutil.which("speaker-test"):
        r["status"] = "WARN"
        r["notes"].append("speaker-test not installed (alsa-utils)")
        print("  WARN (speaker-test missing)")
        return r
    if not interactive:
        r["status"] = "PASS"
        r["notes"].append("Sound card present — enable --interactive to play tone")
        print("  PASS (card present)")
        return r

    print("  Playing a 2-second tone — listen for clear sound...")
    rc, out, err = run(["speaker-test", "-t", "sine", "-f", "440",
                        "-l", "1", "-c", "2"], timeout=10)
    if rc != 0:
        r["status"] = "WARN"
        r["notes"].append("speaker-test failed")
        print("  WARN (playback error)")
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
    print("  Result: %s" % r["status"])
    return r


# ---------------------------------------------------------------------------
# Wi-Fi
# ---------------------------------------------------------------------------
def test_wifi():
    _banner("wifi")
    r = {"component": "Wi-Fi", "status": "SKIP",
         "details": {}, "notes": []}

    rc, out, _ = run(["ip", "-o", "link", "show"])
    ifaces = re.findall(r"^\d+:\s+(\w+):", out, re.M)
    wlans = [i for i in ifaces if i.startswith(("wl", "wlan"))]
    if not wlans:
        r["notes"].append("No Wi-Fi interface (desktop / disabled in BIOS)")
        print("  SKIP (no interface)")
        return r
    r["details"]["interfaces"] = wlans
    iface = wlans[0]

    if shutil.which("iw"):
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
    print("  Result: %s" % r["status"])
    return r


# ---------------------------------------------------------------------------
# USB
# ---------------------------------------------------------------------------
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
        print("  Plug a USB stick into each external port, one at a time.")
        try:
            input("  Press ENTER when ready to test port 1... ")
        except EOFError:
            return r
        before = set(run(["lsusb"])[1].splitlines())
        time.sleep(2)
        after = set(run(["lsusb"])[1].splitlines())
        new = after - before
        if new:
            r["notes"].append("Port 1 OK — device seen")
            print("  Port 1: OK")
            r["details"]["port_1_new_device"] = list(new)[0][:100]
        else:
            r["status"] = "WARN"
            r["notes"].append("Port 1: no new device detected")
            print("  Port 1: WARN (no device)")
        print("  Repeat for remaining ports manually if desired.")
    else:
        print("  %d USB buses, %d devices visible"
              % (len(controllers), len(r["details"].get("devices", []))))
    print("  Result: %s" % r["status"])
    return r


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def summary_table(results):
    print("\n" + "=" * 60)
    print(" SUMMARY")
    print("=" * 60)
    print(" %-12s %-6s %s" % ("COMPONENT", "STATUS", "NOTES"))
    print("-" * 60)
    for r in results:
        note = "; ".join(r["notes"])[:60] if r["notes"] else "-"
        print(" %-12s %-6s %s" % (r["component"], r["status"], note))
    print("-" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
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
    args = ap.parse_args()

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("Must run as root. Use: sudo python3 laptop_test.py ...")
        sys.exit(3)

    if not args.json:
        default = "/tmp/laptop_test.json"
        for cand in ("/run/live/medium", "/cdrom", "/media/usb", "/mnt/usb"):
            if os.path.isdir(cand) and os.access(cand, os.W_OK):
                default = os.path.join(cand, "laptop_test.json")
                break
        args.json = default

    interactive = not args.no_interactive
    selected = [t.strip().lower() for t in args.test.split(",") if t.strip()]
    unknown = [t for t in selected if t not in ALL_TESTS]
    if unknown:
        print("Unknown test(s): %s" % ", ".join(unknown))
        sys.exit(3)

    print("=" * 60)
    print(" ITAD COMPONENT TEST  v%s" % VERSION)
    print("=" * 60)
    print(" Time:      %s" % NOW.isoformat())
    print(" Job ID:    %s" % (args.job_id or "-"))
    print(" Asset ID:  %s" % (args.asset_id or "-"))
    print(" Tech:      %s" % (args.tech or "-"))
    print(" Tests:     %s" % ", ".join(selected))
    print(" Mode:      %s" % ("interactive" if interactive else "automated only"))

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
    try:
        for t in selected:
            try:
                results.append(dispatcher[t]())
            except KeyboardInterrupt:
                results.append({"component": t.upper(), "status": "SKIP",
                                "details": {},
                                "notes": ["Aborted by technician"]})
                break
            except Exception as e:
                results.append({"component": t.upper(), "status": "WARN",
                                "details": {},
                                "notes": ["Test crashed: %s" % str(e)[:120]]})
    finally:
        pass

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
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        try:
            with open(args.json, "w") as f:
                json.dump(report, f, indent=2)
        except Exception as e:
            print("Could not save JSON: %s" % e)

    summary_table(results)
    print("\n OVERALL RESULT : %s" % overall)
    if overall == "PASS":
        print(" All tested components pass. Ready for report.")
    elif overall == "WARN":
        print(" Some components could not be verified. Review notes above.")
    else:
        print(" FAILED component(s) — do not ship. Review notes above.")
    print("=" * 60)
    if args.json != "-":
        print(" Saved: %s" % args.json)

    sys.exit(code)


if __name__ == "__main__":
    main()