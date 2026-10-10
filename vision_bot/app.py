import json
import socket
import time
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import os
import base64
from pathlib import Path
from tkinter import filedialog
try:
    from vision_bot.settings import load_settings, save_settings
    from vision_bot.updater import check_for_update, download_and_install_update, UpdateError
except ImportError:
    # Supports execution as a PyInstaller entry script as well as a package.
    from settings import load_settings, save_settings
    from updater import check_for_update, download_and_install_update, UpdateError

import cv2
import mss
import numpy as np

UDP_HOST = "127.0.0.1"
UDP_PORT = 26760


class ScreenVisionBot:
    def __init__(self, root):
        self.root = root
        self.root.title("Mac Screen Vision Bot")
        self.root.geometry("1040x780")
        self.root.minsize(900, 680)
        self.preview_photo = None
        self.preview_running = True
        self.target_path = None

        self.template = None
        self.running = False
        self.output_enabled = False
        self.last_seen = 0.0
        self.last_packet = 0.0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.stop_event = threading.Event()
        # Create Tk variables before loading persisted values into them.
        self.threshold = tk.DoubleVar(value=0.72)
        self.deadzone = tk.DoubleVar(value=0.10)
        self.gain = tk.DoubleVar(value=1.25)
        self.max_output = tk.DoubleVar(value=0.65)
        self.settings = load_settings()
        self.threshold.set(self.settings.get("threshold", 0.72))
        self.deadzone.set(self.settings.get("deadzone", 0.10))
        self.gain.set(self.settings.get("gain", 1.25))
        self.max_output.set(self.settings.get("max_output", 0.65))
        self.update_status = tk.StringVar(value="Updates: not checked")
        self.status = tk.StringVar(value="Stopped — simulation mode")
        self.score_text = tk.StringVar(value="Match score: —")
        self.offset_text = tk.StringVar(value="Target offset: —")
        self.stick_text = tk.StringVar(value="Stick: x=0.00, y=0.00")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self):
        self.root.configure(bg="#202431")
        outer = tk.Frame(self.root, padx=14, pady=12, bg="#202431")
        outer.pack(fill="both", expand=True)
        brand = tk.Frame(outer, bg="#202431")
        brand.pack(anchor="w", fill="x")
        eye = tk.Canvas(brand, width=42, height=28, bg="#202431", highlightthickness=0)
        eye.pack(side="left", padx=(0, 9))
        eye.create_oval(3, 5, 39, 23, outline="#e7edf6", width=2)
        eye.create_oval(15, 7, 27, 21, outline="#e7edf6", width=1)
        eye.create_oval(18, 10, 24, 16, fill="#e7edf6", outline="#e7edf6")
        tk.Label(brand, text="MAC SCREEN VISION BOT", font=("Helvetica", 16, "bold"),
                 fg="white", bg="#202431").pack(side="left", anchor="center")
        tk.Label(outer, text="Xbox Remote Play: capture the Remote Play window on this Mac. "
                 "Preview is read-only; controller bridge support requires a separate compatible bridge.",
                 wraplength=980, fg="#dce3f0", bg="#202431", justify="left").pack(anchor="w", pady=(4, 10))

        body = tk.Frame(outer, bg="#202431")
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg="#202431", width=300)
        left.pack(side="left", fill="y", padx=(0, 12))
        right = tk.Frame(body, bg="#11151d", padx=8, pady=8)
        right.pack(side="left", fill="both", expand=True)

        buttons = tk.Frame(left, bg="#202431")
        buttons.pack(fill="x", pady=(0, 8))
        tk.Button(buttons, text="Import Target PNG", command=self.import_target).pack(fill="x", pady=3)
        tk.Button(buttons, text="Select Target from Screen", command=self.select_target).pack(fill="x", pady=3)
        self.start_btn = tk.Button(buttons, text="Start Tracking", command=self.toggle_tracking)
        self.start_btn.pack(fill="x", pady=3)
        tk.Button(buttons, text="EMERGENCY STOP", command=self.emergency_stop, fg="#b00020").pack(fill="x", pady=3)
        self.target_label = tk.Label(left, text="Target: none selected", fg="#dce3f0",
                                     bg="#202431", wraplength=280, justify="left")
        self.target_label.pack(anchor="w", pady=(4, 8))

        def add_slider(label, variable, lo, hi):
            tk.Label(left, text=label, fg="white", bg="#202431").pack(anchor="w", pady=(5, 0))
            tk.Scale(left, from_=lo, to=hi, variable=variable, orient="horizontal",
                     resolution=0.01, showvalue=True, bg="#202431", fg="white",
                     troughcolor="#536078", highlightthickness=0, length=270).pack(fill="x")
        add_slider("Match threshold", self.threshold, 0.45, 0.95)
        add_slider("Stick dead zone", self.deadzone, 0.0, 0.35)
        add_slider("Gain", self.gain, 0.2, 3.0)
        add_slider("Maximum stick magnitude", self.max_output, 0.1, 1.0)

        self.status_label = tk.Label(left, textvariable=self.status, fg="white", bg="#202431",
                                     wraplength=280, justify="left")
        self.status_label.pack(anchor="w", pady=(10, 3))
        tk.Label(left, textvariable=self.score_text, fg="#dce3f0", bg="#202431").pack(anchor="w")
        tk.Label(left, textvariable=self.offset_text, fg="#dce3f0", bg="#202431").pack(anchor="w")
        tk.Label(left, textvariable=self.stick_text, fg="#dce3f0", bg="#202431").pack(anchor="w")

        self.bridge_var = tk.BooleanVar(value=False)
        tk.Checkbutton(left, text="Enable local UDP bridge output",
                       variable=self.bridge_var, command=self.set_output,
                       fg="white", bg="#202431", selectcolor="#202431",
                       activebackground="#202431", activeforeground="white").pack(anchor="w", pady=(8, 0))
        tk.Button(left, text="Check for Updates", command=self.check_updates).pack(fill="x", pady=(8, 3))
        tk.Button(left, text="Install Available Update", command=self.install_update).pack(fill="x")
        tk.Label(left, textvariable=self.update_status, wraplength=280, justify="left",
                 fg="#dce3f0", bg="#202431").pack(anchor="w", pady=(4, 0))
        tk.Label(left, text="Version 1.0.6", font=("Helvetica", 9),
                 fg="#9aa6b8", bg="#202431").pack(anchor="w", pady=(5, 0))

        tk.Label(right, text="LIVE SCREEN PREVIEW", font=("Helvetica", 12, "bold"),
                 fg="white", bg="#11151d").pack(anchor="w")
        self.preview_label = tk.Label(right, text="Waiting for screen capture…",
                                      fg="#dce3f0", bg="#11151d")
        self.preview_label.pack(fill="both", expand=True, pady=(8, 0))
        tk.Label(right, text="The preview shows the full captured desktop. Keep Xbox Remote Play visible.",
                 fg="#aeb9cc", bg="#11151d", wraplength=650, justify="left").pack(anchor="w", pady=(8, 0))
        self.root.after(250, self.refresh_preview)

    def import_target(self):
        path = filedialog.askopenfilename(
            title="Choose target PNG",
            filetypes=[("PNG images", "*.png"), ("Image files", "*.png *.jpg *.jpeg *.bmp")])
        if not path:
            return
        image = cv2.imread(path, cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            messagebox.showerror("Could not load image", "Choose a valid PNG or other supported image.")
            return
        self.template = image
        self.capture_scale = 1.0
        self.target_path = path
        self.target_label.configure(text=f"Target image: {Path(path).name}\\nSize: {image.shape[1]} × {image.shape[0]}")
        self.status.set("Target image loaded. Start tracking when ready.")
        self.score_text.set("Match score: —")

    def refresh_preview(self):
        if not self.preview_running:
            return
        try:
            frame = self.grab_screen()
            h, w = frame.shape[:2]
            max_w, max_h = 700, 560
            scale = min(max_w / w, max_h / h, 1.0)
            if scale < 1:
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
            if ok:
                self.preview_photo = tk.PhotoImage(data=base64.b64encode(encoded.tobytes()).decode("ascii"))
                self.preview_label.configure(image=self.preview_photo, text="")
        except Exception as exc:
            self.preview_label.configure(image="", text=f"Screen preview unavailable\\n{exc}\\n\\nGrant Screen Recording permission in System Settings.")
        self.root.after(33, self.refresh_preview)

    def grab_screen(self):
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            shot = np.array(sct.grab(monitor))
            bgr = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
            return bgr

    def select_target(self):
        try:
            frame = self.grab_screen()
        except Exception as exc:
            messagebox.showerror("Screen capture failed",
                f"Could not capture the screen: {exc}\n\nCheck macOS Screen Recording permission.")
            return
        # Downscale oversized screenshots for comfortable selection; track against same-sized frames.
        h, w = frame.shape[:2]
        scale = min(1.0, 1400.0 / max(w, h))
        if scale < 1:
            frame = cv2.resize(frame, (int(w*scale), int(h*scale)), interpolation=cv2.INTER_AREA)
            self.capture_scale = scale
        else:
            self.capture_scale = 1.0
        # Use a Tkinter selection window instead of cv2.selectROI. Mixing OpenCV's
        # native window/event loop with Tk can abort the entire macOS app.
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            preview = cv2.resize(rgb, (int(w), int(h)), interpolation=cv2.INTER_AREA) if False else rgb
            max_w, max_h = 1100, 720
            display_scale = min(max_w / w, max_h / h, 1.0)
            if display_scale < 1.0:
                preview = cv2.resize(rgb, (int(w * display_scale), int(h * display_scale)),
                                     interpolation=cv2.INTER_AREA)
            else:
                preview = rgb
            ok, encoded = cv2.imencode(".png", cv2.cvtColor(preview, cv2.COLOR_RGB2BGR))
            if not ok:
                raise RuntimeError("Could not prepare screen preview")
            photo = tk.PhotoImage(data=base64.b64encode(encoded.tobytes()).decode("ascii"))
            selector = tk.Toplevel(self.root)
            selector.title("Select target — drag a rectangle, then Confirm")
            selector.transient(self.root)
            selector.grab_set()
            canvas = tk.Canvas(selector, width=photo.width(), height=photo.height(),
                               cursor="crosshair", highlightthickness=0)
            canvas.pack(padx=8, pady=8)
            canvas.create_image(0, 0, image=photo, anchor="nw")
            selector._photo = photo
            state = {"x0": None, "y0": None, "rect": None, "box": None}
            def on_down(event):
                state["x0"], state["y0"] = event.x, event.y
                if state["rect"] is not None:
                    canvas.delete(state["rect"])
                state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                        outline="#ff3333", width=2)
            def on_move(event):
                if state["rect"] is not None and state["x0"] is not None:
                    canvas.coords(state["rect"], state["x0"], state["y0"], event.x, event.y)
            def on_up(event):
                if state["x0"] is not None:
                    x0, y0 = state["x0"], state["y0"]
                    x1, y1 = event.x, event.y
                    sx, sy = sorted((x0, x1)); tx, ty = sorted((y0, y1))
                    state["box"] = (int(sx / display_scale), int(ty / display_scale),
                                    int((tx-sx) / display_scale), int((ty-sy) / display_scale))
            canvas.bind("<ButtonPress-1>", on_down)
            canvas.bind("<B1-Motion>", on_move)
            canvas.bind("<ButtonRelease-1>", on_up)
            controls = tk.Frame(selector)
            controls.pack(fill="x", padx=8, pady=(0, 8))
            tk.Label(controls, text="Drag around the target.").pack(side="left")
            def confirm():
                box = state["box"]
                if not box or box[2] < 8 or box[3] < 8:
                    messagebox.showinfo("Select a target", "Drag a rectangle around the target first.", parent=selector)
                    return
                x, y, rw, rh = box
                self.template = frame[y:y+rh, x:x+rw].copy()
                self.target_path = None
                self.target_label.configure(text=f"Target: selected from screen\\nSize: {rw} × {rh}")
                self.status.set(f"Target selected ({rw}×{rh}). Start tracking when ready.")
                self.score_text.set("Match score: —")
                selector.destroy()
            tk.Button(controls, text="Cancel", command=selector.destroy).pack(side="right")
            tk.Button(controls, text="Confirm Target", command=confirm).pack(side="right", padx=8)
            selector.protocol("WM_DELETE_WINDOW", selector.destroy)
        except Exception as exc:
            messagebox.showerror("Target selection failed", str(exc))

    def toggle_tracking(self):
        if self.running:
            self.running = False
            self.send_neutral()
            self.start_btn.configure(text="Start Tracking")
            self.status.set("Paused — output neutral")
        else:
            if self.template is None:
                messagebox.showinfo("Select a target first", "Click Capture / Select Target and drag a box around the target.")
                return
            self.running = True
            self.last_seen = time.monotonic()
            self.start_btn.configure(text="Pause Tracking")
            self.status.set("Tracking — simulation mode" if not self.output_enabled else "Tracking — bridge output enabled")
            self.root.after(30, self.tick)

    def set_output(self):
        self.output_enabled = bool(self.bridge_var.get())
        if self.output_enabled:
            self.status.set("Bridge output enabled — verify target before proceeding")
        else:
            self.send_neutral()
            self.status.set("Bridge output disabled — simulation mode")

    def tick(self):
        if not self.running:
            return
        try:
            frame = self.grab_screen()
            # The selected screenshot may have been downscaled. Resize current frames to match.
            if getattr(self, "capture_scale", 1.0) < 1.0:
                frame = cv2.resize(frame, (int(frame.shape[1]*self.capture_scale),
                                           int(frame.shape[0]*self.capture_scale)),
                                   interpolation=cv2.INTER_AREA)
            th, tw = self.template.shape[:2]
            fh, fw = frame.shape[:2]
            if th >= fh or tw >= fw:
                self.status.set("Target template larger than capture; reselect target")
                self.send_neutral()
                self.running = False
                self.start_btn.configure(text="Start Tracking")
                return
            result = cv2.matchTemplate(frame, self.template, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(result)
            self.score_text.set(f"Match score: {score:.3f}")
            now = time.monotonic()
            if score < self.threshold.get():
                if now - self.last_seen > 0.45:
                    self.send_neutral()
                    self.status.set("Target lost — neutral output")
                self.root.after(80, self.tick)
                return

            self.last_seen = now
            cx = loc[0] + tw / 2
            cy = loc[1] + th / 2
            dx = (cx - fw/2) / max(1, fw/2)
            dy = (cy - fh/2) / max(1, fh/2)
            dz = self.deadzone.get()
            x = 0.0 if abs(dx) < dz else dx * self.gain.get()
            y = 0.0 if abs(dy) < dz else dy * self.gain.get()
            cap = self.max_output.get()
            mag = max(1.0, (x*x + y*y)**0.5 / max(cap, 0.001))
            x, y = x/mag, y/mag
            x = max(-cap, min(cap, x))
            y = max(-cap, min(cap, y))
            self.offset_text.set(f"Target offset: x={dx:+.3f}, y={dy:+.3f}")
            self.stick_text.set(f"Stick: x={x:+.2f}, y={y:+.2f}")
            self.status.set("Tracking — bridge output enabled" if self.output_enabled else "Tracking — simulation only")
            self.send_stick(x, y, True)
        except Exception as exc:
            self.send_neutral()
            self.status.set(f"Capture/tracking error: {exc}")
        self.root.after(60, self.tick)

    def send_stick(self, x, y, active):
        if not self.output_enabled:
            return
        now = time.monotonic()
        if now - self.last_packet < 1/30:
            return
        self.last_packet = now
        packet = {"type": "stick", "x": round(float(x), 4),
                  "y": round(float(y), 4), "active": bool(active)}
        try:
            self.sock.sendto(json.dumps(packet).encode("utf-8"), (UDP_HOST, UDP_PORT))
        except OSError:
            self.output_enabled = False
            self.bridge_var.set(False)
            self.status.set("Bridge send failed — output disabled")

    def send_neutral(self):
        if self.output_enabled:
            try:
                self.sock.sendto(json.dumps({"type":"stick","x":0.0,"y":0.0,"active":False}).encode("utf-8"),
                                 (UDP_HOST, UDP_PORT))
            except OSError:
                pass
        self.stick_text.set("Stick: x=0.00, y=0.00")

    def emergency_stop(self):
        self.running = False
        self.output_enabled = False
        self.bridge_var.set(False)
        self.send_neutral()
        self.start_btn.configure(text="Start Tracking")
        self.status.set("EMERGENCY STOP — all output disabled")

    def check_updates(self):
        self.update_status.set("Updates: checking GitHub Releases…")
        def worker():
            try:
                info = check_for_update()
                if info is None:
                    message = "Updates: already on the latest version, or GitHub repository not configured."
                else:
                    self.pending_update = info
                    message = f"Updates: version {info['version']} available. Review release notes before installing."
            except Exception as exc:
                message = f"Updates: check failed — {exc}"
            self.root.after(0, lambda: self.update_status.set(message))
        threading.Thread(target=worker, daemon=True).start()

    def install_update(self):
        info = getattr(self, "pending_update", None)
        if not info:
            messagebox.showinfo("No update selected", "Check for updates first. Configure GITHUB_REPOSITORY in updater.py.")
            return
        if self.running or self.output_enabled:
            messagebox.showwarning("Stop tracking first", "Pause tracking and disable controller output before updating.")
            return
        if not messagebox.askyesno("Install update?", f"Install version {info['version']}?\n\nThe installer will verify the new app, preserve a rollback copy, then close this app and relaunch the updated version."):
            return
        self.update_status.set("Updates: downloading, validating, and installing…")
        def worker():
            try:
                result = download_and_install_update(info)
                if result.get("mode") == "app_bundle":
                    # The detached installer waits for this process to exit.
                    # Closing the Tk app is required for the installer to proceed.
                    self.root.after(250, self.close)
                    return
                message = f"Updates: source files updated. Backup: {result.get('backup', 'created')}."
            except Exception as exc:
                message = f"Updates: installation could not start — {exc}"
            self.root.after(0, lambda: self.update_status.set(message))
        threading.Thread(target=worker, daemon=True).start()

    def close(self):
        self.emergency_stop()
        try:
            save_settings({
                "threshold": float(self.threshold.get()),
                "deadzone": float(self.deadzone.get()),
                "gain": float(self.gain.get()),
                "max_output": float(self.max_output.get()),
            })
        except Exception:
            pass
        self.stop_event.set()
        try:
            self.sock.close()
        except Exception:
            pass
        cv2.destroyAllWindows()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ScreenVisionBot(root)
        root.mainloop()
    except Exception as exc:
        import traceback
        details = traceback.format_exc()
        try:
            messagebox.showerror("Mac Screen Vision Bot startup error", f"{exc}\\n\\n{details}")
        except Exception:
            print(details)
        raise


if __name__ == "__main__":
    main()
