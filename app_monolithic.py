### Legacy file kept around for reference

import base64
import io
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Tuple
from collections import deque

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template_string,
    request, send_file, send_from_directory, url_for
)
from PIL import Image

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
if len(sys.argv) >= 2:
    GAMES_ROOT = Path(sys.argv[1]).resolve()
else:
    GAMES_ROOT = Path(os.environ.get("GAMES_ROOT", os.getcwd())).resolve()

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = GAMES_ROOT / "_gamecrawler.json"  # in Games root
METAFILE = "game.json"

ALLOWED_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_EXEC_EXT = {
    ".exe", ".bat", ".cmd", ".com", ".sh", ".py",
    ".x86_64", ".x86", ".appimage", ".bin", ".run",
}
LINUX_EXEC_EXT = {".x86_64", ".x86", ".appimage", ".bin", ".run"}
DEFAULT_TARGET_AR = 0.75
APP_TITLE = "Game Crawler"

# scanning depth for subfolders inside each game directory
MAX_SCAN_DEPTH = 3  # root + 2 levels

# Bind only localhost unless overridden
BIND = os.environ.get("BIND", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))
SANDBOX_BOX = os.environ.get("SANDBOX_BOX", "DefaultBox")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-" + os.urandom(8).hex())

# ──────────────────────────────────────────────────────────────────────────────
# Models / Settings
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class Launcher:
    id: str         # stable id for this option
    name: str       # user visible name (editable)
    relpath: str    # relative path from game folder (e.g. "Subdir/game.exe")
    args: str = ""  # optional args

@dataclass
class GameMeta:
    title: str
    cover_image: str                 # relative filename
    sandboxed: Optional[bool]        # None = use global default; True/False = override
    launchers: List[Launcher]
    last_launcher: Optional[str]     # id of last used launcher (for default selection)
    # Back-compat: old single-command field. If present and no launchers, seed from it.
    command: str = ""

@dataclass
class Game:
    folder: Path
    rel: str
    id: str
    meta: GameMeta
    detected_execs: List[str]    # relative paths discovered recursively
    detected_images: List[str]   # images at root only

def load_settings() -> dict:
    """
    Global settings file stored in GAMES_ROOT/_gamecrawler.json
    Default: default_sandboxed = True
    """
    default = {"default_sandboxed": True}
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text("utf-8"))
            default.update({k: data.get(k, default[k]) for k in default})
    except Exception:
        pass
    return default

def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")

# ──────────────────────────────────────────────────────────────────────────────
# Utils
# ──────────────────────────────────────────────────────────────────────────────
def b64url_encode(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

def b64url_decode(s: str) -> str:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode()).decode()

def game_id_for(rel: str) -> str:
    return b64url_encode(rel)

def rel_for_id(gid: str) -> str:
    return b64url_decode(gid)

def is_windows() -> bool:
    return os.name == "nt"

def wsl_available() -> bool:
    if not is_windows():
        return False
    sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return (sys32 / "wsl.exe").exists()

def find_git_bash() -> Optional[str]:
    if not is_windows(): return None
    for c in [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
    ]:
        if Path(c).exists(): return c
    return None

def find_sandboxie_start() -> Optional[Path]:
    env = os.environ.get("SANDBOXIE_START")
    if env and Path(env).exists():
        return Path(env)
    default_path = Path(r"C:\Program Files\Sandboxie-Plus\Start.exe")
    if default_path.exists():
        return default_path
    return None

def sandboxie_available() -> bool:
    return is_windows() and find_sandboxie_start() is not None

def detect_root_files(game_dir: Path, exts: set) -> List[str]:
    items: List[str] = []
    try:
        for p in game_dir.iterdir():
            if p.is_file() and p.suffix.lower() in exts:
                items.append(p.name)
    except PermissionError:
        pass
    return sorted(items, key=lambda n: n.lower())

