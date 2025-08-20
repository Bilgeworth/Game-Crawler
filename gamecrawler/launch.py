# gamecrawler/launch.py
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import json
import time
from pathlib import Path
from typing import Tuple, List, Union, Optional, Dict

from .utils import (
    is_windows,
    wsl_available,
    find_git_bash,
    find_sandboxie_start,
    find_sandboxed_wsl_script,
)

# ──────────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────────

def _strip_outer_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s

def _is_shell_script(token: str) -> bool:
    t = _strip_outer_quotes(token or "")
    try:
        return Path(t).suffix.lower() == ".sh"
    except Exception:
        return t.lower().endswith(".sh")

def _materialize_first_token(tokens: List[str], cwd: str) -> List[str]:
    """If the first token is a file next to cwd, expand it to an absolute path (Windows-friendly)."""
    if tokens:
        first = _strip_outer_quotes(tokens[0])
        full = os.path.join(cwd, first)
        if os.path.exists(full):
            tokens[0] = full
    return tokens

def _mark_running(folder: Path) -> Path:
    m = folder / ".gamecrawler-running.json"
    payload = {"started": time.time()}
    try:
        m.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass
    return m

def _spawn_and_track(
    argv: Union[str, List[str]],
    cwd: str,
    *,
    shell: bool,
    folder: Path,
    env: Optional[Dict[str, str]] = None
) -> Tuple[bool, str]:
    """
    Spawn the process and (when possible) create a marker file in the game folder.
    If the returned object supports .wait(), we watch it and clear the marker on exit.
    """
    try:
        p = subprocess.Popen(argv, cwd=cwd, shell=shell, env=env)
    except Exception as e:
        return False, str(e)

    # Only track if the object looks like a real Popen (has .wait())
    if hasattr(p, "wait"):
        marker = _mark_running(folder)

        def _wait():
            try:
                p.wait()
            finally:
                try:
                    if marker.exists():
                        marker.unlink()
                    (folder / ".gamecrawler-last-exit.json").write_text(
                        json.dumps({"ended": time.time(), "exit": getattr(p, "returncode", None)}),
                        encoding="utf-8"
                    )
                except Exception:
                    pass

        threading.Thread(target=_wait, daemon=True).start()

    return True, "Launched."

# ──────────────────────────────────────────────────────────────────────────────
# PowerShell WSL wrapper (env-only) and fallbacks
# ──────────────────────────────────────────────────────────────────────────────

def _call_sandboxed_wsl(cwd: str, tokens: List[str], folder: Path) -> Tuple[bool, str]:
    """
    Invoke user-provided PowerShell wrapper to run a .sh via WSL.
    We pass NO parameters – only environment variables:
      SANDBOXED_WSL_CWD = <cwd>
      SANDBOXED_WSL_CMD = <command line, quoted>
    """
    script = find_sandboxed_wsl_script()
    if not script:
        return False, "SANDBOXED_WSL_PS1 not set or script not found."

    cmdline = " ".join(shlex.quote(t) for t in tokens)
    env = os.environ.copy()
    env["SANDBOXED_WSL_CWD"] = cwd
    env["SANDBOXED_WSL_CMD"] = cmdline

    ps_argv = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(script)
    ]
    return _spawn_and_track(ps_argv, cwd=cwd, shell=False, folder=Path(folder), env=env)

def _call_wsl(cwd: str, tokens: List[str], folder: Path) -> Tuple[bool, str]:
    """Fallback: plain WSL bash -lc with wslpath-translated CWD."""
    if not wsl_available():
        return False, "WSL not available."
    rel_cmd = " ".join(shlex.quote(t) for t in tokens)
    wsl_cmd = f'cd "$(wslpath -a "{cwd}")" && {rel_cmd}'
    argv = ["wsl.exe", "bash", "-lc", wsl_cmd]
    return _spawn_and_track(argv, cwd=cwd, shell=False, folder=Path(folder))

