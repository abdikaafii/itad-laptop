#!/usr/bin/env python3
"""
net.py - ITAD network helpers, shared by LaptopSeal / SwitchSeal / PhoneSeal (v0.4.0)

v0.4.0: Wi-Fi scan switches the Wi-Fi radio on, waits for the card to be
ready, scans twice, and wifi_problem() says in plain words why no Wi-Fi
networks show (no card, airplane switch, missing driver/firmware...).

The test page runs in the phone's browser, so the phone must reach the
station. Three ways, easiest first:

  1. Android with USB debugging: over the USB cable (adb reverse). No Wi-Fi.
  2. Station Wi-Fi (hotspot): the station makes its own Wi-Fi network and
     the phone joins it by scanning a QR code. No router needed.
  3. Same Wi-Fi / network: the station joins the office Wi-Fi (simple list
     + password) or a network cable, and the phone is on the same network.

Everything uses NetworkManager (nmcli). All functions return plain values
and never raise.
"""
import random
import re
import subprocess
import time

HOTSPOT_CON = "itad-hotspot"
PORT = 8443


def run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "%s not installed" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except Exception as e:
        return 1, "", str(e)


def _split(line):
    """nmcli -t line -> fields (handles \\: inside values)."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def devices():
    """[{device, type, state, connection}]"""
    rc, o, _ = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION",
                    "device"], 10)
    out = []
    for ln in o.splitlines():
        f = _split(ln)
        if len(f) >= 4:
            out.append({"device": f[0], "type": f[1], "state": f[2],
                        "connection": f[3]})
    return out


def wifi_device():
    for d in devices():
        if d["type"] == "wifi":
            return d["device"]
    return ""


def ip_of(dev):
    rc, o, _ = run(["ip", "-4", "-o", "addr", "show", "dev", dev], 5)
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", o)
    return m.group(1) if m else ""


def hotspot_info():
    """(ssid, password, ip) when the station Wi-Fi is running, else None."""
    for d in devices():
        if d["connection"] == HOTSPOT_CON and d["state"].startswith(
                "connected"):
            rc, o, _ = run(["nmcli", "-s", "-t", "-f",
                            "802-11-wireless.ssid,802-11-wireless-security.psk",
                            "connection", "show", HOTSPOT_CON], 10)
            vals = {}
            for ln in o.splitlines():
                k, _, v = ln.partition(":")
                vals[k] = v
            return (vals.get("802-11-wireless.ssid", ""),
                    vals.get("802-11-wireless-security.psk", ""),
                    ip_of(d["device"]))
    return None


def summary():
    """One short line for the top bar, and the state:
    ('Wi-Fi Office  192.168.1.20', 'ok') / ('No network', 'none')."""
    hs = hotspot_info()
    if hs:
        return "Station Wi-Fi %s  %s" % (hs[0], hs[2]), "hotspot"
    best = None
    for d in devices():
        if not d["state"].startswith("connected") or d["type"] in (
                "loopback", "bridge", "tun"):
            continue
        ip = ip_of(d["device"])
        if not ip:
            continue
        kind = {"wifi": "Wi-Fi", "ethernet": "Cable"}.get(d["type"],
                                                         d["type"])
        txt = "%s %s  %s" % (kind, d["connection"], ip)
        if d["type"] == "wifi" or best is None:
            best = txt
    if best:
        return best, "ok"
    return "No network", "none"


def _wifi_ready():
    """Switch Wi-Fi on and wait (max 15 s) until the card can scan.
    Returns the Wi-Fi device name, or "" when there is none."""
    run(["rfkill", "unblock", "wifi"], 5)
    run(["nmcli", "radio", "wifi", "on"], 10)
    dev = ""
    for _ in range(15):
        for d in devices():
            if d["type"] != "wifi":
                continue
            dev = d["device"]
            if d["state"] == "unmanaged":
                run(["nmcli", "device", "set", dev, "managed", "yes"], 10)
            elif d["state"] != "unavailable":
                return dev
        time.sleep(1)
    return dev


def _parse_list(o):
    nets = {}
    for ln in o.splitlines():
        f = _split(ln)
        if len(f) < 4 or not f[1]:
            continue
        try:
            sig = int(f[2])
        except ValueError:
            sig = 0
        n = {"ssid": f[1], "signal": sig,
             "secure": bool(f[3].strip() and f[3].strip() != "--"),
             "in_use": f[0].strip() == "*"}
        if f[1] not in nets or nets[f[1]]["signal"] < sig:
            nets[f[1]] = n
    return nets


def scan():
    """Wi-Fi networks: [{ssid, signal, secure, in_use}] strongest first."""
    dev = _wifi_ready()
    if not dev:
        return []
    fields = ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
              "device", "wifi", "list", "ifname", dev]
    nets = {}
    # The first scan after start-up often comes back empty: try 3 times.
    for attempt in range(3):
        run(["nmcli", "device", "wifi", "rescan", "ifname", dev], 15)
        time.sleep(3)
        rc, o, e = run(fields + ["--rescan", "no"], 20)
        nets = _parse_list(o)
        if nets:
            break
    return sorted(nets.values(), key=lambda n: (-n["in_use"], -n["signal"]))


def wifi_problem():
    """Plain-English reason why no Wi-Fi networks were found."""
    rc, o, e = run(["nmcli", "-t", "-f", "RUNNING", "general"], 10)
    if rc != 0 or "running" not in o:
        run(["systemctl", "start", "NetworkManager"], 20)
        rc, o, e = run(["nmcli", "-t", "-f", "RUNNING", "general"], 10)
        if rc != 0 or "running" not in o:
            return ("The network service is not running on this USB "
                    "(NetworkManager). Use a network cable, and tell the "
                    "USB builder.")
    rc, o, e = run(["rfkill", "-n", "-o", "TYPE,SOFT,HARD"], 5)
    for ln in o.splitlines():
        f = ln.split()
        if len(f) >= 3 and f[0] == "wlan" and f[2] == "blocked":
            return ("Wi-Fi is switched OFF by the laptop itself: press the "
                    "airplane / Wi-Fi key (often F12 or Fn+F2) or the Wi-Fi "
                    "switch on the side, then press Scan again. On some "
                    "laptops it is turned off in the BIOS.")
    dev = wifi_device()
    if not dev:
        rc, o, e = run(["lspci"], 10)
        rc2, o2, e2 = run(["lsusb"], 10)
        hw = [ln for ln in (o + o2).splitlines()
              if re.search(r"network controller|wireless|wi-?fi|802\.11",
                           ln, re.I)]
        if not hw:
            return ("No Wi-Fi card found in this computer (it may be "
                    "removed or turned off in the BIOS). Use a network "
                    "cable.")
        rc, o, e = run(["dmesg"], 10)
        fw = [ln for ln in o.splitlines()
              if re.search(r"firmware", ln, re.I) and
              re.search(r"fail|error|not found", ln, re.I)]
        card = re.sub(r"^\S+\s+", "", hw[0]).strip()[:70]
        if fw:
            return ("Wi-Fi card found (%s) but its driver/firmware is "
                    "missing on this USB. Use a network cable for now." %
                    card)
        return ("Wi-Fi card found (%s) but it has no working driver on "
                "this USB. Use a network cable for now." % card)
    for d in devices():
        if d["device"] == dev and d["state"] == "unavailable":
            return ("The Wi-Fi card (%s) is not ready - press the laptop's "
                    "Wi-Fi / airplane key, wait 10 seconds and press Scan "
                    "again." % dev)
    return ("The Wi-Fi card works but found no networks here. Move closer "
            "to the router, wait a few seconds and press Scan again.")


def connect(ssid, password=""):
    """(ok, message)"""
    hotspot_stop()
    cmd = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    rc, o, e = run(cmd, 60)
    msg = (o + e).strip().splitlines()
    msg = msg[-1] if msg else ""
    if rc == 0:
        return True, "Connected to %s" % ssid
    if "Secrets were required" in msg or "802-1x" in msg or \
            "password" in msg.lower():
        return False, "Wrong password for %s" % ssid
    return False, msg or "could not connect"


def hotspot_start():
    """Station Wi-Fi. (ok, message, ssid, password, ip)"""
    run(["rfkill", "unblock", "wifi"], 5)
    dev = wifi_device()
    if not dev:
        return (False, "This station has no Wi-Fi card that works - use a "
                "network cable, or Android over USB.", "", "", "")
    ssid = "ITAD-PhoneSeal-%04d" % random.randint(0, 9999)
    pw = "%08d" % random.randint(0, 99999999)
    run(["nmcli", "connection", "delete", HOTSPOT_CON], 15)
    rc, o, e = run(["nmcli", "device", "wifi", "hotspot", "ifname", dev,
                    "con-name", HOTSPOT_CON, "ssid", ssid, "password", pw],
                   45)
    if rc != 0:
        msg = (e or o).strip().splitlines()
        return (False, "This Wi-Fi card can't make a hotspot (%s). Use the "
                "office Wi-Fi, a network cable, or Android over USB." %
                (msg[-1] if msg else "error"), "", "", "")
    ip = ""
    for _ in range(10):
        ip = ip_of(dev)
        if ip:
            break
        time.sleep(1)
    return True, "Station Wi-Fi is on", ssid, pw, ip


def hotspot_stop():
    run(["nmcli", "connection", "down", HOTSPOT_CON], 15)


def wifi_qr_text(ssid, password):
    """Text for a QR code that a phone camera uses to join the Wi-Fi."""
    def esc(s):
        for ch in "\\;,:\"":
            s = s.replace(ch, "\\" + ch)
        return s
    return "WIFI:T:WPA;S:%s;P:%s;;" % (esc(ssid), esc(password))


def android_usb_open(serial, url_path):
    """Open the test page on an Android phone over the USB cable: the phone
    reaches the station through adb (no Wi-Fi). (ok, message)"""
    rc, o, e = run(["adb", "-s", serial, "reverse", "tcp:%d" % PORT,
                    "tcp:%d" % PORT], 15)
    if rc != 0:
        return False, (e or o).strip()[:120] or "adb reverse failed"
    url = "https://127.0.0.1:%d%s" % (PORT, url_path)
    rc, o, e = run(["adb", "-s", serial, "shell", "am", "start", "-a",
                    "android.intent.action.VIEW", "-d", url], 15)
    if rc != 0 or "Error" in o + e:
        return False, "open this on the phone: %s" % url
    return True, "test page opened on the phone"
