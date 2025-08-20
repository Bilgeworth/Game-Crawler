from __future__ import annotations
import io
from pathlib import Path
from flask import Blueprint, current_app, render_template_string, redirect, url_for, flash, request, send_from_directory, send_file, abort, jsonify

from .models import Launcher
from .settings import load_settings, save_settings
from .utils import sandboxie_available, load_ignore_patterns  
from .scanning import build_games, get_game_or_404, effective_sandbox, save_meta
from .launch import run_resolved_command

from .templates import INDEX_HTML, EDIT_HTML, LAUNCH_HTML, SETTINGS_HTML

bp = Blueprint("gamecrawler", __name__)

def _cfg():
    c = current_app.config
    return (
        Path(c["GAMES_ROOT"]),
        c["APP_TITLE"],
        Path(c["SETTINGS_FILE"]),
        c["METAFILE"],
        set(c["ALLOWED_IMG_EXT"]),
        set(c["ALLOWED_EXEC_EXT"]),
        int(c["MAX_SCAN_DEPTH"]),
        float(c["DEFAULT_TARGET_AR"]),
    )

@bp.get("/")
def index():
    G, APP_TITLE, SETTINGS_FILE, METAFILE, IMG_EXTS, EXEC_EXTS, MAX_DEPTH, TARGET_AR, *_ = _cfg()
    settings = load_settings(SETTINGS_FILE)
    games = build_games(G, settings, IMG_EXTS, EXEC_EXTS, MAX_DEPTH, METAFILE, TARGET_AR)

    running_ids = set()
    for g in games:
        if (g.folder / ".gamecrawler-running.json").exists():
            running_ids.add(g.id)

    return render_template_string(
        INDEX_HTML,
        app_title=APP_TITLE,
        games=games,
        settings=settings,
        running_ids=running_ids,   # NEW
    )

@bp.get("/settings")
def settings():
    from .utils import find_sandboxie_start
    G, APP_TITLE, SETTINGS_FILE, *_ = _cfg()
    settings = load_settings(SETTINGS_FILE)
    sbie = find_sandboxie_start()

    ignore_path = G / current_app.config["IGNORE_FILE"]
    ignore_text = ""
    try:
        if ignore_path.exists():
            ignore_text = ignore_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass

    wrapper = current_app.config.get("SANDBOXED_WSL_PS1")
    
    return render_template_string(
        SETTINGS_HTML,
        app_title=APP_TITLE,
        global_default=settings["default_sandboxed"],
        sbie_path=str(sbie) if sbie else None,
        ignore_text=ignore_text,                         # NEW
        ignore_file=current_app.config["IGNORE_FILE"],   # NEW
        ps_wrapper=wrapper or None
    )

@bp.post("/settings")
def settings_post():
    G, _, SETTINGS_FILE, *_ = _cfg()
    settings = load_settings(SETTINGS_FILE)
    settings["default_sandboxed"] = bool(request.form.get("default_sandboxed"))
    save_settings(SETTINGS_FILE, settings)

    # Save .gamecrawlerignore
    ignore_text = request.form.get("ignore_text", "")
    try:
        (G / current_app.config["IGNORE_FILE"]).write_text(ignore_text, encoding="utf-8")
        flash("Settings and ignore patterns saved.")
    except Exception as e:
        flash(f"Settings saved, but failed to save ignore file: {e}")

    return redirect(url_for("gamecrawler.settings"))

@bp.get("/rescan")
def rescan():
    flash("Rescanned folders.")
    return redirect(url_for("gamecrawler.index"))

@bp.get("/edit/<game_id>")
def edit_game(game_id):
    G, APP_TITLE, SETTINGS_FILE, METAFILE, IMG, EXEC, MAX_DEPTH, TARGET_AR = _cfg()
    game = get_game_or_404(G, game_id, IMG, EXEC, MAX_DEPTH, METAFILE, TARGET_AR)
    settings = load_settings(SETTINGS_FILE)
    return render_template_string(
        EDIT_HTML,
        game=game, app_title=APP_TITLE,
        sandboxie_available=sandboxie_available(),
        global_default=settings["default_sandboxed"],
        ALLOWED_EXEC_EXT=sorted(EXEC)
    )

