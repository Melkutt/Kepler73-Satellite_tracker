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

# When running from source, make sure the project root is importable and is the
# working directory. In a PyInstaller bundle the modules are frozen in and the
# writable data dir is an absolute path (~/.kepler73), so neither is needed –
# and chdir'ing into the temp _MEIPASS dir would just be confusing.
if not getattr(sys, "frozen", False):
    PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
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