def detect_files_bfs(game_dir: Path, exts: set, max_depth: int = MAX_SCAN_DEPTH) -> List[str]:
    """
    Return relative file paths from game_dir by breadth-first search.
    As soon as we hit the first executable depth, only search directories at that depth.
    """
    results: List[str] = []
    q = deque([(game_dir, 0)])
    found_depth: Optional[int] = None

    while q:
        cur, depth = q.popleft()
        if depth > max_depth:
            continue

        try:
            entries = list(cur.iterdir())
        except PermissionError:
            continue

        files_here = [e for e in entries if e.is_file() and e.suffix.lower() in exts]
        if files_here:
            if found_depth is None:
                found_depth = depth
            if depth == found_depth:
                for f in files_here:
                    rel = f.relative_to(game_dir).as_posix()
                    results.append(rel)
            # don’t enqueue deeper dirs once we’ve locked depth
            continue

        # only enqueue subdirs if we haven’t yet found execs
        if found_depth is None:
            for d in entries:
                if d.is_dir():
                    q.append((d, depth + 1))

    results.sort(key=lambda s: s.lower())
    return results

def new_id() -> str:
    return os.urandom(6).hex()

def load_meta(game_dir: Path, default_title: str) -> GameMeta:
    meta_path = game_dir / METAFILE
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text("utf-8"))
            # tri-state sandbox
            sb = data.get("sandboxed", None)
            if sb in ("", "global"): sb_val = None
            elif isinstance(sb, bool): sb_val = sb
            else: sb_val = None
            # launchers
            l_raw = data.get("launchers", [])
            launchers: List[Launcher] = []
            for item in l_raw:
                if not isinstance(item, dict): continue
                lid = item.get("id") or new_id()
                name = item.get("name") or "Launch"
                relp = item.get("relpath") or ""
                args = item.get("args") or ""
                if relp:
                    launchers.append(Launcher(id=lid, name=name, relpath=relp, args=args))
            last = data.get("last_launcher")
            cmd = data.get("command", "")  # back-compat
            m = GameMeta(
                title=data.get("title", default_title),
                cover_image=data.get("cover_image", ""),
                sandboxed=sb_val,
                launchers=launchers,
                last_launcher=last,
                command=cmd
            )
            # seed from old single-command if needed
            if not m.launchers and m.command:
                base = Path(m.command).name
                m.launchers = [Launcher(id=new_id(), name=Path(base).stem, relpath=m.command, args="")]
                m.command = ""
            return m
        except Exception:
            pass
    # default
    return GameMeta(
        title=default_title, cover_image="", sandboxed=None,
        launchers=[], last_launcher=None, command=""
    )

def save_meta(game_dir: Path, meta: GameMeta) -> None:
    data = asdict(meta)
    (game_dir / METAFILE).write_text(json.dumps(data, indent=2), encoding="utf-8")

def pick_best_image(game_dir: Path, candidates: List[str]) -> Optional[str]:
    best = None
    best_score = float("inf")
    best_area = -1
    for name in candidates:
        f = game_dir / name
        try:
            with Image.open(f) as im:
                w, h = im.size
                if w <= 0 or h <= 0: continue
                ar = w / h
                score = abs(ar - DEFAULT_TARGET_AR)
                area = w * h
                if score < best_score or (abs(score - best_score) < 1e-6 and area > best_area):
                    best, best_score, best_area = name, score, area
        except Exception:
            continue
    return best

def effective_sandbox(game_meta: GameMeta, global_default: bool) -> bool:
    return game_meta.sandboxed if game_meta.sandboxed is not None else global_default

def build_games(global_default: bool) -> List[Game]:
    games: List[Game] = []
    if not GAMES_ROOT.exists(): return games
    for p in sorted(GAMES_ROOT.iterdir()):
        if not p.is_dir(): continue
        rel = str(p.relative_to(GAMES_ROOT))
        meta = load_meta(p, default_title=p.name)
        images = detect_root_files(p, ALLOWED_IMG_EXT)
        execs = detect_files_bfs(p, ALLOWED_EXEC_EXT, MAX_SCAN_DEPTH)
        if not meta.cover_image and images:
            chosen = pick_best_image(p, images)
            if chosen:
                meta.cover_image = chosen
                save_meta(p, meta)
        games.append(Game(
            folder=p, rel=rel, id=game_id_for(rel),
            meta=meta, detected_execs=execs, detected_images=images
        ))
    return games

