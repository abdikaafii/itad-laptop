#!/usr/bin/env python3
import tkinter as tk
import subprocess
import os
import json

class WifiTestApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Wi-Fi Test")
        self.root.configure(bg="#1e1e1e")
        self.root.attributes("-fullscreen", True)

        tk.Label(root, text="WI-FI TEST", font=("Helvetica", 24, "bold"), bg="#1e1e1e", fg="#38bdf8").pack(pady=20)
        
        self.status = tk.Label(root, text="Click SCAN to look for networks.", font=("Helvetica", 12), bg="#1e1e1e", fg="#cccccc")
        self.status.pack()

        tk.Button(root, text="📡 SCAN NETWORKS", font=("Helvetica", 14, "bold"), bg="#3b82f6", fg="white", 
                  command=self.scan_wifi, takefocus=0).pack(pady=10)

        self.listbox = tk.Listbox(root, font=("Courier", 12), bg="#333333", fg="white", width=50, height=10)
        self.listbox.pack(pady=10)

        btn_frame = tk.Frame(root, bg="#1e1e1e")
        btn_frame.pack(pady=20)

        tk.Button(btn_frame, text="PASS", font=("Helvetica", 16, "bold"), bg="#22c55e", fg="white", width=15,
                  command=lambda: self.finish("PASS"), takefocus=0).pack(side=tk.LEFT, padx=20)
        tk.Button(btn_frame, text="FAIL", font=("Helvetica", 16, "bold"), bg="#ef4444", fg="white", width=15,
                  command=lambda: self.finish("FAIL"), takefocus=0).pack(side=tk.LEFT, padx=20)

        self.root.bind("<Escape>", lambda e: self.finish("FAIL"))

    def scan_wifi(self):
        self.listbox.delete(0, tk.END)
        self.status.config(text="Scanning...")
        self.root.update()
        
        try:
            # Uses nmcli to scan for networks
            out = subprocess.check_output(["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list"], text=True)
            networks = set()
            for line in out.strip().split("\n"):
                if line and not line.startswith(":"):
                    parts = line.split(":")
                    if len(parts) >= 2:
                        ssid = parts[0]
                        signal = parts[1]
                        networks.add(f"{ssid.ljust(30)} | Signal: {signal}%")
            
            if networks:
                for n in sorted(networks):
                    self.listbox.insert(tk.END, n)
                self.status.config(text=f"Found {len(networks)} networks.")
            else:
                self.status.config(text="No networks found.")
        except Exception as e:
            self.status.config(text="Error: nmcli not found or Wi-Fi hardware disabled.")
            print(e)

    def finish(self, result):
        report_dir = os.environ.get("ITAD_DATA", "/tmp/itad-reports")
        os.makedirs(report_dir, exist_ok=True)
        with open(os.path.join(report_dir, "wifi_test.json"), "w") as f:
            json.dump({"test": "wifi", "result": result}, f, indent=2)
        self.root.destroy()
        raise SystemExit(0 if result == "PASS" else 1)

if __name__ == "__main__":
    root = tk.Tk()
    app = WifiTestApp(root)
    root.mainloop()