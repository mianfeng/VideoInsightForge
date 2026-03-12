"""
Desktop launcher for the VideoInsightForge web GUI.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn

try:
    import webview
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "pywebview is required for the desktop GUI. Install dependencies with "
        "`pip install -r requirements.txt`."
    ) from exc

import tkinter as tk
from tkinter import filedialog


_HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(_HERE))
os.chdir(_HERE)

APP_URL = "http://127.0.0.1:8732"

VIDEO_FILETYPES = [
    ("Video files", "*.mp4 *.avi *.mkv *.mov *.flv *.wmv *.webm *.m4v"),
    ("All files", "*.*"),
]
AUDIO_FILETYPES = [
    ("Audio files", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma *.aiff *.alac"),
    ("All files", "*.*"),
]


class DesktopBridge:
    def __init__(self):
        self._dialog_lock = threading.Lock()

    def choose_media(self, kind: str):
        filetypes = AUDIO_FILETYPES if kind == "audio" else VIDEO_FILETYPES
        with self._dialog_lock:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                path = filedialog.askopenfilename(
                    title="Select audio file" if kind == "audio" else "Select video file",
                    filetypes=filetypes,
                )
            finally:
                root.destroy()
        return {"path": path or ""}

    def open_output(self):
        output_dir = _HERE / "output"
        output_dir.mkdir(exist_ok=True)
        subprocess.Popen(["explorer", str(output_dir)])
        return {"ok": True}

    def capabilities(self):
        return {"desktop": True}


def _wait_for_server(url: str, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1.5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.25)
    return False


def main():
    config = uvicorn.Config("server:app", host="127.0.0.1", port=8732, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not _wait_for_server(APP_URL):
        server.should_exit = True
        raise SystemExit("Failed to start local UI server on http://127.0.0.1:8732")

    bridge = DesktopBridge()
    window = webview.create_window(
        "VideoInsightForge",
        APP_URL,
        js_api=bridge,
        width=1520,
        height=920,
        min_size=(1180, 760),
        background_color="#f5efe6",
    )

    try:
        webview.start(debug=False)
    finally:
        server.should_exit = True
        if window:
            time.sleep(0.2)
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