def get_game_or_404(game_id: str) -> Game:
    rel = rel_for_id(game_id)
    folder = (GAMES_ROOT / rel).resolve()
    try:
        folder.relative_to(GAMES_ROOT)
    except ValueError:
        abort(404)
    if not folder.exists() or not folder.is_dir():
        abort(404)

    meta = load_meta(folder, default_title=folder.name)
    images = detect_root_files(folder, ALLOWED_IMG_EXT)
    execs = detect_files_bfs(folder, ALLOWED_EXEC_EXT, MAX_SCAN_DEPTH)
    if not meta.cover_image and images:
        chosen = pick_best_image(folder, images)
        if chosen:
            meta.cover_image = chosen
            save_meta(folder, meta)

    return Game(
        folder=folder,
        rel=str(folder.relative_to(GAMES_ROOT)),
        id=game_id_for(str(folder.relative_to(GAMES_ROOT))),
        meta=meta,
        detected_execs=execs,
        detected_images=images
    )

# ──────────────────────────────────────────────────────────────────────────────
# Launchers
# ──────────────────────────────────────────────────────────────────────────────
def run_resolved_command(game: Game, relpath: str, args: str, sandbox: bool) -> Tuple[bool, str]:
    """Compose a command string and delegate to the appropriate runner."""
    # Build a command: quoted relpath + args (args left as-is for user's control)
    cmd = f"\"{relpath}\"{(' ' + args.strip()) if args.strip() else ''}".strip()
    if sandbox:
        return run_command_sandboxed(game, cmd)
    return run_command(game, cmd)

def run_command(game: Game, cmd: str) -> Tuple[bool, str]:
    cwd = str(game.folder)
    try:
        tokens = shlex.split(cmd, posix=not is_windows())
        if tokens:
            first = tokens[0].strip('"')
            full = (game.folder / first)
            if full.exists():
                tokens[0] = str(full)

        if is_windows() and tokens and tokens[0].lower().endswith(".sh"):
            if wsl_available():
                win_cmd = " ".join(shlex.quote(t) for t in tokens)
                wsl_cmd = f'cd "{cwd}" && {win_cmd}'
                subprocess.Popen(["wsl.exe", "bash", "-lc", wsl_cmd])
                return True, "Launched via WSL."
            git_bash = find_git_bash()
            if git_bash:
                subprocess.Popen([git_bash, "-lc", f'cd "{cwd}" && ' + " ".join(shlex.quote(t) for t in tokens)])
                return True, "Launched via Git Bash."
            return False, "No WSL or Git Bash found to run .sh."

        if is_windows():
            subprocess.Popen(" ".join(tokens), cwd=cwd, shell=True)
        else:
            subprocess.Popen(tokens, cwd=cwd)
        return True, "Launched."
    except Exception as e:
        return False, str(e)

def run_command_sandboxed(game: Game, cmd: str, box: str = SANDBOX_BOX) -> Tuple[bool, str]:
    if not is_windows():
        return False, "Sandboxed launch requires Windows + Sandboxie."
    start_exe = find_sandboxie_start()
    if not start_exe:
        return False, (
            "Sandboxie Plus not found. Please install it to "
            "C:\\Program Files\\Sandboxie-Plus\\ or set SANDBOXIE_START "
            "to your custom Start.exe location."
        )
    cwd = str(game.folder)
    try:
        tokens = shlex.split(cmd, posix=False)
        if tokens:
            first = tokens[0].strip('"')
            full = (game.folder / first)
            if full.exists():
                tokens[0] = str(full)
        args = [str(start_exe), f"/box:{box}"] + tokens
        subprocess.Popen(args, cwd=cwd, shell=False)
        return True, f"Launched sandboxed in box '{box}'."
    except Exception as e:
        return False, str(e)