def _call_git_bash(cwd: str, tokens: List[str], folder: Path) -> Tuple[bool, str]:
    """Last-resort: Git Bash (may not always handle Windows paths)."""
    git_bash = find_git_bash()
    if not git_bash:
        return False, "Git Bash not found."
    bash_cmd = f'cd "{cwd}" && ' + " ".join(shlex.quote(t) for t in tokens)
    argv = [git_bash, "-lc", bash_cmd]
    return _spawn_and_track(argv, cwd=cwd, shell=False, folder=Path(folder))

# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def run_command(game_folder: Union[Path, str], cmd: str) -> Tuple[bool, str]:
    """
    Generic launcher for a command string (no Sandboxie wrapping here).
    - On Windows, .sh is routed to PS wrapper -> WSL -> Git Bash (in that order).
    - Non-.sh or non-Windows: spawn directly (shell=True on Windows for .exe convenience).
    """
    folder = Path(game_folder)
    cwd = str(folder)

    try:
        tokens = shlex.split(cmd, posix=not is_windows())

        # Handle .sh on Windows BEFORE materializing to absolute Windows paths
        if is_windows() and tokens and _is_shell_script(tokens[0]):
            # dequote first token and ensure ./ prefix for bare script names
            tokens[0] = _strip_outer_quotes(tokens[0])
            if ("/" not in tokens[0]) and (not tokens[0].startswith("./")) and (not tokens[0].startswith("/")):
                tokens[0] = f"./{tokens[0]}"

            ok, msg = _call_sandboxed_wsl(cwd, tokens, folder)
            if ok:
                return ok, msg

            ok, msg = _call_wsl(cwd, tokens, folder)
            if ok:
                return ok, msg

            ok, msg = _call_git_bash(cwd, tokens, folder)
            if ok:
                return ok, msg

            return False, "No WSL/Git Bash found to run .sh."

        # Non-.sh or non-Windows: OK to materialize first token now
        tokens = _materialize_first_token(tokens, cwd)

        if is_windows():
            # shell=True allows launching .exe/.bat with args as a single string
            return _spawn_and_track(" ".join(tokens), cwd=cwd, shell=True, folder=folder)
        else:
            return _spawn_and_track(tokens, cwd=cwd, shell=False, folder=folder)

    except Exception as e:
        return False, str(e)

def run_resolved_command(
    game,
    relpath: str,
    args: str,
    sandbox: bool,
    default_box: str,
) -> Tuple[bool, str]:
    """
    Compose and launch a command for a given game entry.

    - If `sandbox` is True on Windows and Sandboxie Start.exe is present, we wrap the launch.
    - For `.sh` on Windows, we ignore `sandbox` (the PS wrapper+WSL path is used).
    - On non-Windows, we just exec the tokens (no Sandboxie).
    """
    folder = Path(game.folder)
    # Build tokens: keep relpath as given so our .sh logic can decide the right pathing
    tokens: List[str] = []
    rel = relpath.strip().strip('"')
    if rel:
        tokens.append(rel)
    if args:
        tokens.extend(shlex.split(args, posix=True))

    # Windows + .sh: route via run_command() which already does wrapper/WSL/Git Bash
    if is_windows() and tokens and _is_shell_script(tokens[0]):
        return run_command(folder, " ".join(tokens))

    # Windows + Sandboxie:
    if is_windows() and sandbox:
        sbie = find_sandboxie_start()
        if sbie and Path(sbie).exists():
            tokens2 = _materialize_first_token(tokens[:], str(folder))
            # Start.exe [/box:Name] [/wait] [/silent] program [args...]
            argv: List[str] = [str(sbie), f"/box:{default_box}", "/wait", "/silent"] + tokens2
            return _spawn_and_track(argv, cwd=str(folder), shell=False, folder=folder)
        # If Sandboxie not found, fall through to normal spawn

    # Everyone else: plain run
    return run_command(folder, " ".join(tokens))
