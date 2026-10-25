#!/usr/bin/env python3
import tkinter as tk
import subprocess
import os
import json

class MicTestApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Microphone Test")
        self.root.configure(bg="#1e1e1e")
        self.root.attributes("-fullscreen", True)

        tk.Label(root, text="MICROPHONE TEST", font=("Helvetica", 24, "bold"), bg="#1e1e1e", fg="#38bdf8").pack(pady=30)
        tk.Label(root, text="Press RECORD, speak for 3 seconds, then press PLAY.", font=("Helvetica", 14), bg="#1e1e1e", fg="white").pack(pady=10)

        self.status = tk.Label(root, text="", font=("Helvetica", 14, "bold"), bg="#1e1e1e", fg="#fbbf24")
        self.status.pack(pady=10)

        tk.Button(root, text="🔴 RECORD (3s)", font=("Helvetica", 18, "bold"), bg="#ef4444", fg="white", 
                  command=self.record_audio, takefocus=0).pack(pady=20)
                  
        tk.Button(root, text="🔊 PLAY RECORDING", font=("Helvetica", 18, "bold"), bg="#3b82f6", fg="white", 
                  command=self.play_audio, takefocus=0).pack(pady=20)

        btn_frame = tk.Frame(root, bg="#1e1e1e")
        btn_frame.pack(pady=20)

        tk.Button(btn_frame, text="PASS", font=("Helvetica", 16, "bold"), bg="#22c55e", fg="white", width=15,
                  command=lambda: self.finish("PASS"), takefocus=0).pack(side=tk.LEFT, padx=20)
        tk.Button(btn_frame, text="FAIL", font=("Helvetica", 16, "bold"), bg="#ef4444", fg="white", width=15,
                  command=lambda: self.finish("FAIL"), takefocus=0).pack(side=tk.LEFT, padx=20)

        self.root.bind("<Escape>", lambda e: self.finish("FAIL"))

    def record_audio(self):
        self.status.config(text="Recording...", fg="#ef4444")
        self.root.update()
        # Records 3 seconds of audio to /tmp/mic_test.wav
        subprocess.run(["arecord", "-d", "3", "-f", "cd", "/tmp/mic_test.wav"], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.status.config(text="Recording saved.", fg="#22c55e")

    def play_audio(self):
        if os.path.exists("/tmp/mic_test.wav"):
            self.status.config(text="Playing...", fg="#3b82f6")
            self.root.update()
            subprocess.run(["aplay", "/tmp/mic_test.wav"], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.status.config(text="Playback finished.", fg="#22c55e")
        else:
            self.status.config(text="No recording found!", fg="#ef4444")

    def finish(self, result):
        report_dir = os.environ.get("ITAD_DATA", "/tmp/itad-reports")
        os.makedirs(report_dir, exist_ok=True)
        with open(os.path.join(report_dir, "mic_test.json"), "w") as f:
            json.dump({"test": "microphone", "result": result}, f, indent=2)
        self.root.destroy()
        raise SystemExit(0 if result == "PASS" else 1)

if __name__ == "__main__":
    root = tk.Tk()
    app = MicTestApp(root)
    root.mainloop()