# ──────────────────────────────────────────────────────────────────────────────
# Templates
# ──────────────────────────────────────────────────────────────────────────────
INDEX_HTML = r"""<!doctype html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <title>{{ app_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    .card { border: 1px solid rgba(255,255,255,.08); }
    .game-cover { width: 100%; height: 260px; object-fit: cover; border-radius: .5rem .5rem 0 0; background:#222; }
    .title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .path { color: rgba(255,255,255,.6); }
    .unsupported .game-cover { filter: grayscale(1) brightness(.7); }
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg bg-body-tertiary px-3">
  <a class="navbar-brand" href="#">{{ app_title }}</a>
  <div class="ms-auto d-flex gap-2">
    <a class="btn btn-outline-light btn-sm" href="{{ url_for('settings') }}">Settings</a>
    <a class="btn btn-outline-light btn-sm" href="{{ url_for('rescan') }}">Rescan</a>
  </div>
</nav>

<div class="container py-4">
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-warning">{{ messages|join('. ') }}</div>
    {% endif %}
  {% endwith %}

  <div class="mb-3 small">
    Global default: <strong>{{ 'Sandboxed' if global_default else 'Not sandboxed' }}</strong>
    {% if not sandboxie_available %}
      <span class="text-warning ms-2">[Sandboxie not found]</span>
    {% endif %}
  </div>

  {% if not games %}
    <div class="text-center py-5">
      <h4>No games found in <code>{{ root }}</code>.</h4>
      <p class="text-secondary">Add one folder per game. Put a cover image and your .exe / .sh in the folder (subfolders OK).</p>
    </div>
  {% else %}
  <div class="row row-cols-1 row-cols-sm-2 row-cols-md-3 row-cols-xl-5 g-4">
    {% for g in games %}
      {% set has_exec = (g.detected_execs|length) > 0 %}
      {% set eff_sb = (g.meta.sandboxed if g.meta.sandboxed is not none else global_default) %}
      <div class="col">
        <div class="card h-100 shadow-sm {% if not has_exec %}unsupported{% endif %}">
          <img class="game-cover" src="{{ url_for('cover', game_id=g.id) }}" alt="cover">
          <div class="card-body d-flex flex-column">
            <div class="title fw-semibold" title="{{ g.meta.title }}">{{ g.meta.title }}</div>
            <div class="small path mt-1">{{ g.rel }}</div>

            <div class="mt-2 d-flex flex-wrap gap-2">
              {% if not has_exec and (g.meta.launchers|length) == 0 %}
                <a class="btn btn-outline-light btn-sm" href="{{ url_for('edit_game', game_id=g.id) }}">Edit</a>
                <span class="badge text-bg-secondary">No executable</span>
              {% else %}
                <a class="btn btn-success btn-sm" href="{{ url_for('launch_select', game_id=g.id) }}">Run</a>
                <a class="btn btn-outline-warning btn-sm {% if not sandboxie_available %}disabled{% endif %}"
                   {% if sandboxie_available %}href="{{ url_for('launch_select', game_id=g.id) }}?sandbox=1"{% endif %}
                   {% if not sandboxie_available %}tabindex="-1" aria-disabled="true"{% endif %}>
                   Run Sandboxed
                </a>
                <a class="btn btn-outline-light btn-sm" href="{{ url_for('edit_game', game_id=g.id) }}">Edit</a>
              {% endif %}
              {% if eff_sb %}
                <span class="badge text-bg-warning align-self-center">
                  {{ 'Sandboxed' if g.meta.sandboxed is not none else 'Sandboxed (global)' }}
                </span>
              {% endif %}
            </div>
          </div>
        </div>
      </div>
    {% endfor %}
  </div>
  {% endif %}
</div>
</body>
</html>
"""

