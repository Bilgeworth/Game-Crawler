#!/usr/bin/env python3
import os
import sys
from gamecrawler import create_app, ensure_root, BIND, PORT

def _resolve_games_root() -> str:
    if len(sys.argv) >= 2:
        return os.path.abspath(sys.argv[1])
    return os.path.abspath(os.environ.get("GAMES_ROOT", r"D:\Games"))

if __name__ == "__main__":
    games_root = _resolve_games_root()
    ensure_root(games_root)
    app = create_app(games_root)
    app.run(host=BIND, port=PORT, debug=False)
