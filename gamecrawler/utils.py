import base64
import io
import os
import shlex
import fnmatch
from pathlib import Path
from typing import Optional, List
from PIL import Image

def b64url_encode(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

def b64url_decode(s: str) -> str:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode()).decode()

def is_windows() -> bool:
    return os.name == "nt"

def wsl_available() -> bool:
    if not is_windows():
        return False
    sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return (sys32 / "wsl.exe").exists()

def find_git_bash() -> Optional[str]:
    if not is_windows():
        return None
    for c in [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
    ]:
        if Path(c).exists():
            return c
    return None

def find_sandboxie_start() -> Optional[Path]:
    env = os.environ.get("SANDBOXIE_START")
    if env and Path(env).exists():
        return Path(env)
    p = Path(r"C:\Program Files\Sandboxie-Plus\Start.exe")
    return p if p.exists() else None

def sandboxie_available() -> bool:
    return is_windows() and find_sandboxie_start() is not None

def pick_best_image(game_dir: Path, candidates: List[str], target_ar: float) -> Optional[str]:
    best = None
    best_score = float("inf")
    best_area = -1
    for name in candidates:
        f = game_dir / name
        try:
            with Image.open(f) as im:
                w, h = im.size
                if w <= 0 or h <= 0:
                    continue
                ar = w / h
                score = abs(ar - target_ar)
                area = w * h
                if score < best_score or (abs(score - best_score) < 1e-6 and area > best_area):
                    best, best_score, best_area = name, score, area
        except Exception:
            continue
    return best

def find_sandboxed_wsl_script() -> Optional[Path]:  # NEW
    env = os.environ.get("SANDBOXED_WSL_PS1")
    if env and Path(env).exists():
        return Path(env)
    return None

# --- ignore patterns (gitignore-ish) ---

def load_ignore_patterns(root: Path, ignore_filename: str) -> List[str]:
    p = root / ignore_filename
    if not p.exists():
        return []
    raw = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    patterns = []
    for line in raw:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        patterns.append(s.replace("\\", "/"))
    return patterns

def _match_any(path_rel: str, patterns: List[str]) -> bool:
    """Basic gitignore-like matching with !negations and dir patterns.

    - 'GameB/' matches 'GameB' and everything under it
    - 'foo' matches 'foo' and 'foo/...'
    - '!keepme/' re-include a previously ignored path
    - Globs allowed via fnmatch
    """
    pr = path_rel.replace("\\", "/").lstrip("/")
    decided: bool | None = None  # last matching rule wins

    for raw in patterns:
        neg = raw.startswith("!")
        pat = raw[1:] if neg else raw
        pat = pat.lstrip("/")

        if pat.endswith("/"):
            base = pat[:-1]
            hit = (pr == base) or pr.startswith(base + "/")
        else:
            hit = (pr == pat) or pr.startswith(pat + "/") or fnmatch.fnmatch(pr, pat)

        if hit:
            decided = (not neg)

    return bool(decided)

def is_dir_ignored(root: Path, dir_path: Path, patterns: List[str]) -> bool:
    rel = str(dir_path.relative_to(root))
    return _match_any(rel, patterns)