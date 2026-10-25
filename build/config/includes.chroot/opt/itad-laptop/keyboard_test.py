#!/usr/bin/env python3
import tkinter as tk
from tkinter import messagebox

# Define the keyboard layout (rows of keys)
KEYBOARD_LAYOUT = [
    ["Esc", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"],
    ["`", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=", "BackSpace"],
    ["Tab", "q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "[", "]", "\\"],
    ["CapsLock", "a", "s", "d", "f", "g", "h", "j", "k", "l", ";", "'", "Return"],
    ["Shift_L", "z", "x", "c", "v", "b", "n", "m", ",", ".", "/", "Shift_R"],
    ["Control_L", "Alt_L", "space", "Alt_R", "Control_R"]
]

class KeyboardTestApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ITAD Keyboard Test")
        self.root.configure(bg="#1e1e1e")
        self.root.attributes("-fullscreen", True) # Make it full screen

        # Title
        title = tk.Label(root, text="KEYBOARD TEST", font=("Helvetica", 24, "bold"), 
                         bg="#1e1e1e", fg="#38bdf8")
        title.pack(pady=10)

        subtitle = tk.Label(root, text="Press every key. Press twice to confirm (Yellow -> Green).", 
                            font=("Helvetica", 14), bg="#1e1e1e", fg="#cccccc")
        subtitle.pack(pady=5)

        # Progress
        self.total_keys = sum(len(row) for row in KEYBOARD_LAYOUT)
        self.tested_keys = set()
        
        self.progress_var = tk.StringVar()
        self.progress_var.set(f"Progress: 0 / {self.total_keys}")
        progress_label = tk.Label(root, textvariable=self.progress_var, 
                                  font=("Helvetica", 16), bg="#1e1e1e", fg="#fbbf24")
        progress_label.pack(pady=10)

        # Keyboard Frame
        kb_frame = tk.Frame(root, bg="#1e1e1e")
        kb_frame.pack(expand=True)

        self.buttons = {}
        self.key_states = {} # Tracks if key is unpressed (0), yellow (1), or green (2)

        for r, row in enumerate(KEYBOARD_LAYOUT):
            for c, key in enumerate(row):
                btn = tk.Button(kb_frame, text=key, width=4, height=2, 
                                bg="#333333", fg="white", font=("Helvetica", 10, "bold"),
                                relief="raised", takefocus=0) # takefocus=0 prevents stealing Space/Enter
                
                # Adjust widths for special keys
                if key == "space": btn.config(width=20)
                elif key in ["Shift_L", "Shift_R", "BackSpace", "CapsLock", "Return"]: btn.config(width=8)
                elif key in ["Tab", "Control_L", "Alt_L", "Alt_R", "Control_R"]: btn.config(width=6)
                
                btn.grid(row=r, column=c, padx=3, pady=3, sticky="nsew")
                self.buttons[key] = btn
                self.key_states[key] = 0

        # Bottom Buttons
        btn_frame = tk.Frame(root, bg="#1e1e1e")
        btn_frame.pack(pady=20)

        tk.Button(btn_frame, text="FINISH TEST", font=("Helvetica", 14, "bold"),
                  bg="#22c55e", fg="white", command=self.finish_test, takefocus=0).pack(side=tk.LEFT, padx=20)
        
        tk.Button(btn_frame, text="FAIL / CANCEL", font=("Helvetica", 14, "bold"),
                  bg="#ef4444", fg="white", command=self.cancel_test, takefocus=0).pack(side=tk.LEFT, padx=20)

        # Bind keypress events
        self.root.bind("<KeyPress>", self.key_press)
        self.root.focus_force()

    def key_press(self, event):
        key = event.keysym
        
        # Standardize key names to match our layout
        if key == "Escape": key = "Esc"
        elif key == "Return": key = "Return"
        elif key == "BackSpace": key = "BackSpace"
        elif key == "space": key = "space"
        
        # Handle uppercase letters (if Shift or CapsLock is on)
        if len(key) == 1 and key.isupper():
            key = key.lower()
            if "Shift_L" in self.buttons: self.cycle_key("Shift_L")

        # If key exists in layout, cycle its state
        if key in self.buttons:
            self.cycle_key(key)

    def cycle_key(self, key):
        state = self.key_states[key]
        btn = self.buttons[key]
        
        if state == 0:
            # Unpressed -> Yellow (Pressed)
            btn.config(bg="#eab308", activebackground="#eab308")
            self.key_states[key] = 1
        elif state == 1:
            # Yellow -> Green (Confirmed)
            btn.config(bg="#22c55e", activebackground="#22c55e")
            self.key_states[key] = 2
            self.tested_keys.add(key)
            self.update_progress()
        elif state == 2:
            # Reset back to Gray (if they want to test again)
            btn.config(bg="#333333", activebackground="#333333")
            self.key_states[key] = 0
            self.tested_keys.discard(key)
            self.update_progress()

    def update_progress(self):
        count = len(self.tested_keys)
        self.progress_var.set(f"Progress: {count} / {self.total_keys}")

    def finish_test(self):
        print("KEYBOARD_TEST: PASS")
        self.root.destroy()
        raise SystemExit(0)

    def cancel_test(self):
        print("KEYBOARD_TEST: FAIL")
        self.root.destroy()
        raise SystemExit(1)

if __name__ == "__main__":
    root = tk.Tk()
    app = KeyboardTestApp(root)
    root.mainloop()