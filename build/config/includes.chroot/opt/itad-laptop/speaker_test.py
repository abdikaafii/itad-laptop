#!/usr/bin/env python3
import tkinter as tk
import subprocess
import os
import json

class SpeakerTestApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Speaker Test")
        self.root.configure(bg="#1e1e1e")
        self.root.attributes("-fullscreen", True)

        tk.Label(root, text="SPEAKER TEST", font=("Helvetica", 24, "bold"), bg="#1e1e1e", fg="#38bdf8").pack(pady=30)
        tk.Label(root, text="Press PLAY to test the left and right speakers.", font=("Helvetica", 14), bg="#1e1e1e", fg="white").pack(pady=10)

        tk.Button(root, text="▶ PLAY TEST TONE", font=("Helvetica", 18, "bold"), bg="#3b82f6", fg="white", 
                  command=self.play_sound, takefocus=0).pack(pady=40)

        btn_frame = tk.Frame(root, bg="#1e1e1e")
        btn_frame.pack(pady=20)

        tk.Button(btn_frame, text="PASS", font=("Helvetica", 16, "bold"), bg="#22c55e", fg="white", width=15,
                  command=lambda: self.finish("PASS"), takefocus=0).pack(side=tk.LEFT, padx=20)
        tk.Button(btn_frame, text="FAIL", font=("Helvetica", 16, "bold"), bg="#ef4444", fg="white", width=15,
                  command=lambda: self.finish("FAIL"), takefocus=0).pack(side=tk.LEFT, padx=20)

        self.root.bind("<Escape>", lambda e: self.finish("FAIL"))

    def play_sound(self):
        # Plays a 440Hz tone for 2 seconds on the front-left and front-right channels
        subprocess.Popen(["speaker-test", "-t", "sine", "-f", "440", "-l", "1", "-c", "2"], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def finish(self, result):
        report_dir = os.environ.get("ITAD_DATA", "/tmp/itad-reports")
        os.makedirs(report_dir, exist_ok=True)
        with open(os.path.join(report_dir, "speaker_test.json"), "w") as f:
            json.dump({"test": "speaker", "result": result}, f, indent=2)
        self.root.destroy()
        raise SystemExit(0 if result == "PASS" else 1)

if __name__ == "__main__":
    root = tk.Tk()
    app = SpeakerTestApp(root)
    root.mainloop()