EDIT_HTML = r"""<!doctype html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <title>Edit {{ game.meta.title }} — {{ app_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    .card { border: 1px solid rgba(255,255,255,.08); }
    .cover-preview { width: 260px; height: 260px; object-fit: cover; border-radius: .5rem; background:#222; }
    .path { color: rgba(255,255,255,.6); }
    code.path { word-break: break-all; }
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg bg-body-tertiary px-3">
  <a class="navbar-brand" href="{{ url_for('index') }}">{{ app_title }}</a>
  <div class="ms-auto">
    <a class="btn btn-outline-light btn-sm" href="{{ url_for('settings') }}">Settings</a>
  </div>
</nav>

<div class="container py-4">
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-warning">{{ messages|join('. ') }}</div>
    {% endif %}
  {% endwith %}

  <div class="row g-4">
    <div class="col-md-4">
      <img id="coverPreview" class="cover-preview" src="{{ url_for('cover', game_id=game.id) }}" alt="cover">
      <div class="small path mt-2">{{ game.rel }}</div>
    </div>
    <div class="col-md-8">
      <div class="card p-3">
        <form id="editForm" action="{{ url_for('edit_game', game_id=game.id) }}" method="post" enctype="multipart/form-data">
          <div class="mb-3">
            <label class="form-label">Title</label>
            <input class="form-control" type="text" name="title" value="{{ game.meta.title }}" required>
          </div>

          <div class="mb-3">
            <label class="form-label">Sandboxing</label>
            <div class="form-check">
              <input class="form-check-input" type="radio" name="sandbox_choice" id="sb_global" value="global" {% if game.meta.sandboxed is none %}checked{% endif %}>
              <label class="form-check-label" for="sb_global">Use global default</label>
            </div>
            <div class="form-check">
              <input class="form-check-input" type="radio" name="sandbox_choice" id="sb_on" value="on" {% if game.meta.sandboxed is true %}checked{% endif %}>
              <label class="form-check-label" for="sb_on">Always sandbox this game</label>
            </div>
            <div class="form-check">
              <input class="form-check-input" type="radio" name="sandbox_choice" id="sb_off" value="off" {% if game.meta.sandboxed is false %}checked{% endif %}>
              <label class="form-check-label" for="sb_off">Never sandbox this game</label>
            </div>
          </div>

          <hr class="my-3">

          <h6>Configured launch options</h6>
          {% if game.meta.launchers %}
            <div class="table-responsive mb-3">
              <table class="table table-dark table-sm align-middle">
                <thead><tr><th style="width:22%">Name</th><th>Relative path</th><th style="width:26%">Args</th><th style="width:10%"></th></tr></thead>
                <tbody>
                {% for L in game.meta.launchers %}
                  <tr>
                    <td><input class="form-control form-control-sm" type="text" name="name_{{ L.id }}" value="{{ L.name }}" required></td>
                    <td><code class="path">{{ L.relpath }}</code></td>
                    <td><input class="form-control form-control-sm" type="text" name="args_{{ L.id }}" value="{{ L.args }}"></td>
                    <td>
                      <button class="btn btn-outline-danger btn-sm" name="remove_id" value="{{ L.id }}" type="submit">Remove</button>
                    </td>
                  </tr>
                {% endfor %}
                </tbody>
              </table>
            </div>
          {% else %}
            <div class="alert alert-info">No launch options configured yet. Add from detected executables below.</div>
          {% endif %}

          <div class="mb-2">
            <button class="btn btn-primary" type="submit" name="action" value="save_all">Save</button>
            <a class="btn btn-secondary" href="{{ url_for('index') }}">Back</a>
            {% if game.meta.launchers|length > 0 %}
              <a class="btn btn-success" href="{{ url_for('launch_select', game_id=game.id) }}">Run…</a>
            {% endif %}
          </div>

          <hr class="my-3">

          <h6>Detected executables (subfolders included)</h6>
          {% if game.detected_execs %}
            <div class="list-group">
              {% for p in game.detected_execs %}
                <div class="list-group-item d-flex justify-content-between align-items-center">
                  <code class="path">{{ p }}</code>
                  <button class="btn btn-outline-info btn-sm" name="add_exec" value="{{ p }}">Add</button>
                </div>
              {% endfor %}
            </div>
          {% else %}
            <div class="alert alert-secondary">No executables found. Supported: {{ ALLOWED_EXEC_EXT|join(', ') }}</div>
          {% endif %}

          <hr class="my-3">

          <div class="mb-3">
            <label class="form-label">Cover image</label>
            <div class="row g-2">
              {% for img in game.detected_images %}
              <div class="col-6 col-md-4">
                <div class="form-check">
                  <input class="form-check-input cover-radio" type="radio" name="cover_choice" id="img_{{ loop.index }}" value="{{ img }}" {% if img == game.meta.cover_image %}checked{% endif %}>
                  <label class="form-check-label" for="img_{{ loop.index }}">{{ img }}</label>
                </div>
              </div>
              {% endfor %}
            </div>
            <div class="form-text">Or upload (png/jpg/webp). It will be saved in this game’s folder.</div>
            <input id="coverUpload" class="form-control mt-2" type="file" name="cover_upload" accept=".png,.jpg,.jpeg,.webp">
          </div>
        </form>
      </div>
    </div>
  </div>
</div>

<script>
  // Live cover preview: radio selection -> exact image
  document.querySelectorAll('.cover-radio').forEach(r => {
    r.addEventListener('change', () => {
      const imgName = r.value;
      const preview = document.getElementById('coverPreview');
      preview.src = "{{ url_for('game_file', game_id=game.id, filename='__REPLACE__') }}"
        .replace('__REPLACE__', encodeURIComponent(imgName))
        + "?_cb=" + Date.now();
    });
  });

  // Live cover preview: file upload
  const up = document.getElementById('coverUpload');
  if (up) {
    up.addEventListener('change', () => {
      const file = up.files && up.files[0];
      if (!file) return;
      document.getElementById('coverPreview').src = URL.createObjectURL(file);
    });
  }
</script>

</body>
</html>
"""

