#!/usr/bin/env python3
import tkinter as tk
from tkinter import messagebox
import subprocess
import os
import json
import re

COLORS = [
    ("RED", "#FF0000"),
    ("GREEN", "#00FF00"),
    ("BLUE", "#0000FF"),
    ("WHITE", "#FFFFFF"),
    ("BLACK", "#000000")
]

def get_device_info():
    info = {}
    
    # 1. Serial & Model
    try:
        with open("/sys/class/dmi/id/product_serial", "r") as f:
            info["Serial"] = f.read().strip()
        with open("/sys/class/dmi/id/product_name", "r") as f:
            info["Model"] = f.read().strip()
    except Exception:
        info["Serial"] = "Unknown"
        info["Model"] = "Unknown"

    # 2. RAM
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    info["RAM"] = f"{round(kb / 1024 / 1024, 1)} GB"
                    break
    except Exception:
        info["RAM"] = "Unknown"

    # 3. Storage (Model & Size)
    try:
        out = subprocess.check_output(["lsblk", "-d", "-n", "-o", "MODEL,SIZE,ROTA"], text=True)
        drives = []
        for line in out.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 3:
                # Handle models with spaces (e.g. "Samsung SSD 980")
                rota = parts[-1]
                size = parts[-2]
                model = " ".join(parts[:-2])
                drive_type = "HDD" if rota == "1" else "SSD"
                drives.append(f"{model} ({drive_type}) - {size}")
        info["Storage"] = " / ".join(drives) if drives else "Unknown"
    except Exception:
        info["Storage"] = "Unknown"

    # 4. Screen Resolution
    try:
        out = subprocess.check_output(["xrandr"], text=True)
        match = re.search(r"(\d+x\d+)\+0\+0", out)
        if match:
            info["Screen"] = match.group(1)
        else:
            info["Screen"] = "Unknown"
    except Exception:
        info["Screen"] = "Unknown"

    return info

class ScreenTestApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ITAD Screen Test")
        self.root.attributes("-fullscreen", True)
        
        self.color_index = 0
        self.result = None
        self.comments = ""

        # --- Phase 1: Color Cycle Canvas ---
        self.canvas = tk.Canvas(root, bg=COLORS[0][1], highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.instructions = self.canvas.create_text(
            400, 300, text="", font=("Helvetica", 24, "bold"), fill="gray"
        )
        
        self.update_color()

        self.root.bind("<space>", self.next_color)
        self.root.bind("<Escape>", self.skip_to_summary)
        self.root.focus_force()

    def update_color(self):
        if self.color_index < len(COLORS):
            name, hex_code = COLORS[self.color_index]
            self.canvas.config(bg=hex_code)
            
            # Set text color (black on white/green, white on others)
            text_color = "black" if name in ["GREEN", "WHITE"] else "white"
            
            self.canvas.itemconfig(
                self.instructions, 
                text=f"[{name}]\n\nPress SPACE for next color\nPress ESC to exit", 
                fill=text_color,
                anchor="center"
            )
            # Center the text
            self.canvas.coords(self.instructions, self.root.winfo_screenwidth()/2, self.root.winfo_screenheight()/2)
        else:
            self.skip_to_summary()

    def next_color(self, event=None):
        self.color_index += 1
        self.update_color()

    def skip_to_summary(self, event=None):
        self.canvas.destroy()
        self.root.unbind("<space>")
        self.root.unbind("<Escape>")
        
        # --- Phase 2: Summary & Feedback ---
        self.root.config(bg="#1e1e1e")
        
        frame = tk.Frame(self.root, bg="#1e1e1e")
        frame.pack(expand=True, fill=tk.BOTH, padx=50, pady=50)

        tk.Label(frame, text="SCREEN TEST SUMMARY", font=("Helvetica", 28, "bold"), bg="#1e1e1e", fg="#38bdf8").pack(pady=20)

        # Device Info
        info_frame = tk.LabelFrame(frame, text=" Device Information ", font=("Helvetica", 14), bg="#1e1e1e", fg="white", padx=20, pady=20)
        info_frame.pack(fill=tk.X, pady=20)

        info = get_device_info()
        row = 0
        for k, v in info.items():
            tk.Label(info_frame, text=f"{k}:", font=("Helvetica", 12, "bold"), bg="#1e1e1e", fg="#a3a3a3", anchor="w").grid(row=row, column=0, sticky="w", pady=5)
            tk.Label(info_frame, text=v, font=("Helvetica", 12), bg="#1e1e1e", fg="white", anchor="w").grid(row=row, column=1, sticky="w", padx=20, pady=5)
            row += 1

        # Comments
        tk.Label(frame, text="Comments (if any):", font=("Helvetica", 14, "bold"), bg="#1e1e1e", fg="white").pack(pady=(20, 5))
        self.comment_entry = tk.Text(frame, height=4, width=60, font=("Helvetica", 12), bg="#333333", fg="white", insertbackground="white")
        self.comment_entry.pack(pady=10)

        # Buttons
        btn_frame = tk.Frame(frame, bg="#1e1e1e")
        btn_frame.pack(pady=20)

        tk.Button(btn_frame, text="PASS (Good Screen)", font=("Helvetica", 16, "bold"), bg="#22c55e", fg="white", 
                  width=20, command=lambda: self.finish("PASS"), takefocus=0).pack(side=tk.LEFT, padx=20)
                  
        tk.Button(btn_frame, text="FAIL (Dead Pixels)", font=("Helvetica", 16, "bold"), bg="#ef4444", fg="white", 
                  width=20, command=lambda: self.finish("FAIL"), takefocus=0).pack(side=tk.LEFT, padx=20)

    def finish(self, result):
        self.result = result
        self.comments = self.comment_entry.get("1.0", tk.END).strip()
        
        # Save to JSON
        report_dir = os.environ.get("ITAD_DATA", "/tmp/itad-reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, "screen_test.json")
        
        data = {
            "test": "screen",
            "result": self.result,
            "comments": self.comments,
            "device_info": get_device_info()
        }
        
        try:
            with open(report_path, "w") as f:
                json.dump(data, f, indent=2)
            print(f"Saved to {report_path}")
        except Exception as e:
            print(f"Failed to save: {e}")

        self.root.destroy()
        raise SystemExit(0 if self.result == "PASS" else 1)

if __name__ == "__main__":
    root = tk.Tk()
    app = ScreenTestApp(root)
    root.mainloop()