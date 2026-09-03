# -*- coding: utf-8 -*-
"""
Kepler73 - main.py
Starts Flask backend and opens the browser automatically.
"""

import threading
import webbrowser
import time
import sys
import os

# Ensure project directory is in Python path
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# Change working directory to project root
os.chdir(PROJECT_DIR)

from api import create_app, socketio
from backend.config import APP_VERSION, AUTHOR

HOST = "127.0.0.1"
PORT = 5000
URL  = f"http://{HOST}:{PORT}"


def open_browser():
    """Waits for Flask to start, then opens the browser."""
    time.sleep(1.5)
    print(f"[Kepler73] Opening browser -> {URL}")
    webbrowser.open(URL)


if __name__ == "__main__":
    print("=" * 50)
    print(f"  Kepler73 v{APP_VERSION}  -  {AUTHOR}")
    print(f"  Starting at {URL}")
    print("=" * 50)

    # Open browser in background thread
    threading.Thread(target=open_browser, daemon=True).start()

    # Start Flask + SocketIO
    socketio.run(
        create_app(),
        host=HOST,
        port=PORT,
        debug=False,
        use_reloader=False,
        log_output=False,
        allow_unsafe_werkzeug=True
)