LAUNCH_HTML = r"""<!doctype html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <title>Launch {{ game.meta.title }} — {{ app_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
<nav class="navbar navbar-expand-lg bg-body-tertiary px-3">
  <a class="navbar-brand" href="{{ url_for('index') }}">{{ app_title }}</a>
</nav>

<div class="container py-4">
  <h5 class="mb-3">{{ game.meta.title }}</h5>
  {% if not game.meta.launchers %}
    <div class="alert alert-info">No launch options configured. <a href="{{ url_for('edit_game', game_id=game.id) }}">Add some</a>.</div>
  {% else %}
    <form class="card p-3" action="{{ url_for('launch_execute', game_id=game.id) }}" method="post">
      <div class="mb-3">
        {% for L in game.meta.launchers %}
          <div class="form-check">
            <input class="form-check-input" type="radio" name="launcher_id" id="L{{ L.id }}" value="{{ L.id }}"
              {% if (game.meta.last_launcher and game.meta.last_launcher == L.id) or (not game.meta.last_launcher and loop.first) %}checked{% endif %}>
            <label class="form-check-label" for="L{{ L.id }}">
              <strong>{{ L.name }}</strong> <span class="text-secondary"> — {{ L.relpath }}{% if L.args %} {{ L.args }}{% endif %}</span>
            </label>
          </div>
        {% endfor %}
      </div>
      <div class="d-flex gap-2">
        <button class="btn btn-success" type="submit" name="mode" value="normal">Launch</button>
        <button class="btn btn-outline-warning" type="submit" name="mode" value="sandboxed" {% if not sandboxie_available %}disabled{% endif %}>Launch Sandboxed</button>
        <a class="btn btn-secondary ms-auto" href="{{ url_for('index') }}">Cancel</a>
      </div>
    </form>
  {% endif %}
</div>
</body>
</html>
"""

SETTINGS_HTML = r"""<!doctype html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <title>Settings — {{ app_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
<nav class="navbar navbar-expand-lg bg-body-tertiary px-3">
  <a class="navbar-brand" href="{{ url_for('index') }}">{{ app_title }}</a>
</nav>

<div class="container py-4">
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-warning">{{ messages|join('. ') }}</div>
    {% endif %}
  {% endwith %}

  <form action="{{ url_for('settings') }}" method="post" class="card p-3">
    <h5 class="mb-3">Global Settings</h5>

    <div class="form-check form-switch mb-3">
      <input class="form-check-input" type="checkbox" role="switch" id="defSandbox" name="default_sandboxed" {% if global_default %}checked{% endif %}>
      <label class="form-check-label" for="defSandbox">Default to sandboxed</label>
      <div class="form-text">Applies to games set to “Use Global”. Per-game overrides take precedence.</div>
    </div>

    <div class="mb-3">
      <label class="form-label">Sandboxie Start.exe</label>
      {% if sbie_path %}
        <div class="alert alert-success py-2 mb-2">Found at: <code>{{ sbie_path }}</code></div>
      {% else %}
        <div class="alert alert-warning py-2 mb-2">
          Sandboxie Plus not found. Install to <code>C:\Program Files\Sandboxie-Plus\</code>
          or set the <code>SANDBOXIE_START</code> environment variable to your custom <code>Start.exe</code> location.
        </div>
      {% endif %}
    </div>

    <div class="d-flex gap-2">
      <button class="btn btn-primary" type="submit">Save</button>
      <a class="btn btn-secondary" href="{{ url_for('index') }}">Back</a>
    </div>
  </form>
</div>
</body>
</html>
"""

# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/")
def index():
    settings = load_settings()
    games = build_games(settings["default_sandboxed"])
    return render_template_string(
        INDEX_HTML,
        games=games, root=str(GAMES_ROOT), app_title=APP_TITLE,
        sandboxie_available=sandboxie_available(),
        global_default=settings["default_sandboxed"]
    )

@app.get("/settings")
def settings():
    settings = load_settings()
    sbie = find_sandboxie_start()
    return render_template_string(
        SETTINGS_HTML,
        app_title=APP_TITLE,
        global_default=settings["default_sandboxed"],
        sbie_path=str(sbie) if sbie else None
    )

@app.post("/settings")
def settings_post():
    settings = load_settings()
    settings["default_sandboxed"] = bool(request.form.get("default_sandboxed"))
    save_settings(settings)
    flash("Settings saved.")
    return redirect(url_for("settings"))

@app.get("/rescan")
def rescan():
    flash("Rescanned folders.")
    return redirect(url_for("index"))

@app.get("/edit/<game_id>")
def edit_game(game_id):
    game = get_game_or_404(game_id)
    settings = load_settings()
    return render_template_string(
        EDIT_HTML,
        game=game, app_title=APP_TITLE,
        sandboxie_available=sandboxie_available(),
        global_default=settings["default_sandboxed"],
        ALLOWED_EXEC_EXT=sorted(ALLOWED_EXEC_EXT)
    )

@app.post("/edit/<game_id>")
def save_game(game_id):
    game = get_game_or_404(game_id)
    meta = game.meta

    # Handle add/remove launcher first (these are discrete actions)
    add_exec = request.form.get("add_exec")
    remove_id = request.form.get("remove_id")
    action = request.form.get("action", "")

    title = request.form.get("title", "").strip()
    sandbox_choice = (request.form.get("sandbox_choice") or "global").strip()
    cover_choice = request.form.get("cover_choice", "").strip()
    file = request.files.get("cover_upload")

    if title:
        meta.title = title

    # Tri-state sandbox
    if sandbox_choice == "on":
        meta.sandboxed = True
    elif sandbox_choice == "off":
        meta.sandboxed = False
    else:
        meta.sandboxed = None

    # Mutations: add/remove/update launchers
    # Update names/args for existing launchers on any post-back
    new_launchers: List[Launcher] = []
    existing_by_id = {L.id: L for L in meta.launchers}
    for L in meta.launchers:
        nm = request.form.get(f"name_{L.id}", L.name).strip() or L.name
        ar = request.form.get(f"args_{L.id}", L.args).strip()
        L.name = nm
        L.args = ar
        new_launchers.append(L)

    # Remove?
    if remove_id:
        new_launchers = [L for L in new_launchers if L.id != remove_id]
        if meta.last_launcher == remove_id:
            meta.last_launcher = None

    # Add from detected item?
    if add_exec:
        # Avoid duplicates by relpath; allow same relpath twice only if user wants (but generally avoid)
        exists = any(L.relpath == add_exec for L in new_launchers)
        if not exists:
            default_name = Path(add_exec).stem
            new_launchers.append(Launcher(id=new_id(), name=default_name, relpath=add_exec, args=""))

    meta.launchers = new_launchers

    # Uploaded image takes precedence
    if file and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_IMG_EXT:
            flash("Unsupported image type.")
            return redirect(url_for("edit_game", game_id=game.id))
        target_name = f"cover{ext}"
        target_path = game.folder / target_name
        file.stream.seek(0)
        data = file.read()
        try:
            Image.open(io.BytesIO(data)).verify()
        except Exception:
            flash("Uploaded file is not a valid image.")
            return redirect(url_for("edit_game", game_id=game.id))
        with open(target_path, "wb") as f:
            f.write(data)
        meta.cover_image = target_name
    elif cover_choice and (game.folder / cover_choice).exists():
        meta.cover_image = cover_choice

    save_meta(game.folder, meta)

    # Decide where to go after different actions
    if add_exec or remove_id:
        return redirect(url_for("edit_game", game_id=game.id))
    if action == "save_all":
        flash("Saved.")
        return redirect(url_for("edit_game", game_id=game.id))

    flash("Saved.")
    return redirect(url_for("index"))

