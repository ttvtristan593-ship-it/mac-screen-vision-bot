import json
import socket
import time
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import os
import base64
import sys
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

# Keep OpenCV from oversubscribing CPU cores and starving the Tk UI.
cv2.setNumThreads(2)

UDP_HOST = "127.0.0.1"
UDP_PORT = 26760


class ScreenVisionBot:
    def __init__(self, root):
        self.root = root
        self.root.title("Mac Screen Vision Bot")
        self.version = self._read_version()
        try:
            icon_path = Path(__file__).resolve().parent / "assets" / "eye_icon.png"
            if not icon_path.exists() and getattr(sys, "_MEIPASS", None):
                icon_path = Path(sys._MEIPASS) / "vision_bot" / "assets" / "eye_icon.png"
            if icon_path.exists():
                self._window_icon = tk.PhotoImage(file=str(icon_path))
                self.root.iconphoto(True, self._window_icon)
        except Exception:
            pass
        self.root.geometry("1040x780")
        self.root.minsize(900, 680)
        self.preview_photo = None
        self.preview_running = True
        self.preview_target_fps = 30
        self._preview_frame_count = 0
        self._preview_fps_started = time.perf_counter()
        # Keep one MSS capture session alive; opening/closing it for every frame adds overhead.
        self._screen_capture = mss.mss()
        self._capture_monitor = self._screen_capture.monitors[0]
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

    def _read_version(self):
        candidates = [Path(__file__).resolve().parents[1] / "VERSION"]
        if getattr(sys, "_MEIPASS", None):
            candidates.insert(0, Path(sys._MEIPASS) / "VERSION")
        for candidate in candidates:
            try:
                value = candidate.read_text(encoding="utf-8").strip()
                if value:
                    return value
            except OSError:
                pass
        return "1.0.6.2"

    def _build_ui(self):
