#!/usr/bin/env python3
# Split-only compat shim

from __future__ import annotations
import sys
from pathlib import Path

# Ensure project root import
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gamecrawler import create_app, ensure_root
from gamecrawler.settings import load_settings, save_settings
from gamecrawler.scanning import (
    build_games,
    get_game_or_404,
    save_meta,
    load_meta,
    effective_sandbox,
)
from gamecrawler.launch import run_resolved_command
from gamecrawler.utils import sandboxie_available

def get_cfg(app_instance):
    # Flask app.config already has everything the test expects
    return app_instance.config
