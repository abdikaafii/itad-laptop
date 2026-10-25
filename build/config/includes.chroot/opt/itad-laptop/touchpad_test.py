#!/usr/bin/env python3
import tkinter as tk
import os
import json

class TouchpadTestApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Touchpad Test")
        self.root.configure(bg="#1e1e1e")
        self.root.attributes("-fullscreen", True)

        tk.Label(root, text="TOUCHPAD TEST", font=("Helvetica", 24, "bold"), bg="#1e1e1e", fg="#38bdf8").pack(pady=10)
        tk.Label(root, text="Move your finger around. Draw a circle to test.", font=("Helvetica", 14), bg="#1e1e1e", fg="white").pack()

        self.canvas = tk.Canvas(root, bg="#000000", highlightthickness=0, cursor="cross")
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        self.canvas.bind("<B1-Motion>", self.draw)
        self.canvas.bind("<Motion>", self.draw) # Also draw when just moving finger without clicking

        btn_frame = tk.Frame(root, bg="#1e1e1e")
        btn_frame.pack(pady=10)

        tk.Button(btn_frame, text="CLEAR", font=("Helvetica", 14), bg="#3b82f6", fg="white",
                  command=self.clear_canvas, takefocus=0).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="PASS", font=("Helvetica", 16, "bold"), bg="#22c55e", fg="white", width=12,
                  command=lambda: self.finish("PASS"), takefocus=0).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="FAIL", font=("Helvetica", 16, "bold"), bg="#ef4444", fg="white", width=12,
                  command=lambda: self.finish("FAIL"), takefocus=0).pack(side=tk.LEFT, padx=10)

        self.root.bind("<Escape>", lambda e: self.finish("FAIL"))

    def draw(self, event):
        x, y = event.x, event.y
        r = 4
        self.canvas.create_oval(x-r, y-r, x+r, y+r, fill="#ef4444", outline="")

    def clear_canvas(self):
        self.canvas.delete("all")

    def finish(self, result):
        report_dir = os.environ.get("ITAD_DATA", "/tmp/itad-reports")
        os.makedirs(report_dir, exist_ok=True)
        with open(os.path.join(report_dir, "touchpad_test.json"), "w") as f:
            json.dump({"test": "touchpad", "result": result}, f, indent=2)
        self.root.destroy()
        raise SystemExit(0 if result == "PASS" else 1)

if __name__ == "__main__":
    root = tk.Tk()
    app = TouchpadTestApp(root)
    root.mainloop()