@bp.post("/edit/<game_id>")
def save_game(game_id):
    G, _, SETTINGS_FILE, METAFILE, IMG, EXEC, MAX_DEPTH, TARGET_AR = _cfg()
    from .scanning import load_meta
    from .scanning import new_id
    from PIL import Image
    game = get_game_or_404(G, game_id, IMG, EXEC, MAX_DEPTH, METAFILE, TARGET_AR)
    meta = game.meta

    add_exec = request.form.get("add_exec")
    remove_id = request.form.get("remove_id")
    action = request.form.get("action", "")

    title = request.form.get("title", "").strip()
    sandbox_choice = (request.form.get("sandbox_choice") or "global").strip()
    cover_choice = request.form.get("cover_choice", "").strip()
    file = request.files.get("cover_upload")

    if title:
        meta.title = title

    if sandbox_choice == "on":
        meta.sandboxed = True
    elif sandbox_choice == "off":
        meta.sandboxed = False
    else:
        meta.sandboxed = None

    # update names/args for existing launchers
    new_launchers = []
    for L in meta.launchers:
        nm = request.form.get(f"name_{L.id}", L.name).strip() or L.name
        ar = request.form.get(f"args_{L.id}", L.args).strip()
        L.name = nm
        L.args = ar
        new_launchers.append(L)

    if remove_id:
        new_launchers = [L for L in new_launchers if L.id != remove_id]
        if meta.last_launcher == remove_id:
            meta.last_launcher = None

    if add_exec and not any(L.relpath == add_exec for L in new_launchers):
        from pathlib import Path as _P
        default_name = _P(add_exec).stem
        new_launchers.append(Launcher(id=new_id(), name=default_name, relpath=add_exec, args=""))

    meta.launchers = new_launchers

    # cover upload/selection
    if file and file.filename:
        from pathlib import Path as _P
        ext = _P(file.filename).suffix.lower()
        if ext not in IMG:
            flash("Unsupported image type.")
            return redirect(url_for("gamecrawler.edit_game", game_id=game_id))
        target_name = f"cover{ext}"
        target_path = game.folder / target_name
        file.stream.seek(0)
        data = file.read()
        try:
            Image.open(io.BytesIO(data)).verify()
        except Exception:
            flash("Uploaded file is not a valid image.")
            return redirect(url_for("gamecrawler.edit_game", game_id=game_id))
        with open(target_path, "wb") as f:
            f.write(data)
        meta.cover_image = target_name
    elif cover_choice and (game.folder / cover_choice).exists():
        meta.cover_image = cover_choice

    save_meta(game.folder, METAFILE, meta)

    if add_exec or remove_id:
        return redirect(url_for("gamecrawler.edit_game", game_id=game_id))
    if action == "save_all":
        flash("Saved.")
        return redirect(url_for("gamecrawler.edit_game", game_id=game_id))

    flash("Saved.")
    return redirect(url_for("gamecrawler.index"))

@bp.get("/launch/<game_id>")
def launch_select(game_id):
    G, APP_TITLE, SETTINGS_FILE, METAFILE, IMG, EXEC, MAX_DEPTH, TARGET_AR = _cfg()
    game = get_game_or_404(G, game_id, IMG, EXEC, MAX_DEPTH, METAFILE, TARGET_AR)
    if not game.meta.launchers:
        flash("No launch options configured.")
        return redirect(url_for("gamecrawler.edit_game", game_id=game_id))
    sandbox_q = request.args.get("sandbox", "")
    return render_template_string(
        LAUNCH_HTML,
        game=game, app_title=APP_TITLE,
        sandboxie_available=sandboxie_available(),
        sandbox_q=sandbox_q
    )

