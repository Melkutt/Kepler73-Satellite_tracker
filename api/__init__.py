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
    # Build absolute paths from the project root location
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    frontend_dir = os.path.join(project_root, "frontend")

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