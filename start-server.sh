#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export GAMES_ROOT="$PARENT_DIR/Games"
mkdir -p "$GAMES_ROOT"

# Optional venv auto-activate
if [[ -f "$SCRIPT_DIR/.venv/Scripts/activate" ]]; then
  # Git Bash on Windows
  source "$SCRIPT_DIR/.venv/Scripts/activate"
elif [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
  source "$SCRIPT_DIR/.venv/bin/activate"
fi

python3 -m pip install -r "$SCRIPT_DIR/requirements.txt"

export FLASK_SECRET="prod-$RANDOM$RANDOM"
export BIND="127.0.0.1"
export PORT="5000"

python3 "$SCRIPT_DIR/app.py" "$GAMES_ROOT"
