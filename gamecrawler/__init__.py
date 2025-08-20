import os
from flask import Flask
from .routes import bp as routes_bp

# Bind only localhost unless overridden (preserves current behavior)
BIND = os.environ.get("BIND", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))
SANDBOX_BOX = os.environ.get("SANDBOX_BOX", "DefaultBox")
SANDBOXED_WSL_PS1 = os.environ.get("SANDBOXED_WSL_PS1")

def ensure_root(games_root: str) -> None:
    if not os.path.isdir(games_root):
        raise SystemExit(f"GAMES_ROOT does not exist: {games_root}")

def create_app(games_root: str) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET", "dev-" + os.urandom(8).hex())
    app.config["GAMES_ROOT"] = games_root
    app.config["APP_TITLE"] = "Portable Game Shelf (Flask)"
    app.config["SETTINGS_FILE"] = os.path.join(games_root, "_gamecrawler.json")
    app.config["METAFILE"] = "game.json"
    app.config["ALLOWED_IMG_EXT"] = {".png", ".jpg", ".jpeg", ".webp"}
    app.config["ALLOWED_EXEC_EXT"] = {".exe", ".bat", ".cmd", ".com", ".sh", ".py"}
    app.config["DEFAULT_TARGET_AR"] = 0.75
    app.config["MAX_SCAN_DEPTH"] = 3
    app.config["SANDBOX_BOX"] = SANDBOX_BOX
    app.config["IGNORE_FILE"] = ".gamecrawlerignore"
    app.config["SANDBOXED_WSL_PS1"] = SANDBOXED_WSL_PS1

    app.register_blueprint(routes_bp)
    return app