@bp.post("/launch/<game_id>")
def launch_execute(game_id):
    G, _, SETTINGS_FILE, METAFILE, IMG, EXEC, MAX_DEPTH, TARGET_AR = _cfg()
    game = get_game_or_404(G, game_id, IMG, EXEC, MAX_DEPTH, METAFILE, TARGET_AR)
    launcher_id = request.form.get("launcher_id")
    mode = request.form.get("mode", "normal")
    if not launcher_id:
        flash("Pick a version to launch.")
        return redirect(url_for("gamecrawler.launch_select", game_id=game_id))

    by_id = {L.id: L for L in game.meta.launchers}
    L = by_id.get(launcher_id)
    if not L:
        flash("Invalid launcher selection.")
        return redirect(url_for("gamecrawler.launch_select", game_id=game_id))

    sandbox = (mode == "sandboxed")
    ok, msg = run_resolved_command(game, L.relpath, L.args, sandbox, default_box=current_app.config["SANDBOX_BOX"])
    if ok:
        meta = game.meta
        meta.last_launcher = L.id
        save_meta(game.folder, current_app.config["METAFILE"], meta)

    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return (jsonify({"ok": ok, ("message" if ok else "error"): msg}), 200 if ok else 500)

    flash(("Launch requested. " if ok else "Launch failed: ") + msg)
    return redirect(url_for("gamecrawler.index"))

@bp.post("/run/<game_id>")
def run_game(game_id):
    G, _, SETTINGS_FILE, METAFILE, IMG, EXEC, MAX_DEPTH, TARGET_AR = _cfg()
    from .scanning import effective_sandbox
    game = get_game_or_404(G, game_id, IMG, EXEC, MAX_DEPTH, METAFILE, TARGET_AR)
    settings = load_settings(SETTINGS_FILE)
    if game.meta.launchers:
        if len(game.meta.launchers) == 1:
            L = game.meta.launchers[0]
            sandbox = effective_sandbox(game.meta, settings["default_sandboxed"])
            ok, msg = run_resolved_command(game, L.relpath, L.args, sandbox, default_box=current_app.config["SANDBOX_BOX"])
            if ok:
                game.meta.last_launcher = L.id
                save_meta(game.folder, METAFILE, game.meta)
            flash(("Launch requested. " if ok else "Launch failed: ") + msg)
            return redirect(url_for("gamecrawler.index"))
        else:
            return redirect(url_for("gamecrawler.launch_select", game_id=game_id))
    flash("No launch options configured.")
    return redirect(url_for("gamecrawler.edit_game", game_id=game_id))

@bp.post("/run_sandboxed/<game_id>")
def run_game_sandboxed(game_id):
    return redirect(url_for("gamecrawler.launch_select", game_id=game_id, sandbox=1))

@bp.get("/cover/<game_id>")
def cover(game_id):
    G, _, _, METAFILE, IMG, EXEC, MAX_DEPTH, TARGET_AR = _cfg()
    game = get_game_or_404(G, game_id, IMG, EXEC, MAX_DEPTH, METAFILE, TARGET_AR)
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

@bp.get("/file/<game_id>/<path:filename>")
def game_file(game_id, filename):
    from .scanning import rel_for_id
    G, *_ = _cfg()
    rel = rel_for_id(game_id)
    folder = (G / rel).resolve()
    try:
        folder.relative_to(G)
    except Exception:
        abort(404)
    p = (folder / filename).resolve()
    try:
        p.relative_to(folder)
    except Exception:
        abort(404)
    if p.suffix.lower() not in current_app.config["ALLOWED_IMG_EXT"] or not p.exists():
        abort(404)
    return send_from_directory(folder, filename)

@bp.get("/favicon.ico")
def favicon():
    return ("", 204)
