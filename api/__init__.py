# -*- coding: utf-8 -*-
"""
Kepler73 – API package
Flask app factory + SocketIO init
"""

import secrets

from flask import Flask
from flask_socketio import SocketIO

socketio = SocketIO(cors_allowed_origins="*", async_mode="threading")


def create_app():
    import os
    # BASE_DIR is the project root when run from source, or the PyInstaller
    # bundle dir (sys._MEIPASS) when frozen – frontend/ is bundled alongside.
    from backend.config import BASE_DIR
    frontend_dir = os.path.join(BASE_DIR, "frontend")

    app = Flask(
        __name__,
        static_folder=frontend_dir,
        static_url_path="",          # serves /css/..., /js/... directly
        template_folder=frontend_dir
    )
    # Random per run – this is a single-user localhost app, nothing needs to
    # keep a session across restarts.
    app.config["SECRET_KEY"] = secrets.token_hex(32)

    # Register routes
    from api.routes import bp
    app.register_blueprint(bp)

    # Start SocketIO
    socketio.init_app(app)

    # Start background thread for live positions
    from api.sockets import start_position_broadcast
    start_position_broadcast(socketio)

    return app