@app.get("/launch/<game_id>")
def launch_select(game_id):
    game = get_game_or_404(game_id)
    if not game.meta.launchers:
        flash("No launch options configured.")
        return redirect(url_for("edit_game", game_id=game.id))
    sandbox_q = request.args.get("sandbox", "")
    return render_template_string(
        LAUNCH_HTML,
        game=game, app_title=APP_TITLE,
        sandboxie_available=sandboxie_available(),
        sandbox_q=sandbox_q
    )

@app.post("/launch/<game_id>")
def launch_execute(game_id):
    game = get_game_or_404(game_id)
    launcher_id = request.form.get("launcher_id")
    mode = request.form.get("mode", "normal")
    if not launcher_id:
        flash("Pick a version to launch.")
        return redirect(url_for("launch_select", game_id=game.id))

    # Find launcher
    by_id = {L.id: L for L in game.meta.launchers}
    L = by_id.get(launcher_id)
    if not L:
        flash("Invalid launcher selection.")
        return redirect(url_for("launch_select", game_id=game.id))

    # Determine sandbox (explicit button trumps defaults)
    sandbox = (mode == "sandboxed")

    ok, msg = run_resolved_command(game, L.relpath, L.args, sandbox)
    if ok:
        meta = game.meta
        meta.last_launcher = L.id
        save_meta(game.folder, meta)

    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return (jsonify({"ok": ok, "message" if ok else "error": msg}), 200 if ok else 500)

    flash(("Launch requested. " if ok else "Launch failed: ") + msg)
    return redirect(url_for("index"))

@app.post("/run/<game_id>")
def run_game(game_id):
    # Legacy route: if multiple options exist, send to picker; if one, launch it
    settings = load_settings()
    game = get_game_or_404(game_id)
    if game.meta.launchers:
        if len(game.meta.launchers) == 1:
            L = game.meta.launchers[0]
            sandbox = effective_sandbox(game.meta, settings["default_sandboxed"])
            ok, msg = run_resolved_command(game, L.relpath, L.args, sandbox)
            if ok:
                game.meta.last_launcher = L.id
                save_meta(game.folder, game.meta)
            flash(("Launch requested. " if ok else "Launch failed: ") + msg)
            return redirect(url_for("index"))
        else:
            return redirect(url_for("launch_select", game_id=game.id))
    # no configured launchers
    flash("No launch options configured.")
    return redirect(url_for("edit_game", game_id=game.id))

@app.post("/run_sandboxed/<game_id>")
def run_game_sandboxed(game_id):
    # Legacy route -> redirect to picker with sandbox=1
    return redirect(url_for("launch_select", game_id=game_id, sandbox=1))

@app.get("/cover/<game_id>")
def cover(game_id):
    game = get_game_or_404(game_id)
    if game.meta.cover_image:
        p = game.folder / game.meta.cover_image
        if p.exists():
            return send_from_directory(game.folder, game.meta.cover_image)
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="600" height="600">
      <rect width="100%" height="100%" fill="#1f2630"/>
      <text x="50%" y="50%" fill="#e0e6ee" font-size="28" text-anchor="middle" dominant-baseline="middle">
        {game.meta.title[:32]}
      </text>
    </svg>
    """
    return send_file(io.BytesIO(svg.encode("utf-8")), mimetype="image/svg+xml")

@app.get("/file/<game_id>/<path:filename>")
def game_file(game_id, filename):
    game = get_game_or_404(game_id)
    p = (game.folder / filename).resolve()
    try:
        p.relative_to(game.folder)
    except ValueError:
        abort(404)
    if p.suffix.lower() not in ALLOWED_IMG_EXT or not p.exists():
        abort(404)
    return send_from_directory(game.folder, filename)

@app.get("/favicon.ico")
def favicon():
    return ("", 204)

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def ensure_root():
    if not GAMES_ROOT.exists():
        raise SystemExit(f"GAMES_ROOT does not exist: {GAMES_ROOT}")

if __name__ == "__main__":
    ensure_root()
    app.run(host=BIND, port=PORT, debug=False)
